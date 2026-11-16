"""__main__ : erreur de configuration => code 78 (EX_CONFIG) et message sans valeur ; bind impossible => pas 78."""
import http.client
import socket
import threading

import pytest

from herdr_web import __main__ as m

GOOD = "A" * 48
SECRET = "SECRET-VALUE-zzzz"


@pytest.fixture(autouse=True)
def stubs(monkeypatch, tmp_path):
    class Hc:
        def start(self):
            raise AssertionError("hc.start atteint")
    monkeypatch.setattr(m, "HcClient", Hc)
    monkeypatch.setattr(m, "AgentBoard", lambda hc: object())
    for k in ("HERDR_WEB_TOKEN", "HERDR_WEB_VAPID_KEY", "HERDR_WEB_HOSTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERDR_WEB_STATE", str(tmp_path))


def run(capsys, argv=("--cert", "/nonexistent/c.pem", "--key", "/nonexistent/k.pem")):
    with pytest.raises(SystemExit) as e:
        m.main(list(argv))
    err = capsys.readouterr().err
    assert SECRET not in err
    return e.value.code, err


@pytest.mark.parametrize("token", ["", "tiny1", "é" * 48, "A" * 31, SECRET + "!" * 40])
def test_jeton_invalide_78(monkeypatch, capsys, token):
    monkeypatch.setenv("HERDR_WEB_TOKEN", token)
    code, err = run(capsys)
    assert code == 78 and "jeton" in err and (not token or token not in err)


def test_cle_vapid_illisible_78(monkeypatch, capsys):
    monkeypatch.setenv("HERDR_WEB_TOKEN", GOOD)
    monkeypatch.setenv("HERDR_WEB_VAPID_KEY", SECRET)
    code, _ = run(capsys)
    assert code == 78


def test_certificat_illisible_78(monkeypatch, capsys):
    monkeypatch.setenv("HERDR_WEB_TOKEN", GOOD)
    code, err = run(capsys)
    assert code == 78 and "TLS" in err and "/nonexistent" not in err


def test_argument_invalide_78(capsys):
    code, _ = run(capsys, ["--nimporte"])
    assert code == 78
    code, _ = run(capsys, [])  # --cert/--key requis
    assert code == 78


def test_bind_mal_forme_78(monkeypatch, capsys):
    code, _ = run(capsys, ["--bind", "pas-un-port", "--cert", "c", "--key", "k"])
    assert code == 78


def test_bind_impossible_n_est_pas_78(monkeypatch):
    monkeypatch.setenv("HERDR_WEB_TOKEN", GOOD)

    def boom(*a, **k):
        raise OSError(99, "Cannot assign requested address")
    monkeypatch.setattr(m, "make_server", boom)
    with pytest.raises(OSError):  # trace, rc 1 : l'unit relance (Restart=on-failure)
        m.main(["--cert", "c", "--key", "k"])


@pytest.mark.parametrize("port", ["99999", "65536", "²"])
def test_port_hors_plage_78(capsys, port):
    code, _ = run(capsys, ["--bind", f"127.0.0.1:{port}", "--cert", "c", "--key", "k"])
    assert code == 78


def libre(host):
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("spec", ["127.0.0.1:1,127.0.0.1:1", "127.0.0.1:1,", "", "127.0.0.1:1,x:99999"])
def test_liste_invalide_78(capsys, spec):
    code, _ = run(capsys, ["--bind", spec, "--cert", "c", "--key", "k"])
    assert code == 78


def test_parse_binds_liste_valide():
    assert m.parse_binds("127.0.0.1:10,127.0.0.2:11") == [("127.0.0.1", 10), ("127.0.0.2", 11)]


class FauxHc:
    def start(self): pass
    def on_state(self, cb): pass


class FauxBoard:
    def subscribe(self, cb): pass


def faux_monde(monkeypatch, servers):
    from herdr_web import server as srv
    monkeypatch.setenv("HERDR_WEB_TOKEN", GOOD)
    monkeypatch.setattr(m, "HcClient", FauxHc)
    monkeypatch.setattr(m, "AgentBoard", lambda hc: FauxBoard())

    def ms(*a, **k):
        k.pop("cert", None); k.pop("key", None)  # HTTP clair : pas de certificat en test
        s = srv.make_server(*a, **k)
        servers.append(s)
        return s
    monkeypatch.setattr(m, "make_server", ms)


def test_deux_serveurs_repondent(monkeypatch):
    servers = []
    faux_monde(monkeypatch, servers)
    p1, p2 = libre("127.0.0.1"), libre("127.0.0.2")
    t = threading.Thread(target=m.main, args=(["--bind", f"127.0.0.1:{p1},127.0.0.2:{p2}",
                                               "--cert", "c", "--key", "k"],), daemon=True)
    t.start()
    try:
        for _ in range(100):
            if len(servers) == 2:
                break
            threading.Event().wait(0.05)
        assert len(servers) == 2
        assert servers[0].hc is servers[1].hc and servers[0].board is servers[1].board
        for host, port in (("127.0.0.1", p1), ("127.0.0.2", p2)):
            c = http.client.HTTPConnection(host, port, timeout=5)
            c.request("GET", "/")
            assert c.getresponse().status > 0
            c.close()
    finally:
        for s in servers:
            s.shutdown()
    t.join(5)
    assert not t.is_alive()


def test_une_adresse_prise_rc1_et_libere_les_autres(monkeypatch):
    servers = []
    faux_monde(monkeypatch, servers)
    p1 = libre("127.0.0.1")
    with socket.socket() as occ:
        occ.bind(("127.0.0.2", 0))
        occ.listen()
        p2 = occ.getsockname()[1]
        with pytest.raises(OSError):  # trace, rc 1 : l'unit relance
            m.main(["--bind", f"127.0.0.1:{p1},127.0.0.2:{p2}", "--cert", "c", "--key", "k"])
    assert len(servers) == 1
    with socket.socket() as s:  # le premier port a bien été rendu
        s.bind(("127.0.0.1", p1))
