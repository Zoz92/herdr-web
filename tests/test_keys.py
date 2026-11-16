"""Touches nommées vers un terminal (brief palette, 2026-11-15) : Ctrl+L qui nettoie l'écran et la
section « Agent ouvert » de la palette. Liste BLANCHE, jamais quand l'agent est `blocked`."""
import json

import pytest

from herdr_web import actions
from herdr_web.actions import AgentBlocked
from test_actions import FakeHc as FakeHcActions
from test_server import cert, req, srv  # noqa: F401 (fixtures)

TOUCHES = ["esc", "ctrl+c", "ctrl+l", "enter", "up", "down"]


def appuis(s):
    return [c for c in s.hc_fake.calls if c[0] == "terminal.keys"]


@pytest.mark.parametrize("touche", TOUCHES)
def test_keys_liste_blanche_passe_a_terminal_keys(srv, touche):  # noqa: F811
    srv.hc_fake.status = {"state": "idle", "msg": ""}
    r, b = req(srv, "POST", "/api/agents/t1/keys", {"key": touche})
    assert r.status == 200, b
    assert appuis(srv) == [("terminal.keys", {"terminal": "t1", "keys": [touche]})]


@pytest.mark.parametrize("corps", [{"key": "a"}, {"key": "ctrl+d"}, {"key": "Ctrl+L"}, {"key": ["ctrl+l"]},
                                   {"key": "ctrl+l ctrl+c"}, {"keys": ["ctrl+l"]}, {}, {"key": "tab"}])
def test_keys_hors_liste_400_sans_appui(srv, corps):  # noqa: F811
    srv.hc_fake.status = {"state": "idle", "msg": ""}
    r, b = req(srv, "POST", "/api/agents/t1/keys", corps)
    assert r.status == 400, b
    assert appuis(srv) == []


def test_keys_refusee_quand_l_agent_est_bloque(srv):  # noqa: F811
    # jamais une touche à un dialogue : Entrée ou ↓ y répondraient à la place de la personne
    srv.hc_fake.status = {"state": "blocked", "msg": "Allow Bash?"}
    r, b = req(srv, "POST", "/api/agents/t1/keys", {"key": "enter"})
    assert r.status == 409 and json.loads(b)["erreur"] == "bloque"
    assert appuis(srv) == []


def test_keys_sans_cookie_401(srv):  # noqa: F811
    r, _ = req(srv, "POST", "/api/agents/t1/keys", {"key": "ctrl+l"}, cookie=False)
    assert r.status == 401 and srv.hc_fake.calls == []


def test_keys_sans_origine_403(srv):  # noqa: F811
    r, _ = req(srv, "POST", "/api/agents/t1/keys", {"key": "ctrl+l"}, origin=False)
    assert r.status == 403 and srv.hc_fake.calls == []


def test_keys_adresse_inconnue_404(srv):  # noqa: F811
    r, _ = req(srv, "POST", "/api/agents/zz/keys", {"key": "ctrl+l"})
    assert r.status == 404 and srv.hc_fake.calls == []


def test_keys_en_get_404(srv):  # noqa: F811
    r, _ = req(srv, "GET", "/api/agents/t1/keys")
    assert r.status == 404 and appuis(srv) == []


# ---------- actions.send_key, sans serveur ----------
def test_send_key_relit_le_statut_puis_appuie():
    hc = FakeHcActions(results={"terminal.get": {"status": {"state": "working", "msg": ""}}})
    actions.send_key(hc, "t1", "ctrl+l")
    assert [c[:2] for c in hc.calls] == [("terminal.get", {"terminal": "t1"}),
                                         ("terminal.keys", {"terminal": "t1", "keys": ["ctrl+l"]})]


def test_send_key_terminal_sans_statut_appuie():
    hc = FakeHcActions(results={"terminal.get": {"status": None}})  # un zsh : pas de statut d'agent
    actions.send_key(hc, "t2", "ctrl+l")
    assert [c[0] for c in hc.calls] == ["terminal.get", "terminal.keys"]


def test_send_key_bloque_leve_agentblocked_sans_appuyer():
    hc = FakeHcActions(results={"terminal.get": {"status": {"state": "blocked", "msg": "Allow?"}}})
    with pytest.raises(AgentBlocked):
        actions.send_key(hc, "t1", "enter")
    assert [c[0] for c in hc.calls] == ["terminal.get"]


@pytest.mark.parametrize("touche", ["a", "ctrl+d", "tab", "", None, ["ctrl+l"], "CTRL+L"])
def test_send_key_hors_liste_valueerror(touche):
    hc = FakeHcActions()
    with pytest.raises(ValueError):
        actions.send_key(hc, "t1", touche)
    assert hc.calls == []


# ---------- concurrence (réserve du checker, arbitrée : oui) : un envoi à la fois par terminal ----------
def test_keys_occupe_pendant_un_envoi_en_vol(srv):  # noqa: F811
    srv.hc_fake.status = {"state": "idle", "msg": ""}
    with actions._lock:
        actions._inflight.add("t1")  # un terminal.send en cours (brouillon retenu jusqu'à 60 s)
    try:
        r, b = req(srv, "POST", "/api/agents/t1/keys", {"key": "esc"})
    finally:
        with actions._lock:
            actions._inflight.discard("t1")
    assert r.status == 409 and json.loads(b)["erreur"] == "occupe"
    assert appuis(srv) == []
    r, _ = req(srv, "POST", "/api/agents/t1/keys", {"key": "esc"})  # libéré : la touche passe
    assert r.status == 200 and appuis(srv) == [("terminal.keys", {"terminal": "t1", "keys": ["esc"]})]


def test_send_key_occupe_puis_libere_meme_en_erreur():
    hc = FakeHcActions(results={"terminal.get": {"status": {"state": "blocked"}}})
    with pytest.raises(AgentBlocked):
        actions.send_key(hc, "t9", "enter")
    assert "t9" not in actions._inflight  # libéré malgré l'erreur
    with actions._lock:
        actions._inflight.add("t9")
    try:
        with pytest.raises(actions.Busy):
            actions.send_key(FakeHcActions(), "t9", "ctrl+l")
        with pytest.raises(actions.Busy):  # et l'inverse : un texte n'entre pas pendant une touche
            actions.send_text(FakeHcActions(), "t9", "x")
    finally:
        with actions._lock:
            actions._inflight.discard("t9")
