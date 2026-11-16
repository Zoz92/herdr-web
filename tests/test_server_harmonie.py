"""Harmonie des zones Agents (spec du 2026-11-15) : épingle.
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import threading

import pytest

import test_server
from test_server import FakeBar, FakeBoard, FakeHc, FakeKnown, FakeRun, HOST, agent, cert, req  # noqa: F401
from herdr_web.server import make_server


@pytest.fixture
def srv(cert, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "index.html").write_text("<html>")
    run = FakeRun()
    s = make_server(FakeHc(), FakeBoard([agent()]), test_server.TOKEN, ("127.0.0.1", 0), [HOST], tmp_path,
                    cert[0], cert[1], statusbar=FakeBar(), run=run, home=home, known=FakeKnown(home))
    s.run_fake = run
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.mark.parametrize("valeur,mot", [(True, "true"), (False, "false")])
def test_epingler_un_agent(srv, valeur, mot):
    r, b = req(srv, "POST", "/api/agents/t1/pin", {"pinned": valeur})
    assert r.status == 200, b
    argv, kw = srv.run_fake.appels[0]
    assert argv == ["herdr-app", "do", "terminal.pin", "--terminal", "t1", "--pinned", mot]
    assert "shell" not in kw
    assert srv.board.refreshes == 1  # le label arrive au tableau sans attendre un événement


@pytest.mark.parametrize("corps", [{}, {"pinned": "true"}, {"pinned": 1}, {"pinned": None}])
def test_epingle_invalide_400(srv, corps):
    r, _ = req(srv, "POST", "/api/agents/t1/pin", corps)
    assert r.status == 400 and srv.run_fake.appels == []


def test_epingle_agent_inconnu_404_et_herdr_app_en_echec_502(srv):
    r, _ = req(srv, "POST", "/api/agents/t9/pin", {"pinned": True})
    assert r.status == 404 and srv.run_fake.appels == []
    srv.run_fake.rc = 1
    r, _ = req(srv, "POST", "/api/agents/t1/pin", {"pinned": True})
    assert r.status == 502


def test_epingle_exige_origine_et_jeton(srv):
    assert req(srv, "POST", "/api/agents/t1/pin", {"pinned": True}, origin=False)[0].status == 403
    assert req(srv, "POST", "/api/agents/t1/pin", {"pinned": True}, cookie=False)[0].status == 401
    assert srv.run_fake.appels == []
