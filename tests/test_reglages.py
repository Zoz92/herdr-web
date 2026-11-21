"""Panneau de réglages (roue crantée) côté serveur, et le « vu » partagé avec herdr-app (done -> idle).
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import json
import os
import stat
import threading

import pytest

import test_server
from test_server import FakeBoard, FakeHc, HOST, agent, cert, req  # noqa: F401
from test_agents import board, term
from herdr_web import reglages as reglages_mod
from herdr_web.agents import etat_vu, label_seen
from herdr_web.reglages import Reglages, cache_sw, handy_info
from herdr_web.server import COOKIE_MAX_AGE, make_server


class FakePush:
    def __init__(self, endpoints):
        self.endpoints = endpoints

    def appareils(self):
        return self.endpoints

    def public_key(self):
        return "k"


@pytest.fixture
def srv(cert, tmp_path):
    (tmp_path / "index.html").write_text("<html>")
    (tmp_path / "sw.js").write_text('const CACHE = "herdr-coquille-v99";\n')
    a = agent("t1", state="done")
    a.turn = 4
    s = make_server(FakeHc(), FakeBoard([a]), test_server.TOKEN, ("127.0.0.1", 0), [HOST], tmp_path,
                    cert[0], cert[1], push=FakePush(["fcm.googleapis.com"]),
                    reglages=Reglages(tmp_path / "conf" / "reglages.json"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def multipart_audio():
    b = "XyZ"
    corps = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"v.webm\"\r\n"
             f"Content-Type: audio/webm\r\n\r\nabc\r\n--{b}--\r\n").encode()
    return corps, {"Content-Type": f"multipart/form-data; boundary={b}"}


# -- 1e : tout passe derrière l'authentification --

@pytest.mark.parametrize("method,path", [("GET", "/api/reglages"), ("POST", "/api/reglages"),
                                         ("POST", "/api/logout"), ("POST", "/api/agents/t1/seen")])
def test_sans_cookie_401(srv, method, path):
    r, _ = req(srv, method, path, {"handy": False} if method == "POST" else None, cookie=False)
    assert r.status == 401
    assert srv.reglages.voix_active() is True  # rien écrit
    assert not srv.hc.calls  # aucun label posé


# -- 1a : Handy --

def test_reglages_montrent_handy_version_session(srv):
    r, b = req(srv, "GET", "/api/reglages")
    d = json.loads(b)
    assert r.status == 200
    assert d["handy"]["active"] is True and d["handy"]["delai_s"] == 90
    assert set(d["handy"]) >= {"bin", "version", "modele", "langue", "ffmpeg"}
    assert d["version"]["cache"] == "herdr-coquille-v99"
    assert d["session"]["cookie_max_age_s"] == COOKIE_MAX_AGE == 31536000
    assert d["session"]["appareils_push"] == ["fcm.googleapis.com"]


def test_desactiver_handy_persiste_et_refuse_la_voix(srv, tmp_path):
    r, b = req(srv, "POST", "/api/reglages", {"handy": False})
    assert r.status == 200 and json.loads(b)["handy"] is False
    f = tmp_path / "conf" / "reglages.json"
    assert json.loads(f.read_text()) == {"handy": False, "langue": "en"}
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert Reglages(f).voix_active() is False  # relu par un autre processus : partagé
    corps, h = multipart_audio()
    r, b = req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)
    assert r.status == 403 and json.loads(b)["erreur"] == "voix_desactivee"
    assert not srv.hc.calls  # rien envoyé à l'agent


def test_reactiver_handy_rouvre_la_voix(srv, monkeypatch):
    envois = []  # jamais ffmpeg ni Handy pour de vrai
    monkeypatch.setattr("herdr_web.actions.send_voice", lambda hc, addr, data, modele=None: envois.append(addr) or {"text": "ok"})
    req(srv, "POST", "/api/reglages", {"handy": False})
    req(srv, "POST", "/api/reglages", {"handy": True})
    corps, h = multipart_audio()
    r, b = req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)
    assert r.status == 200 and envois == ["t1"]


@pytest.mark.parametrize("corps", [{"handy": "non"}, {"handy": 0}, {}, {"handy": True, "x": 1}])
def test_reglage_invalide_400(srv, corps):
    r, _ = req(srv, "POST", "/api/reglages", corps)
    assert r.status == 400
    assert srv.reglages.voix_active() is True


def test_changement_diffuse_aux_autres_appareils(srv):
    vus = []
    srv.broadcast = lambda ev, data: vus.append((ev, data))
    req(srv, "POST", "/api/reglages", {"handy": False})
    assert ("reglages", {"handy": False, "nom_affiche": reglages_mod.nom_compte(), "voix": reglages_mod.VOIX_DEFAUT,
                         "langue": "en"}) in vus


def test_reglages_fichier_illisible_ou_type_faux_rend_les_defauts(tmp_path, monkeypatch):
    monkeypatch.setattr(reglages_mod, "nom_compte", lambda: "Compte")
    f = tmp_path / "r.json"
    f.write_text("{pas du json")
    defauts = {"handy": True, "langue": "en", "nom_affiche": "Compte", "voix": reglages_mod.VOIX_DEFAUT}
    assert Reglages(f).lire() == defauts
    f.write_text('{"handy": "false"}')
    assert Reglages(f).lire() == defauts
    Reglages(f).ecrire(handy=False)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]  # aucun reste d'écriture


def test_handy_info_lit_le_store_et_la_config_sans_cle_d_api(tmp_path):
    desk = tmp_path / "Handy.desktop"
    desk.write_text("[Desktop Entry]\nExec=/nix/store/" + "a" * 32 + "-handy-0.9.8/bin/.handy-wrapped \n")
    conf = tmp_path / "settings_store.json"
    conf.write_text(json.dumps({"settings": {"selected_model": "Qwen3-ASR", "selected_language": "fr",
                                             "post_process_api_keys": {"openai": "sk-secret"}}}))
    d = handy_info(desk, conf, which=lambda n: "/bin/ffmpeg")
    assert d["version"] == "0.9.8" and d["modele"] == "Qwen3-ASR" and d["langue"] == "fr"
    assert d["ffmpeg"] == "/bin/ffmpeg" and d["delai_s"] == 90
    assert "sk-secret" not in json.dumps(d)


def test_handy_info_sans_desktop_ni_config_le_dit(tmp_path):
    d = handy_info(tmp_path / "absent.desktop", tmp_path / "absent.json", which=lambda n: None)
    assert d["bin"] is None and d["bin_erreur"] and d["modele"] is None and d["ffmpeg"] is None


def test_cache_du_sw_lu_dans_le_vrai_fichier():
    assert cache_sw(test_server.__file__.rsplit("/tests/", 1)[0] + "/static") == "herdr-coquille-v42"


def test_commit_servi_est_un_sha():
    assert reglages_mod.commit_servi(test_server.__file__.rsplit("/tests/", 1)[0])


# -- 1e : déconnexion --

def test_logout_efface_le_cookie(srv):
    r, _ = req(srv, "POST", "/api/logout", {})
    assert r.status == 200
    c = r.getheader("Set-Cookie")
    assert c.startswith("hw=;") and "Max-Age=0" in c and "HttpOnly" in c


# -- 3 : « vu » partagé --

def test_seen_pose_le_label_de_herdr_app(srv):
    r, b = req(srv, "POST", "/api/agents/t1/seen", {})
    assert r.status == 200
    assert srv.hc.calls == [("terminal.label", {"terminal": "t1", "labels": {"herdr.seen": "4"}})]


def test_seen_deja_vu_n_ecrit_rien(srv):
    srv.board.agents[0].seen = 4
    req(srv, "POST", "/api/agents/t1/seen", {})
    assert srv.hc.calls == []


def test_seen_agent_inconnu_404(srv):
    r, _ = req(srv, "POST", "/api/agents/zz/seen", {})
    assert r.status == 404 and srv.hc.calls == []


@pytest.mark.parametrize("state,turn,seen,attendu", [
    ("done", 5, 5, "idle"), ("done", 5, 7, "idle"), ("done", 5, 4, "done"), ("done", 5, None, "done"),
    ("working", 5, 5, "working"), ("blocked", 5, 9, "blocked")])
def test_etat_vu(state, turn, seen, attendu):
    assert etat_vu(state, turn, seen) == attendu


@pytest.mark.parametrize("labels,attendu", [({"herdr.seen": "3"}, 3), ({"herdr.seen": "x"}, None),
                                            ({"herdr.seen": "-1"}, None), ({}, None), (None, None)])
def test_label_seen(labels, attendu):
    assert label_seen(labels) == attendu


def test_tableau_lit_herdr_seen_done_vu_devient_idle():
    vu, pas_vu = term("t1", "done"), term("t2", "done")
    vu["turn"] = pas_vu["turn"] = 6
    vu["labels"] = {"herdr.seen": "6"}
    pas_vu["labels"] = {"herdr.seen": "5"}
    _, b = board([vu, pas_vu])
    etats = {a.address: (a.state, a.seen) for a in b.snapshot()}
    assert etats == {"t1": ("idle", 6), "t2": ("done", 5)}


# -- nom affiché : défaut = nom du compte (GECOS, sinon login), le réglage le remplace --

class _Pw:
    def __init__(self, gecos, name):
        self.pw_gecos, self.pw_name = gecos, name


@pytest.mark.parametrize("pw,attendu", [
    (_Pw("Alex Martin,,,", "alex"), "Alex Martin"), (_Pw("", "alex"), "alex"), (_Pw("  ,,", "alex"), "alex"),
    (_Pw("", ""), "Vous")])
def test_nom_compte(pw, attendu):
    assert reglages_mod.nom_compte(getpwuid=lambda uid: pw, uid=lambda: 1000) == attendu


def test_nom_compte_inconnu_du_systeme():
    def absent(uid):
        raise KeyError(uid)
    assert reglages_mod.nom_compte(getpwuid=absent, uid=lambda: 1) == "Vous"


def test_nom_affiche_defaut_remplace_puis_retour(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(reglages_mod, "nom_compte", lambda: "Nom du compte")
    d = json.loads(req(srv, "GET", "/api/reglages")[1])
    assert d["nom_affiche"] == "Nom du compte"
    r, b = req(srv, "POST", "/api/reglages", {"nom_affiche": "  Alex  "})
    assert r.status == 200 and json.loads(b)["nom_affiche"] == "Alex"
    assert json.loads((tmp_path / "conf" / "reglages.json").read_text())["nom_affiche"] == "Alex"
    assert json.loads(req(srv, "GET", "/api/reglages")[1])["nom_affiche"] == "Alex"
    r, b = req(srv, "POST", "/api/reglages", {"nom_affiche": ""})  # champ vide : retour au nom du compte
    assert json.loads(b)["nom_affiche"] == "Nom du compte"
    assert "nom_affiche" not in json.loads((tmp_path / "conf" / "reglages.json").read_text())


@pytest.mark.parametrize("corps", [{"nom_affiche": 3}, {"nom_affiche": "x" * 61}, {"nom_affiche": None}])
def test_nom_affiche_invalide_400(srv, corps):
    assert req(srv, "POST", "/api/reglages", corps)[0].status == 400


def test_nom_affiche_n_ecrase_pas_handy(srv, tmp_path):
    req(srv, "POST", "/api/reglages", {"handy": False})
    req(srv, "POST", "/api/reglages", {"nom_affiche": "Alex"})
    assert json.loads((tmp_path / "conf" / "reglages.json").read_text()) == {"handy": False, "langue": "en", "nom_affiche": "Alex"}
