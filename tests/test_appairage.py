"""Connecter un appareil par un lien à usage unique (brief appairage, 2026-11-15).
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import glob
import hmac
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import test_server
from test_server import HOST, cert, req, srv  # noqa: F401
from herdr_web import appairage as ap

ICI = Path(__file__).parent
QRJS = ICI.parent / "static" / "vendor" / "qrcode" / "qrcode.js"


class Horloge:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------- le module seul ----------

def test_code_a_usage_unique():
    a = ap.Appairage(Horloge())
    code, duree = a.creer()
    assert duree == 300 and len(code) >= 32
    assert a.consommer(code) is True
    assert a.consommer(code) is False  # réutilisé : refusé


def test_code_expire_refuse():
    h = Horloge()
    a = ap.Appairage(h)
    code, _ = a.creer()
    h.t += 300  # échéance atteinte
    assert a.consommer(code) is False
    assert a.en_attente() == 0


def test_code_juste_avant_l_echeance_accepte():
    h = Horloge()
    a = ap.Appairage(h)
    code, _ = a.creer()
    h.t += 299
    assert a.consommer(code) is True


@pytest.mark.parametrize("faux", ["", "x", "A" * 32, "../../etc", "a b", "é" * 32, None, 12])
def test_code_faux_ou_mal_forme_refuse(faux):
    a = ap.Appairage(Horloge())
    a.creer()
    assert a.consommer(faux) is False
    assert a.en_attente() == 1  # un faux code ne retire pas le vrai


def test_codes_aleatoires_et_bornes_en_nombre():
    a = ap.Appairage(Horloge())
    codes = [a.creer()[0] for _ in range(ap.MAX_CODES + 1)]
    assert len(set(codes)) == len(codes)
    assert a.en_attente() == ap.MAX_CODES
    assert a.consommer(codes[0]) is False  # le plus ancien est oublié
    assert a.consommer(codes[-1]) is True


def test_chaque_code_en_attente_compare_en_temps_constant(monkeypatch):
    """Pas de sortie anticipée : le bon code trouvé en tête, les suivants sont comparés quand même,
    et par hmac.compare_digest (jamais ==)."""
    a = ap.Appairage(Horloge())
    codes = [a.creer()[0] for _ in range(3)]
    vrai = hmac.compare_digest
    appels = []
    monkeypatch.setattr(ap.hmac, "compare_digest", lambda x, y: appels.append(1) or vrai(x, y))
    assert a.consommer(codes[0]) is True
    assert len(appels) == 3


def test_essais_bornes_par_adresse_et_fenetre_glissante():
    h = Horloge()
    a = ap.Appairage(h)
    assert all(a.essai_permis("100.64.0.9") for _ in range(ap.ESSAIS))
    assert a.essai_permis("100.64.0.9") is False  # 11e dans la minute
    assert a.essai_permis("100.64.0.10") is True  # une autre adresse n'est pas touchée
    h.t += ap.FENETRE
    assert a.essai_permis("100.64.0.9") is True


def test_adresses_suivies_bornees(monkeypatch):
    monkeypatch.setattr(ap, "MAX_ADRESSES", 4)
    a = ap.Appairage(Horloge())
    for i in range(10):
        assert a.essai_permis(f"10.0.0.{i}")
    assert len(a._essais) <= 4


# ---------- les routes ----------

def nouveau_lien(s):
    r, b = req(s, "POST", "/api/appairage", {})
    assert r.status == 200, b
    return json.loads(b)


def test_creation_sans_cookie_401(srv):
    r, _ = req(srv, "POST", "/api/appairage", {}, cookie=False)
    assert r.status == 401
    assert srv.appairage.en_attente() == 0


def test_creation_sans_origine_403(srv):
    r, _ = req(srv, "POST", "/api/appairage", {}, origin=False)
    assert r.status == 403
    assert srv.appairage.en_attente() == 0


def test_creation_en_get_refusee(srv):
    r, _ = req(srv, "GET", "/api/appairage")
    assert r.status == 404 and srv.appairage.en_attente() == 0


def test_lien_porte_l_hote_et_jamais_le_jeton(srv):
    d = nouveau_lien(srv)
    u = urlsplit(d["lien"])
    assert u.scheme == "https" and u.netloc == HOST and u.path.startswith("/appairer/")
    assert d["expire_s"] == 300
    assert test_server.TOKEN not in d["lien"]
    assert test_server.TOKEN not in json.dumps(d)


def test_lien_pose_le_cookie_de_login_puis_renvoie_a_la_page(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    r, _ = req(srv, "GET", chemin, cookie=False)
    assert r.status == 303 and r.getheader("Location") == "/"
    c = r.getheader("Set-Cookie")
    login, _ = req(srv, "POST", "/login", {"token": test_server.TOKEN}, cookie=False)
    assert c == login.getheader("Set-Cookie")  # mêmes attributs que /login, même valeur
    assert c.startswith(f"hw={test_server.TOKEN};")
    assert r.getheader("Referrer-Policy") == "no-referrer"
    assert r.getheader("Cache-Control") == "no-store"


def test_lien_deja_utilise_page_expiree(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    assert req(srv, "GET", chemin, cookie=False)[0].status == 303
    r, b = req(srv, "GET", chemin, cookie=False)
    assert r.status == 410 and r.getheader("Set-Cookie") is None
    assert r.getheader("Content-Type").startswith("text/html")
    assert "Link expired" in b.decode()  # anglais : la langue par défaut


def test_lien_expire_page_expiree(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    h = Horloge()
    h.t = srv.appairage.horloge() + 301
    srv.appairage.horloge = h
    r, b = req(srv, "GET", chemin, cookie=False)
    assert r.status == 410 and r.getheader("Set-Cookie") is None and "Link expired" in b.decode()


@pytest.mark.parametrize("chemin", ["/appairer/", "/appairer/faux", "/appairer/%00%ff", "/appairer/a/b",
                                    "/appairer/" + "x" * 300, "/appairer/%2e%2e%2fapi"])
def test_lien_faux_page_expiree_pas_500(srv, chemin):
    r, b = req(srv, "GET", chemin, cookie=False)
    assert r.status == 410 and r.getheader("Set-Cookie") is None and "Link expired" in b.decode()


def test_lien_hote_etranger_403(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    r, _ = req(srv, "GET", chemin, cookie=False, host="evil.test")
    assert r.status == 403 and r.getheader("Set-Cookie") is None
    assert srv.appairage.en_attente() == 1  # le code n'est pas brûlé par l'hôte refusé


def test_lien_en_post_refuse(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    r, _ = req(srv, "POST", chemin, {}, cookie=False)
    assert r.status == 405 and r.getheader("Set-Cookie") is None


def test_essais_limites_par_adresse(srv):
    chemin = urlsplit(nouveau_lien(srv)["lien"]).path
    for _ in range(ap.ESSAIS):
        assert req(srv, "GET", "/appairer/faux", cookie=False)[0].status == 410
    r, b = req(srv, "GET", chemin, cookie=False)  # même le bon code : la 11e tentative est refusée
    assert r.status == 429 and r.getheader("Set-Cookie") is None and "Too many attempts" in b.decode()
    assert srv.appairage.en_attente() == 1


def test_le_code_ne_va_dans_aucun_journal(srv, caplog):
    caplog.set_level(logging.DEBUG)
    lien = nouveau_lien(srv)["lien"]
    code = lien.rsplit("/", 1)[1]
    req(srv, "GET", urlsplit(lien).path, cookie=False)
    req(srv, "GET", urlsplit(lien).path, cookie=False)
    assert code not in caplog.text and test_server.TOKEN not in caplog.text


def test_le_module_n_ecrit_rien_sur_disque():
    src = Path(ap.__file__).read_text()
    for interdit in ("open(", "write", "Path(", "log.", "logging", "print("):
        assert interdit not in src, interdit


def test_service_worker_coquille_et_lien_jamais_en_cache():
    sw = (ICI.parent / "static" / "sw.js").read_text()
    assert '"/static/vendor/qrcode/qrcode.js"' in sw.split("];")[0]  # dans la COQUILLE
    garde = next(l for l in sw.splitlines() if l.strip().startswith('if (u.pathname.startsWith("/api/")'))
    assert 'u.pathname.startsWith("/appairer/")' in garde and garde.rstrip().endswith("return;")
    html = (ICI.parent / "static" / "index.html").read_text()
    assert html.index("/static/vendor/qrcode/qrcode.js") < html.index("/static/app.js")


# ---------- le QR code ----------

def _jsqr():
    """Décodeur QR indépendant de l'encodeur : jsQR (Apache-2.0), fourni par bitwarden-cli dans le
    store Nix de la machine, ou HERDR_JSQR. Absent : le test SAUTE en le disant."""
    p = os.environ.get("HERDR_JSQR")
    if p and Path(p).is_file():
        return p
    for p in glob.glob("/nix/store/*-bitwarden-cli-*/lib/node_modules/@bitwarden/clients/node_modules/jsqr/dist/jsQR.js"):
        return p
    return None


DECODE = r"""
const jsQR = require(process.argv[1]); require(process.argv[2]);
const texte = process.argv[3];
const m = globalThis.HerdrQR.matrice(texte), ech = 6, marge = 4, n = (m.taille + 2 * marge) * ech;
const px = new Uint8ClampedArray(n * n * 4).fill(255);
for (let l = 0; l < m.taille; l++) for (let c = 0; c < m.taille; c++) if (m.noir(l, c))
  for (let y = 0; y < ech; y++) for (let x = 0; x < ech; x++) {
    const i = (((l + marge) * ech + y) * n + (c + marge) * ech + x) * 4; px[i] = px[i + 1] = px[i + 2] = 0; }
const r = jsQR(px, n, n);
process.stdout.write(JSON.stringify(r ? r.data : null));
"""


def test_qr_decode_rend_le_lien_et_pas_le_jeton(srv):
    jsqr, node = _jsqr(), shutil.which("node")
    if not jsqr or not node:
        pytest.skip("jsQR ou node absent : décodage du QR non rejoué (HERDR_JSQR=<jsQR.js>)")
    lien = nouveau_lien(srv)["lien"]
    r = subprocess.run([node, "-e", DECODE, jsqr, str(QRJS), lien], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-1000:]
    lu = json.loads(r.stdout)
    assert lu == lien
    assert test_server.TOKEN not in lu
