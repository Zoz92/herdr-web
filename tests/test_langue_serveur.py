"""Langue d'affichage côté serveur (brief langue-web, 2026-11-15) : réglage `langue` partagé (défaut en),
posé dans <html lang> de la page, page « lien expiré » et titre du push dans cette langue, erreurs de saisie
rendues en codes (« motif » + paramètres) que la page traduit.
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import test_server
from test_server import FakeBoard, FakeHc, HOST, agent, cert, req  # noqa: F401
from herdr_web import reglages as reglages_mod
from herdr_web.reglages import Reglages, handy_info
from herdr_web.server import make_server

STATIC = Path(__file__).resolve().parent.parent / "static"


class FakePush:
    def appareils(self):
        return []

    def public_key(self):
        return "k"


@pytest.fixture
def srv(cert, tmp_path):
    (tmp_path / "index.html").write_text((STATIC / "index.html").read_text())
    (tmp_path / "sw.js").write_text('const CACHE = "herdr-coquille-v99";\n')
    s = make_server(FakeHc(), FakeBoard([agent("t1")]), test_server.TOKEN, ("127.0.0.1", 0), [HOST], tmp_path,
                    cert[0], cert[1], push=FakePush(), reglages=Reglages(tmp_path / "conf" / "reglages.json"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def langue_de_la_page(srv):
    r, b = req(srv, "GET", "/", cookie=False)  # la page se charge sans cookie : l'écran de jeton aussi est traduit
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/html")
    html = b.decode()
    assert html.count("<html lang=") == 1
    return html.split('<html lang="')[1].split('"')[0]


def test_anglais_par_defaut_puis_francais_partage(srv, tmp_path):
    assert langue_de_la_page(srv) == "en"
    assert json.loads(req(srv, "GET", "/api/reglages")[1])["langue"] == "en"
    vus = []
    srv.broadcast = lambda ev, data: vus.append((ev, data))
    r, b = req(srv, "POST", "/api/reglages", {"langue": "fr"})
    assert r.status == 200 and json.loads(b)["langue"] == "fr"
    assert ("reglages", {"handy": True, "nom_affiche": json.loads(b)["nom_affiche"], "voix": reglages_mod.VOIX_DEFAUT,
                         "langue": "fr"}) in vus  # les autres appareils suivent (événement SSE)
    assert json.loads((tmp_path / "conf" / "reglages.json").read_text())["langue"] == "fr"
    assert langue_de_la_page(srv) == "fr" and json.loads(req(srv, "GET", "/api/reglages")[1])["langue"] == "fr"
    assert req(srv, "POST", "/api/reglages", {"langue": "en"})[0].status == 200 and langue_de_la_page(srv) == "en"


@pytest.mark.parametrize("valeur", ["de", "", None, 1, ["fr"], "FR"])
def test_langue_hors_liste_refusee(srv, valeur):
    r, b = req(srv, "POST", "/api/reglages", {"langue": valeur})
    assert r.status == 400 and json.loads(b)["erreur"] == "invalide"
    assert srv.reglages.langue() == "en"  # rien écrit


def test_fichier_ecrit_a_la_main_langue_inconnue(tmp_path):
    f = tmp_path / "r.json"
    f.write_text('{"langue": "de"}')
    assert Reglages(f).lire()["langue"] == "en"


def test_nom_par_defaut_dans_la_langue(tmp_path, monkeypatch):
    monkeypatch.setattr(reglages_mod, "nom_compte", lambda: "Vous")  # compte sans nom lisible
    f = tmp_path / "r.json"
    assert Reglages(f).lire()["nom_affiche"] == "You"
    f.write_text('{"langue": "fr"}')
    assert Reglages(f).lire()["nom_affiche"] == "Vous"
    monkeypatch.setattr(reglages_mod, "nom_compte", lambda: "Marie Curie")
    assert Reglages(f).lire()["nom_affiche"] == "Marie Curie"


def nouveau_lien(srv):
    r, b = req(srv, "POST", "/api/appairage", {})
    assert r.status == 200
    return urlsplit(json.loads(b)["lien"]).path


def test_page_lien_expire_dans_la_langue(srv):
    r, b = req(srv, "GET", "/appairer/faux", cookie=False)
    assert r.status == 410 and '<html lang="en">' in b.decode() and "Link expired" in b.decode()
    assert "Open herdr" in b.decode() and "Lien" not in b.decode()
    req(srv, "POST", "/api/reglages", {"langue": "fr"})
    r, b = req(srv, "GET", "/appairer/faux", cookie=False)
    assert r.status == 410 and '<html lang="fr">' in b.decode() and "Lien expiré" in b.decode()
    assert "Ouvrir herdr" in b.decode()
    chemin = nouveau_lien(srv)
    assert req(srv, "GET", chemin, cookie=False)[0].status == 303  # un vrai lien sert toujours


def test_titre_du_push_suit_la_langue(srv):
    assert srv.push.langue() == "en"
    req(srv, "POST", "/api/reglages", {"langue": "fr"})
    assert srv.push.langue() == "fr"


@pytest.mark.parametrize("chemin,corps,motif,params", [
    ("/api/spaces", {"name": "", "folder": "/"}, "nom_space", {}),
    ("/api/spaces", {"name": "ok", "folder": "/etc"}, "dossier_hors", "home"),
    ("/api/reglages", {"voix": {"modele": "!!"}}, "voix_modele", {}),
    ("/api/reglages", {"voix": {"langue": "fr_FR_trop_long_pour_un_code"}}, "voix_langue", {}),
    ("/api/reglages", {"voix": {"vocabulaire": ["x"] * 201}}, "voix_liste", {"max": 200}),
    ("/api/reglages", {"voix": {"vocabulaire": ["a\u0007b"]}}, "voix_mot", {"max": 64}),
    ("/api/reglages", {"nom_affiche": "x" * 61}, "nom_affiche", {"max": 60}),
    ("/api/agents/t1/send", {"text": "  "}, "texte_vide", {}),
    ("/api/tabs/inconnu/rename", {"name": "ok"}, "onglet_inconnu", {}),
    ("/api/tabs/inconnu/rename", {"name": "x" * 65}, "nom_onglet", {}),
])
def test_erreurs_de_saisie_rendues_en_codes(srv, chemin, corps, motif, params):
    r, b = req(srv, "POST", chemin, corps)
    d = json.loads(b)
    assert r.status in (400, 404) and d["motif"] == motif, d
    if params == "home":
        assert set(d["params"]) == {"home"} and d["params"]["home"].startswith("/")
    else:
        assert d["params"] == params
    assert isinstance(d["detail"], str) and d["detail"]  # le texte d'avant reste, pour une page d'avant


def test_erreur_sans_motif_inchangee(srv):
    r, b = req(srv, "POST", "/api/reglages", {"inconnu": 1})
    assert r.status == 400 and set(json.loads(b)) == {"erreur", "detail"}


def test_binaire_de_handy_absent_rendu_en_code(tmp_path):
    desktop = tmp_path / "Handy.desktop"
    desktop.write_text("[Desktop Entry]\nExec=/nulle/part/handy\n")
    h = handy_info(desktop=desktop, settings=tmp_path / "absent.json", which=lambda _: None)
    assert (h["present"], h["bin_motif"], h["bin_params"]) == (False, "bin_introuvable", {"nom": "handy"})
    assert h["bin_erreur"] == "binaire introuvable : handy"
    h = handy_info(desktop=tmp_path / "absent.desktop", settings=tmp_path / "absent.json", which=lambda _: None)
    assert h["bin_motif"] == "bin_desktop" and h["bin_params"]["nom"] == "absent.desktop"
