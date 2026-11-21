import datetime
import http.client
import json
import secrets
import socket
import ssl
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from herdr_web.agents import Agent
from herdr_web.chat import ChatReader
from herdr_web.server import make_server

HOST = "herdr.test:10370"
TOKEN = secrets.token_urlsafe(32)


def agent(addr="t1", state="idle", program="claude-code", pid=7, msg=""):
    return Agent(addr, "a", "", "/w", program, pid, state, None, msg, None, None, 0)


class FakeBoard:
    def __init__(self, agents, spaces=()):
        self.agents, self.subs, self.sp = agents, [], list(spaces)
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1

    def snapshot(self):
        return list(self.agents)

    def spaces(self):
        return list(self.sp)

    def subscribe(self, cb):
        self.subs.append(cb)


class FakeHc:
    connected = True

    def __init__(self):
        self.calls, self.screen = [], "texte\n"
        self.status = {"state": "idle", "msg": ""}

    def on_state(self, cb):
        pass

    def request(self, method, params, timeout=100.0):
        self.calls.append((method, params))
        if method == "terminal.read":
            return {"text": self.screen, "complete": True, "how": "x", "why": None}
        if method == "terminal.get":
            return {"status": self.status}
        if method == "terminal.put":
            return {"path": "/f/" + params["name"]}
        return {}


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "herdr.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
         .sign(key, hashes.SHA256()))
    (d / "c.pem").write_bytes(c.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return str(d / "c.pem"), str(d / "k.pem")


@pytest.fixture
def srv(cert, tmp_path):
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    s.hc_fake, s.board_fake = hc, board
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def req(s, method, path, body=None, headers=None, host=HOST, origin=True, cookie=True):
    h = {"Host": host}
    if origin and method != "GET":
        h["Origin"] = f"https://{host}"
    if cookie:
        h["Cookie"] = f"hw={TOKEN}"
    h.update(headers or {})
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", s.server_address[1], context=ctx, timeout=5)
    if isinstance(body, dict):
        body = json.dumps(body)
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r, r.read()


def test_host_etranger_403(srv):
    r, _ = req(srv, "GET", "/api/agents", host="evil.test")
    assert r.status == 403


def test_origine_absente_sur_post_403(srv):
    r, _ = req(srv, "POST", "/api/agents/t1/send", {"text": "x"}, origin=False)
    assert r.status == 403


def test_sans_cookie_401(srv):
    r, _ = req(srv, "GET", "/api/agents", cookie=False)
    assert r.status == 401


def test_login_pose_cookie_secure(srv):
    r, _ = req(srv, "POST", "/login", {"token": TOKEN}, cookie=False)
    c = r.getheader("Set-Cookie")
    assert r.status == 200 and c.startswith("hw=")
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Max-Age=31536000", "Path=/"):
        assert attr in c


def test_mauvais_jeton_401(srv):
    r, _ = req(srv, "POST", "/login", {"token": "x" * 40}, cookie=False)
    assert r.status == 401 and r.getheader("Set-Cookie") is None


def test_answer_perime_409(srv):
    srv.hc_fake.screen = "1. Oui\n2. Non\n"
    srv.hc_fake.status = {"state": "blocked", "msg": "autre"}
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "voulu"})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    assert not [c for c in srv.hc_fake.calls if c[0] == "terminal.keys"]


def test_answer_chiffre_absent_400(srv):
    srv.hc_fake.screen = "1. Oui\n2. Non\n"
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m"})
    assert r.status == 400


def test_answer_ok(srv):
    srv.hc_fake.screen = "1. Oui\n2. Non\n"
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m"})
    assert r.status == 200
    assert ("terminal.keys", {"terminal": "t1", "keys": ["2"]}) in srv.hc_fake.calls


def test_adresse_inconnue_404(srv):
    r, _ = req(srv, "POST", "/api/agents/zz/send", {"text": "x"})
    assert r.status == 404


def test_corps_trop_gros_413(srv):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    s = ctx.wrap_socket(raw)
    s.sendall((f"POST /api/agents/t1/send HTTP/1.1\r\nHost: {HOST}\r\nOrigin: https://{HOST}\r\n"
               f"Cookie: hw={TOKEN}\r\nContent-Length: {13 * 1024 * 1024}\r\n\r\n").encode())
    assert s.recv(100).startswith(b"HTTP/1.0 413")


def test_stream_envoie_agents(srv):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", srv.server_address[1], context=ctx, timeout=5)
    c.request("GET", "/api/stream", headers={"Host": HOST, "Cookie": f"hw={TOKEN}"})
    r = c.getresponse()
    assert r.getheader("Content-Type") == "text/event-stream"
    assert r.fp.readline() == b"event: agents\n"
    assert json.loads(r.fp.readline()[len(b"data: "):])[0]["address"] == "t1"
    r.fp.readline()
    assert r.fp.readline() == b"event: etat\n"


def test_chat_programme_autre_brut_sans_note(srv):
    srv.board_fake.agents = [agent(program="zsh")]
    r, b = req(srv, "GET", "/api/agents/t1/chat")
    d = json.loads(b)
    assert d == {"mode": "brut", "screen": "texte\n", "note": None}


def test_chat_session_introuvable(srv, monkeypatch):
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: None)
    d = json.loads(req(srv, "GET", "/api/agents/t1/chat")[1])
    assert d["mode"] == "brut" and d["note"].startswith("pas encore de fil")


def test_chat_et_flux_chat(srv, monkeypatch, tmp_path):
    j = tmp_path / "s.jsonl"
    j.write_text(json.dumps({"type": "assistant", "timestamp": "t",
                             "message": {"content": [{"type": "text", "text": "salut"}]}}) + "\n")
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: j)
    d = json.loads(req(srv, "GET", "/api/agents/t1/chat?n=10")[1])
    assert d["mode"] == "chat" and d["items"][0]["text"] == "salut"
    got = []
    srv.broadcast = lambda ev, data: got.append((ev, data))
    with j.open("a") as f:
        f.write(json.dumps({"type": "assistant", "timestamp": "t",
                            "message": {"content": [{"type": "text", "text": "suite"}]}}) + "\n")
    for _ in range(50):
        if got:
            break
        time.sleep(0.1)
    assert got[0][0] == "chat" and got[0][1]["items"][0]["text"] == "suite"
    assert "reset" not in got[0][1]


def test_photo_type_refuse_415(srv):
    body = (b"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
            b"Content-Type: text/plain\r\n\r\nhello\r\n--B--\r\n")
    r, _ = req(srv, "POST", "/api/agents/t1/photo", body,
               {"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status == 415


def test_photo_ok(srv):
    body = (b"--B\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\nvoici\r\n"
            b"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"../a.png\"\r\n"
            b"Content-Type: image/png\r\n\r\nPNGDATA\r\n--B--\r\n")
    r, _ = req(srv, "POST", "/api/agents/t1/photo", body,
               {"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status == 200
    put = [p for m, p in srv.hc_fake.calls if m == "terminal.put"][0]
    assert put["name"] == "a.png"


def test_static_et_traversee(srv, tmp_path):
    assert req(srv, "GET", "/", cookie=False)[1] == b"<html>"
    assert req(srv, "GET", "/sw.js", cookie=False)[0].status == 404
    assert req(srv, "GET", "/static/%2e%2e/%2e%2e/etc/passwd", cookie=False)[0].status == 404
    assert req(srv, "GET", "/static//etc/passwd", cookie=False)[0].status == 404


@pytest.mark.parametrize("tok", ["", "court"])
def test_demarrage_refuse_jeton_court(tok):
    with pytest.raises(ValueError):
        make_server(FakeHc(), FakeBoard([]), tok, ("127.0.0.1", 0))


def test_flux_chat_reset_sur_journal_remplace(srv, monkeypatch, tmp_path):
    def ligne(t):
        return json.dumps({"type": "assistant", "timestamp": "t",
                           "message": {"content": [{"type": "text", "text": t}]}}) + "\n"
    j = tmp_path / "s.jsonl"
    j.write_text(ligne("un") + ligne("deux"))
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: j)
    req(srv, "GET", "/api/agents/t1/chat")
    got = []
    srv.broadcast = lambda ev, data: got.append((ev, data))
    j.write_text(ligne("neuf"))  # tronqué : plus court que l'offset lu
    for _ in range(50):
        if got:
            break
        time.sleep(0.1)
    assert got[0][1]["reset"] is True and [i["text"] for i in got[0][1]["items"]] == ["neuf"]


def _raw(srv, head):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5))
    s.sendall(head.encode())
    return s.recv(100)


def test_content_length_negatif_400_sans_cookie(srv):
    head = (f"POST /login HTTP/1.1\r\nHost: {HOST}\r\nOrigin: https://{HOST}\r\n"
            "Content-Length: -1\r\n\r\n")
    assert _raw(srv, head).startswith(b"HTTP/1.0 400")


def test_chat_get_ne_vole_pas_les_lignes_au_flux(srv, monkeypatch, tmp_path):
    def ligne(t):
        return json.dumps({"type": "assistant", "timestamp": "t",
                           "message": {"content": [{"type": "text", "text": t}]}}) + "\n"
    j = tmp_path / "s.jsonl"
    j.write_text(ligne("un"))
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: j)
    srv.stopping.set()  # déterministe : le fil de suivi s'arrête, les passes se jouent à la main
    srv.watcher.join(5)
    req(srv, "GET", "/api/agents/t1/chat")
    got = []
    srv.broadcast = lambda ev, data: got.append((ev, data))
    with j.open("a") as f:
        f.write(ligne("deux"))
    d = json.loads(req(srv, "GET", "/api/agents/t1/chat")[1])  # un autre client lit entre-temps
    assert [i["text"] for i in d["items"]] == ["un"]  # « deux » viendra par le flux, une fois
    srv._poll_chats()
    assert [i["text"] for i in got[0][1]["items"]] == ["deux"]


def test_mauvais_cookie_401(srv):
    bad = {"Cookie": "hw=" + "x" * 43}
    assert req(srv, "GET", "/api/agents", headers=bad)[0].status == 401
    assert req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m"},
               headers=bad)[0].status == 401


@pytest.mark.parametrize("origin", ["https://evil.test", "null"])
def test_origine_fausse_avec_bon_cookie_403(srv, origin):
    r, _ = req(srv, "POST", "/api/agents/t1/send", {"text": "x"}, headers={"Origin": origin})
    assert r.status == 403


def test_host_etranger_sur_login_403(srv):
    assert req(srv, "POST", "/login", {"token": TOKEN}, host="evil.test", cookie=False)[0].status == 403


def test_host_etranger_sans_cookie_403_pas_401(srv):
    assert req(srv, "GET", "/api/agents", host="evil.test", cookie=False)[0].status == 403


def test_jeton_hors_alphabet_refuse():
    with pytest.raises(ValueError):
        make_server(FakeHc(), FakeBoard([]), "a b" * 20, ("127.0.0.1", 0))


@pytest.mark.parametrize("fname", ["..", ".", ""])
def test_nom_de_photo_sur(srv, fname):
    body = (f"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
            "Content-Type: image/png\r\n\r\nX\r\n--B--\r\n").encode()
    r, _ = req(srv, "POST", "/api/agents/t1/photo", body,
               {"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status == 200
    put = [p for m, p in srv.hc_fake.calls if m == "terminal.put"][0]
    assert put["name"] == "photo.png"


def test_file_sse_pleine_retire_le_client(srv):
    import queue as qm
    q = qm.Queue(maxsize=1)
    q.dropped = False
    srv.clients.add(q)
    srv.broadcast("ping", {})
    srv.broadcast("ping", {})
    assert q.dropped and q not in srv.clients


def test_flux_bloque_ferme_la_connexion(srv, monkeypatch):
    monkeypatch.setattr("herdr_web.server.PING_EVERY", 0.5)  # écriture bloquée : OSError en 1 s
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    c = ctx.wrap_socket(raw)
    fermee = threading.Event()
    vrai = srv.shutdown_request
    monkeypatch.setattr(srv, "shutdown_request", lambda r: (vrai(r), fermee.set()))
    c.sendall((f"GET /api/stream HTTP/1.1\r\nHost: {HOST}\r\nCookie: hw={TOKEN}\r\n\r\n").encode())
    for _ in range(50):
        if srv.clients:
            break
        time.sleep(0.1)
    assert len(srv.clients) == 1
    srv.broadcast("chat", {"addr": "t1", "items": ["x" * 8_000_000]})  # plus gros que les tampons : l'écriture bloque
    time.sleep(0.5)
    for _ in range(300):  # le client ne lit pas : la file se remplit derrière l'écriture bloquée
        srv.broadcast("ping", {})
    for _ in range(100):
        if not srv.clients:
            break
        time.sleep(0.1)
    assert not srv.clients  # retiré pour file pleine
    # le client ne lit TOUJOURS pas : seul le délai d'écriture peut libérer le fil du handler
    assert fermee.wait(10), "le fil du flux est resté bloqué sur l'écriture"


def _img_srv(srv, tmp_path):
    root = tmp_path / "core"
    (root / "t" / "files").mkdir(parents=True)
    srv.image_root = root
    return root


def test_image_servie(srv, tmp_path):
    root = _img_srv(srv, tmp_path)
    (root / "t/files/A.PNG").write_bytes(b"\x89PNGdata")
    r, b = req(srv, "GET", "/api/image?p=" + str(root / "t/files/A.PNG"))
    assert r.status == 200 and b == b"\x89PNGdata"
    assert r.getheader("Content-Type") == "image/png"
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert r.getheader("Cache-Control") == "no-store"


def test_image_refus_404(srv, tmp_path):
    root = _img_srv(srv, tmp_path)
    (root / "t/files/a.txt").write_text("x")
    (root / "t/hors.png").write_bytes(b"x")  # pas sous un segment files
    (root / "t/files/gros.png").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    ext = tmp_path / "secret.png"
    ext.write_bytes(b"x")
    (root / "t/files/lien.png").symlink_to(ext)
    for p in [root / "t/files/a.txt", root / "t/hors.png", root / "t/files/gros.png",
              root / "t/files/lien.png", ext, root / "t/files/../../../secret.png",
              root / "t/files/absent.png", "relatif.png"]:
        r, _ = req(srv, "GET", "/api/image?p=" + str(p))
        assert r.status == 404, p


def test_image_sans_cookie_401(srv, tmp_path):
    root = _img_srv(srv, tmp_path)
    (root / "t/files/a.png").write_bytes(b"x")
    r, _ = req(srv, "GET", "/api/image?p=" + str(root / "t/files/a.png"), cookie=False)
    assert r.status == 401


def test_image_collee_servie_seulement_si_vue(srv):
    from herdr_web import chat
    cle = chat._collee({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": "AAEC"}})
    r, b = req(srv, "GET", "/api/collee/" + cle)
    assert r.status == 200 and b == b"\x00\x01\x02"
    assert r.getheader("Content-Type") == "image/jpeg"
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert req(srv, "GET", "/api/collee/" + "0" * 64)[0].status == 404
    assert req(srv, "GET", "/api/collee/" + cle.upper())[0].status == 404
    assert req(srv, "GET", "/api/collee/" + cle, cookie=False)[0].status == 401


class FakePush:
    def __init__(self):
        self.subs = []

    def public_key(self):
        return "CLEPUBLIQUE"

    def subscribe(self, sub):
        self.subs.append(sub)


@pytest.fixture
def srv_push(cert, tmp_path):
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1],
                    push=FakePush())
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


GOOD_SUB = {"endpoint": "https://push.test/1", "keys": {"p256dh": "p", "auth": "a"}}


def test_push_key(srv_push):
    r, b = req(srv_push, "GET", "/api/push/key")
    assert r.status == 200 and json.loads(b) == {"key": "CLEPUBLIQUE"}


def test_push_desactive_404(srv):
    assert req(srv, "GET", "/api/push/key")[0].status == 404
    assert req(srv, "POST", "/api/push/subscribe", GOOD_SUB)[0].status == 404


def test_push_subscribe_ok(srv_push):
    r, b = req(srv_push, "POST", "/api/push/subscribe", GOOD_SUB)
    assert r.status == 200 and json.loads(b) == {"ok": True}
    assert srv_push.push.subs == [GOOD_SUB]


@pytest.mark.parametrize("bad", [
    {}, {"endpoint": "http://push.test/1", "keys": {"p256dh": "p", "auth": "a"}},
    {"endpoint": "https://push.test/1"}, {"endpoint": "https://push.test/1", "keys": []},
    {"endpoint": "https://push.test/1", "keys": {"p256dh": "p"}},
    {"endpoint": "https://push.test/1", "keys": {"p256dh": 1, "auth": "a"}},
    {"endpoint": 5, "keys": {"p256dh": "p", "auth": "a"}},
])
def test_push_subscribe_invalide_400(srv_push, bad):
    r, _ = req(srv_push, "POST", "/api/push/subscribe", bad)
    assert r.status == 400 and srv_push.push.subs == []


def test_push_gardes(srv_push):
    assert req(srv_push, "GET", "/api/push/key", cookie=False)[0].status == 401
    assert req(srv_push, "GET", "/api/push/key", host="evil.test")[0].status == 403
    assert req(srv_push, "POST", "/api/push/subscribe", GOOD_SUB, origin=False)[0].status == 403
    assert req(srv_push, "POST", "/api/push/subscribe", GOOD_SUB, cookie=False)[0].status == 401


@pytest.mark.parametrize("bad", [
    {"endpoint": "https://p.test/" + "x" * 2040, "keys": {"p256dh": "p", "auth": "a"}},
    {"endpoint": "https://p.test/1", "keys": {"p256dh": "p" * 257, "auth": "a"}},
    {"endpoint": "https://p.test/1", "keys": {"p256dh": "p", "auth": "a" * 65}},
])
def test_push_subscribe_trop_long_400(srv_push, bad):
    r, _ = req(srv_push, "POST", "/api/push/subscribe", bad)
    assert r.status == 400 and srv_push.push.subs == []


def test_push_subscribe_bornes_acceptees(srv_push):
    ok = {"endpoint": "https://p.test/" + "x" * 2033, "keys": {"p256dh": "p" * 256, "auth": "a" * 64}}
    assert len(ok["endpoint"]) == 2048
    assert req(srv_push, "POST", "/api/push/subscribe", ok)[0].status == 200


# -- v2 : fil d'un sous-agent, lecteur de journal en échec --

def _ligne_agent(t):
    return json.dumps({"type": "assistant", "timestamp": "t",
                       "message": {"content": [{"type": "text", "text": t}]}}) + "\n"


def test_sous_agent_lecture_seule_puis_suite(srv, monkeypatch, tmp_path):
    from herdr_web.chat import CONTRAT_FIN
    j = tmp_path / "agent-a2db.jsonl"
    j.write_text(json.dumps({"type": "user", "timestamp": "t", "message": {
        "content": "contrat\n" + CONTRAT_FIN + "\nRelis le diff."}}) + "\n" + _ligne_agent("vu"))
    vus = []
    monkeypatch.setattr("herdr_web.server.resolve_subagent",
                        lambda pid, aid: vus.append((pid, aid)) or (j if aid == "a2db" else None))
    d = json.loads(req(srv, "GET", "/api/agents/t1/subagent?id=a2db")[1])
    assert vus == [(7, "a2db")]
    assert d["mode"] == "chat" and [i["text"] for i in d["items"]] == ["Relis le diff.", "vu"]
    with j.open("a") as f:
        f.write(_ligne_agent("suite"))
    d2 = json.loads(req(srv, "GET", f"/api/agents/t1/subagent?id=a2db&depuis={d['offset']}")[1])
    assert [i["text"] for i in d2["items"]] == ["suite"] and d2["offset"] == j.stat().st_size
    assert "reset" not in d2
    d3 = json.loads(req(srv, "GET", "/api/agents/t1/subagent?id=a2db&depuis=999999")[1])
    assert d3["reset"] is True and [i["text"] for i in d3["items"]][-1] == "suite"


def test_sous_agent_introuvable_404_et_id_invalide_400(srv, monkeypatch):
    monkeypatch.setattr("herdr_web.server.resolve_subagent", lambda pid, aid: None)
    assert req(srv, "GET", "/api/agents/t1/subagent?id=zz")[0].status == 404
    assert req(srv, "GET", "/api/agents/t1/subagent?id=../x")[0].status == 400
    assert req(srv, "GET", "/api/agents/t1/subagent?id=ok&depuis=-1")[0].status == 400
    assert req(srv, "POST", "/api/agents/t1/subagent?id=ok", {})[0].status == 404


class _LecteurCasse:
    path = "/x"

    def read_new(self):
        raise OSError("journal illisible")


def test_lecteur_en_echec_retire_apres_trois_fois(cert, tmp_path, caplog):
    """Deux serveurs (deux adresses) : chacun a ses lecteurs, le retrait ne touche que le sien."""
    import logging
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    a = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    b = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    try:
        for s in (a, b):
            s.stopping.set()  # passes jouées à la main
            s.watcher.join(5)
        a.readers["t1"] = (7, _LecteurCasse())
        sain = ChatReader(tmp_path / "index.html")
        b.readers["t1"] = (7, sain)
        with caplog.at_level(logging.WARNING, logger="herdr_web.server"):
            for n in range(1, 4):
                assert "t1" in a.readers, f"retiré trop tôt (passe {n})"
                a._poll_chats()
                b._poll_chats()
        assert "t1" not in a.readers  # ré-résolu (resolve_session) au prochain accès
        assert b.readers["t1"][1] is sain
        traces = [r for r in caplog.records if "lecture du journal de t1" in r.getMessage()]
        assert len(traces) == 3 and all(r.exc_info and r.exc_info[0] is OSError for r in traces)
    finally:
        for s in (a, b):
            s.server_close()


def test_un_succes_remet_le_compteur_a_zero(srv, tmp_path):
    srv.stopping.set()
    srv.watcher.join(5)
    n = {"i": 0}

    class Intermittent:
        path = "/x"
        reset = False

        def read_new(self):
            n["i"] += 1
            if n["i"] % 3 == 0:
                return []
            raise OSError("passager")

    srv.readers["t1"] = (7, Intermittent())
    for _ in range(9):  # 2 échecs, 1 succès, ... : jamais 3 d'affilée
        srv._poll_chats()
    assert "t1" in srv.readers


# -- points 19, 20, 24, 25 : spaces, barre d'état --

class FakeRun:
    def __init__(self, rc=0, out="{}"):
        self.rc, self.out, self.appels = rc, out, []

    def __call__(self, argv, **kw):
        import subprocess
        self.appels.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.rc, self.out, "boom")


class FakeBar:
    def __init__(self):
        self.subs = []

    def snapshot(self):
        return {"cpu": "◔ CPU 15%", "mem": "◑ MEM 16,0G", "quota": "—"}

    def subscribe(self, cb):
        self.subs.append(cb)


class FakeKnown:
    """Agents détectés (point 34) : claude et opencode installés, codex absent."""
    def __init__(self, home):
        self.agents = [
            {"id": "claude", "name": "Claude Code", "installed": True,
             "argv": [str(home / ".local/bin/claude")]},
            {"id": "opencode", "name": "OpenCode", "installed": True, "argv": ["/usr/bin/opencode"]},
            {"id": "codex", "name": "Codex", "installed": False, "argv": None}]

    def list(self):
        return self.agents

    def public(self):
        return [{k: a[k] for k in ("id", "name", "installed")} for a in self.agents]

    def installed(self):
        return {a["id"]: a for a in self.agents if a["installed"]}


@pytest.fixture
def srv2(cert, tmp_path):
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    (tmp_path / "ailleurs").mkdir()
    hc = FakeHc()
    a = agent()
    a.model = "claude-opus-5-5"
    board = FakeBoard([a], [{"id": "sp1", "name": "my-project", "folder": str(home / "proj"),
                             "machine": None}])
    (tmp_path / "index.html").write_text("<html>")
    run = FakeRun()
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1],
                    statusbar=FakeBar(), run=run, home=home, known=FakeKnown(home))
    s.run_fake, s.home, s.tmp = run, home, tmp_path
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def test_statusbar_porte_les_segments_et_le_decompte(srv2):
    d = json.loads(req(srv2, "GET", "/api/statusbar")[1])
    assert d == {"cpu": "◔ CPU 15%", "mem": "◑ MEM 16,0G", "quota": "—", "models": {"opus": 1}}


def test_statusbar_sans_barre_rend_des_tirets(srv):
    d = json.loads(req(srv, "GET", "/api/statusbar")[1])
    assert d["cpu"] == d["mem"] == d["quota"] == "—"


def test_statusbar_poussee_par_sse(srv2):
    got = []
    srv2.broadcast = lambda ev, data: got.append((ev, data))
    srv2.statusbar.subs[0]({"cpu": "x", "mem": "y", "quota": "z"})
    for cb in srv2.board.subs:  # nouvelle liste d'agents : le décompte change
        cb([])
    barres = [d for ev, d in got if ev == "barre"]
    assert barres[0]["cpu"] == "x" and barres[0]["models"] == {"opus": 1}
    assert barres[1]["models"] == {}


def test_liste_des_spaces(srv2):
    d = json.loads(req(srv2, "GET", "/api/spaces")[1])
    assert d == [{"id": "sp1", "name": "my-project", "folder": str(srv2.home / "proj"), "machine": None}]


TABS = [{"id": "tb1", "name": "build"}, {"id": "tb2", "name": "shell"}]


@pytest.fixture
def srv3(srv2):
    srv2.board.sp = [{"id": "sp1", "name": "my-project", "folder": None, "machine": None, "tabs": TABS}]
    return srv2


def test_les_spaces_portent_leurs_onglets(srv3):
    d = json.loads(req(srv3, "GET", "/api/spaces")[1])
    assert d[0]["tabs"] == TABS


@pytest.mark.parametrize("nom,attendu", [("neuf", "neuf"), ("  neuf  ", "neuf"), ("", ""), ("   ", "")])
def test_renommer_un_onglet(srv3, nom, attendu):
    r, b = req(srv3, "POST", "/api/tabs/tb1/rename", {"name": nom})
    assert r.status == 200, b
    argv, kw = srv3.run_fake.appels[0]
    assert argv == ["herdr-app", "do", "tab.rename", "--id", "tb1", "--name", attendu]
    assert "shell" not in kw
    # herdr-app n'émet aucun événement hc pour un renommage : sans refresh forcé, /api/spaces
    # rendait l'ancien nom jusqu'au prochain événement d'un agent (crayon disparu entre-temps)
    assert srv3.board.refreshes == 1


@pytest.mark.parametrize("corps", [{"name": "x" * 65}, {"name": "--machine"}, {"name": "a\nb"},
                                   {"name": "a\x7fb"}, {"name": 5}, {"name": ["a"]}])
def test_renommer_un_onglet_nom_invalide_400(srv3, corps):
    r, _ = req(srv3, "POST", "/api/tabs/tb1/rename", corps)
    assert r.status == 400 and srv3.run_fake.appels == []


def test_renommer_un_onglet_inconnu_404(srv3):
    r, _ = req(srv3, "POST", "/api/tabs/tb99/rename", {"name": "x"})
    assert r.status == 404 and srv3.run_fake.appels == []


def test_renommer_un_onglet_echec_de_herdr_app_502(srv3):
    srv3.run_fake.rc = 1
    r, _ = req(srv3, "POST", "/api/tabs/tb1/rename", {"name": "x"})
    assert r.status == 502 and srv3.board.refreshes == 0


def test_renommer_un_onglet_gardes(srv3):
    r, _ = req(srv3, "POST", "/api/tabs/tb1/rename", {"name": "x"}, origin=False)
    assert r.status == 403
    r, _ = req(srv3, "POST", "/api/tabs/tb1/rename", {"name": "x"}, cookie=False)
    assert r.status == 401
    r, _ = req(srv3, "GET", "/api/tabs/tb1/rename")
    assert r.status == 404
    assert srv3.run_fake.appels == []


def test_creer_un_space(srv2):
    r, b = req(srv2, "POST", "/api/spaces", {"name": "api", "folder": str(srv2.home / "proj")})
    assert r.status == 200, b
    argv, kw = srv2.run_fake.appels[0]
    assert argv == ["herdr-app", "do", "space.new", "--name", "api", "--folder",
                    str(srv2.home / "proj")]
    assert "shell" not in kw and kw["timeout"] <= 30
    assert srv2.board.refreshes == 1  # le space neuf est dans /api/spaces dès la réponse


@pytest.mark.parametrize("corps", [
    {"name": "", "folder": "proj"},
    {"name": "   ", "folder": "proj"},
    {"name": "x" * 65, "folder": "proj"},
    {"name": "--machine", "folder": "proj"},
    {"name": "a\nb", "folder": "proj"},
    {"name": "ok", "folder": "absent"},
    {"name": "ok", "folder": "AILLEURS"},
    {"name": "ok", "folder": "relatif"},
    {"name": "ok", "folder": "PROJ/../../ailleurs"},
])
def test_creer_un_space_refus(srv2, corps):
    folder = {"proj": str(srv2.home / "proj"), "absent": str(srv2.home / "absent"),
              "AILLEURS": str(srv2.tmp / "ailleurs"), "relatif": "proj",
              "PROJ/../../ailleurs": str(srv2.home / "proj/../../ailleurs")}[corps["folder"]]
    r, _ = req(srv2, "POST", "/api/spaces", {"name": corps["name"], "folder": folder})
    assert r.status == 400
    assert srv2.run_fake.appels == []


def test_creer_un_space_exige_l_origine(srv2):
    r, _ = req(srv2, "POST", "/api/spaces", {"name": "a", "folder": str(srv2.home / "proj")},
               origin=False)
    assert r.status == 403 and srv2.run_fake.appels == []


def test_creer_un_space_echec_de_herdr_app_502(srv2):
    srv2.run_fake.rc = 1
    r, _ = req(srv2, "POST", "/api/spaces", {"name": "a", "folder": str(srv2.home / "proj")})
    assert r.status == 502


@pytest.mark.parametrize("corps,argv_attendu", [
    ({"kind": "claude"}, ["CLAUDE"]),
    ({"kind": "claude", "model": "default"}, ["CLAUDE"]),
    ({"kind": "claude", "model": "opus"}, ["CLAUDE", "--model", "opus"]),
    ({"kind": "claude", "model": "sonnet"}, ["CLAUDE", "--model", "sonnet"]),
    ({"kind": "terminal"}, None),
])
def test_ouvrir_dans_un_space(srv2, corps, argv_attendu):
    r, b = req(srv2, "POST", "/api/spaces/sp1/open", corps)
    assert r.status == 200, b
    argv, kw = srv2.run_fake.appels[0]
    assert argv[:7] == ["herdr-app", "do", "tab.new", "--space", "sp1", "--folder",
                        str(srv2.home / "proj")]
    assert "shell" not in kw
    if argv_attendu is None:
        assert "--argv" not in argv
    else:
        claude = str(srv2.home / ".local/bin/claude")
        attendu = [claude if x == "CLAUDE" else x for x in argv_attendu]
        assert json.loads(argv[argv.index("--argv") + 1]) == attendu


@pytest.mark.parametrize("corps,chemin,statut", [
    ({"kind": "bash"}, "sp1", 400),
    ({"kind": "claude", "model": "haiku; rm -rf /"}, "sp1", 400),
    ({"kind": "claude", "model": "haiku"}, "sp1", 400),
    ({"kind": "terminal", "model": "opus"}, "sp1", 400),
    ({"kind": "claude"}, "inconnu", 404),
])
def test_ouvrir_refus(srv2, corps, chemin, statut):
    r, _ = req(srv2, "POST", f"/api/spaces/{chemin}/open", corps)
    assert r.status == statut
    assert srv2.run_fake.appels == []


# -- point 27 : commandes / --

def test_commandes_d_un_agent_claude(srv2):
    d = json.loads(req(srv2, "GET", "/api/agents/t1/commands")[1])
    noms = {c["name"] for c in d}
    assert {"model", "resume", "model opus"} <= noms


def test_pas_de_commandes_pour_un_terminal(srv2):
    srv2.board.agents = [agent(program="zsh")]
    assert json.loads(req(srv2, "GET", "/api/agents/t1/commands")[1]) == []


# -- point 30 : http:// en clair sur le port TLS --

def _clair(srv, donnees):
    s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    s.sendall(donnees)
    out = b""
    try:
        while True:
            b = s.recv(4096)
            if not b:
                break
            out += b
    except (socket.timeout, ConnectionResetError):
        pass
    s.close()
    return out


def test_http_en_clair_redirige_vers_le_nom_configure(srv):
    rep = _clair(srv, b"GET /a/b?x=1 HTTP/1.1\r\nHost: 100.64.0.10:10370\r\n\r\n")
    tete = rep.split(b"\r\n\r\n")[0].decode()
    assert tete.startswith("HTTP/1.1 301")
    assert f"\r\nLocation: https://{HOST}/a/b?x=1" in tete


def test_host_hostile_jamais_dans_location(srv):
    rep = _clair(srv, b"GET / HTTP/1.1\r\nHost: evil.test\r\n\r\n").decode(errors="replace")
    assert "evil.test" not in rep and f"Location: https://{HOST}/" in rep
    rep = _clair(srv, b"GET //evil.test/x HTTP/1.1\r\n\r\n").decode(errors="replace")
    assert f"Location: https://{HOST}/" in rep and "Location: https://" + HOST + "//" not in rep


def test_tls_normal_marche_toujours(srv):
    r, b = req(srv, "GET", "/api/agents")
    assert r.status == 200 and json.loads(b)[0]["address"] == "t1"


@pytest.mark.parametrize("octets", [b"\x00\x01\x02garbage\r\n\r\n", b"\x16\x03\x01\x00\x05zzzzz",
                                    b"SSH-2.0-OpenSSH_9\r\n"])
def test_octets_quelconques_fermes_sans_trace(srv, octets, caplog, capsys):
    import logging
    with caplog.at_level(logging.DEBUG):
        rep = _clair(srv, octets)
        time.sleep(0.3)
    assert b"301" not in rep
    assert not [r for r in caplog.records if r.exc_info]
    assert "Traceback" not in capsys.readouterr().err


# -- fix round 1 : gardes de POST /api/spaces/<id>/open et du dossier --

@pytest.mark.parametrize("model", [["opus"], {"a": 1}, 3])
def test_ouvrir_model_non_texte_400(srv2, model):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "claude", "model": model})
    assert r.status == 400 and srv2.run_fake.appels == []


def test_ouvrir_sans_cookie_401(srv2):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "claude"}, cookie=False)
    assert r.status == 401 and srv2.run_fake.appels == []


@pytest.mark.parametrize("origin", ["https://evil.test", "null"])
def test_ouvrir_mauvaise_origine_403(srv2, origin):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "claude"}, headers={"Origin": origin})
    assert r.status == 403 and srv2.run_fake.appels == []


def test_creer_un_space_lien_symbolique_qui_sort_du_home(srv2):
    lien = srv2.home / "lien"
    lien.symlink_to(srv2.tmp / "ailleurs")
    r, _ = req(srv2, "POST", "/api/spaces", {"name": "x", "folder": str(lien)})
    assert r.status == 400 and srv2.run_fake.appels == []


# -- point 33 : fermer un agent ou un terminal --

def test_fermer_kill_puis_rm_en_liste_d_arguments(srv2):
    r, b = req(srv2, "POST", "/api/agents/t1/close", {"confirm": "t1"})
    assert r.status == 200, b
    assert [a for a, _ in srv2.run_fake.appels] == [["hc", "kill", "t1"], ["hc", "rm", "t1"]]
    assert all("shell" not in kw for _, kw in srv2.run_fake.appels)


@pytest.mark.parametrize("corps", [{}, {"confirm": "t2"}, {"confirm": ""}, {"confirm": ["t1"]},
                                   {"confirm": None}])
def test_fermer_refuse_sans_confirmation_exacte(srv2, corps):
    r, _ = req(srv2, "POST", "/api/agents/t1/close", corps)
    assert r.status == 400 and srv2.run_fake.appels == []


def test_fermer_adresse_inconnue_404(srv2):
    r, _ = req(srv2, "POST", "/api/agents/t9/close", {"confirm": "t9"})
    assert r.status == 404 and srv2.run_fake.appels == []


def test_fermer_sans_cookie_401(srv2):
    r, _ = req(srv2, "POST", "/api/agents/t1/close", {"confirm": "t1"}, cookie=False)
    assert r.status == 401 and srv2.run_fake.appels == []


@pytest.mark.parametrize("origin", ["https://evil.test", "null"])
def test_fermer_mauvaise_origine_403(srv2, origin):
    r, _ = req(srv2, "POST", "/api/agents/t1/close", {"confirm": "t1"}, headers={"Origin": origin})
    assert r.status == 403 and srv2.run_fake.appels == []


def test_fermer_echec_de_kill_rend_l_erreur_et_ne_fait_pas_rm(srv2):
    srv2.run_fake.rc = 1
    r, b = req(srv2, "POST", "/api/agents/t1/close", {"confirm": "t1"})
    texte = b.decode() if isinstance(b, bytes) else b
    assert r.status == 502 and "boom" in texte  # l'erreur de hc, telle quelle
    assert [a for a, _ in srv2.run_fake.appels] == [["hc", "kill", "t1"]]



# -- point 34 : menu « + » de herdr-app, agents détectés --

def test_agents_connus_sans_chemin(srv2):
    r, b = req(srv2, "GET", "/api/known-agents")
    assert r.status == 200
    d = json.loads(b)
    assert [(a["id"], a["installed"]) for a in d] == [("claude", True), ("opencode", True),
                                                       ("codex", False)]
    assert all(set(a) == {"id", "name", "installed"} for a in d)  # jamais le chemin du binaire


def test_ouvrir_un_agent_detecte(srv2):
    r, b = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "opencode"})
    assert r.status == 200, b
    argv = srv2.run_fake.appels[0][0]
    assert json.loads(argv[argv.index("--argv") + 1]) == ["/usr/bin/opencode"]
    assert argv[argv.index("--name") + 1] == "opencode"


@pytest.mark.parametrize("corps", [
    {"kind": "codex"},  # connu mais pas installé
    {"kind": "pi"},  # ni installé ni connu ici
    {"kind": "opencode", "model": "opus"},  # le modèle n'existe que pour Claude
    {"kind": "/bin/sh"}, {"kind": "claude --dangerously-skip-permissions"},
])
def test_ouvrir_kind_hors_liste_blanche(srv2, corps):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", corps)
    assert r.status == 400 and srv2.run_fake.appels == []


# -- vague finale : relecture de la branche entière --

def _ligne_user(texte):
    return json.dumps({"type": "user", "timestamp": "t", "message": {"content": texte}}) + "\n"


def test_journal_deplace_par_enter_worktree_suivi_sans_echec(cert, tmp_path, monkeypatch, caplog):
    """Anomalie du 14/11 22:00:53 et 22:03:35 : EnterWorktree / ExitWorktree de Claude Code
    déplacent le .jsonl vers le dossier projet du nouveau cwd (concordance à la seconde). Le code
    d'alors (dc81bcf) gardait l'ancien chemin : 3 FileNotFoundError puis lecteur retiré. Verrou :
    la passe re-résout AVANT de lire, donc ni échec ni avertissement, et le fil continue."""
    import queue as _q
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    try:
        s.stopping.set()
        s.watcher.join(5)
        a, b = tmp_path / "-home-user-Claude-my-project", tmp_path / "-home-user-Claude-my-project--claude-worktrees-x"
        a.mkdir(); b.mkdir()
        (a / "s.jsonl").write_text(_ligne_user("avant"))
        chemin = {"p": a / "s.jsonl"}
        monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: chemin["p"])
        s.reader(board.agents[0])
        q = _q.Queue()
        s.clients.add(q)
        (a / "s.jsonl").rename(b / "s.jsonl")  # EnterWorktree
        with (b / "s.jsonl").open("a") as f:
            f.write(_ligne_user("dans le worktree"))
        chemin["p"] = b / "s.jsonl"
        caplog.clear()
        s._poll_chats()
        ev, d = q.get_nowait()
        assert d["reset"] is True and [i["text"] for i in d["items"]][-1] == "dans le worktree"
        assert s.read_fails == {} and not [r for r in caplog.records if r.levelname == "WARNING"]
    finally:
        s.server_close()


def test_i3_fil_re_resolu_sans_get_chat(cert, tmp_path, monkeypatch):
    """I3 : session changée (même pid) ou lecteur retiré après 3 échecs : la passe de suivi
    re-résout d'elle-même et diffuse un `chat` reset, sans qu'aucune page refasse GET /chat."""
    import queue as _q
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    try:
        s.stopping.set()
        s.watcher.join(5)
        j1, j2 = tmp_path / "j1.jsonl", tmp_path / "j2.jsonl"
        j1.write_text(_ligne_user("ancien"))
        chemin = {"p": j1}
        monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: chemin["p"])
        s.reader(board.agents[0])  # le seul accès « page » de tout le test
        q = _q.Queue()
        s.clients.add(q)
        # 1. /clear : même pid, autre session
        j2.write_text(_ligne_user("neuf"))
        chemin["p"] = j2
        s._poll_chats()
        ev, d = q.get_nowait()
        assert ev == "chat" and d["reset"] is True and [i["text"] for i in d["items"]] == ["neuf"]
        assert s.readers["t1"][1].path == j2
        # 2. journal illisible : retiré après 3 échecs, puis recréé quand il revient
        j2.unlink()
        for _ in range(4):
            s._poll_chats()
        assert "t1" not in s.readers and q.empty()
        j2.write_text(_ligne_user("revenu"))
        s._poll_chats()
        ev, d = q.get_nowait()
        assert d["reset"] is True and [i["text"] for i in d["items"]] == ["revenu"]
        assert "t1" in s.readers
        # 3. agent parti : plus suivi
        board.agents.clear()
        s._poll_chats()
        assert "t1" not in s.readers and "t1" not in s.suivis
    finally:
        s.server_close()


def test_file_sse_bornee_client_lent_retire(srv, monkeypatch):
    """Mineur 1 : un client qui ne lit plus remplit sa file, est marqué `dropped` et retiré."""
    monkeypatch.setattr("herdr_web.server.SSE_QUEUE", 4)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    tls = ctx.wrap_socket(raw)
    tls.sendall((f"GET /api/stream HTTP/1.1\r\nHost: {HOST}\r\nCookie: hw={TOKEN}\r\n\r\n").encode())
    try:
        assert _attendre(lambda: len(srv.clients) == 1)
        files = list(srv.clients)
        gros = {"x": "y" * 100_000}
        for _ in range(400):  # le client ne lit rien : le tampon TCP se remplit, puis sa file
            srv.broadcast("chat", gros)
            if not srv.clients:
                break
        assert not srv.clients and files[0].dropped is True
    finally:
        tls.close()


def _attendre(cond, t=5.0):
    fin = time.time() + t
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_login_ne_lit_pas_plus_de_4_ko(srv):
    """Mineur 2 : /login est lu avant toute authentification : un Content-Length énorme est refusé
    sans attendre le corps (avant : jusqu'à 12 Mo lus, ou attente du corps jusqu'au délai)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", srv.server_address[1], context=ctx, timeout=4)
    c.putrequest("POST", "/login", skip_host=True, skip_accept_encoding=True)
    for k, v in (("Host", HOST), ("Origin", f"https://{HOST}"), ("Content-Type", "application/json"),
                 ("Content-Length", str(5 * 1024 * 1024))):
        c.putheader(k, v)
    c.endheaders()
    c.send(b'{"token": "x"')  # le reste n'arrive jamais
    r = c.getresponse()
    assert r.status == 413
    r2, _ = req(srv, "POST", "/login", {"token": "x" * 5000}, cookie=False)
    assert r2.status == 413
    r3, _ = req(srv, "POST", "/login", {"token": TOKEN}, cookie=False)
    assert r3.status == 200


def test_connexions_simultanees_bornees(cert, tmp_path, monkeypatch):
    """Mineur 2 : au-delà de MAX_CONNEXIONS, une connexion est fermée tout de suite ; une place
    libérée resert."""
    monkeypatch.setattr("herdr_web.server.MAX_CONNEXIONS", 2)
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    threading.Thread(target=s.serve_forever, daemon=True).start()
    muettes = []
    try:
        for _ in range(2):  # ouvertes et muettes : chacune tient une place (PEEK_TIMEOUT)
            muettes.append(socket.create_connection(("127.0.0.1", s.server_address[1]), timeout=5))
        time.sleep(0.3)
        de_trop = socket.create_connection(("127.0.0.1", s.server_address[1]), timeout=3)
        try:
            assert de_trop.recv(1) == b""  # fermée sans être servie
        except ConnectionResetError:
            pass
        de_trop.close()
        for m in muettes:
            m.close()
        assert _attendre(lambda: s.places._value == 2)
        r, _ = req(s, "GET", "/api/agents")
        assert r.status == 200
    finally:
        for m in muettes:
            m.close()
        s.shutdown()
        s.server_close()



def test_rattrapage_le_fil_de_suivi_survit_a_une_exception(cert, tmp_path, monkeypatch, caplog):
    """Une re-résolution qui lève une fois ne doit pas arrêter le fil de suivi : la passe suivante
    tourne (avant : le fil mourait sans un mot, et plus aucun fil n'arrivait en direct)."""
    import logging
    monkeypatch.setattr("herdr_web.server.CHAT_POLL", 0.02)
    hc, board = FakeHc(), FakeBoard([agent()])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    passes = []

    def reresoudre():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("imprévu")

    s._reresoudre = reresoudre
    try:
        with caplog.at_level(logging.ERROR, logger="herdr_web.server"):
            assert _attendre(lambda: len(passes) >= 3)
        assert s.watcher.is_alive()
        assert any("passe de suivi" in r.getMessage() and r.exc_info for r in caplog.records)
    finally:
        s.stopping.set()
        s.watcher.join(5)
        s.server_close()


def test_depot_de_photo_sans_envoi(srv):
    """Plusieurs photos : chacune déposée (route put) rend sa référence, sans rien taper chez
    l'agent ; la page envoie ensuite un seul message qui les cite toutes."""
    body = (b"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"b.jpg\"\r\n"
            b"Content-Type: image/jpeg\r\n\r\nJPG\r\n--B--\r\n")
    r, out = req(srv, "POST", "/api/agents/t1/put", body,
                 {"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status == 200
    d = json.loads(out)
    put = [p for m, p in srv.hc_fake.calls if m == "terminal.put"][0]
    assert put["name"] == "b.jpg" and d["ref"].startswith("[image : ") and d["ref"].endswith("]")
    assert not [m for m, p in srv.hc_fake.calls if m == "terminal.send"]
    r, _ = req(srv, "POST", "/api/agents/t1/put", body.replace(b"image/jpeg", b"text/plain"),
               {"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status == 415


# -- vue terminal en couleurs : hc attach --observe relayé en SSE --

def _ecran_ouvrir(s):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", s.server_address[1], context=ctx, timeout=10)
    c.request("GET", "/api/agents/t1/ecran", headers={"Host": HOST, "Cookie": f"hw={TOKEN}"})
    return c, c.getresponse()


def _faux_attach(s, script):
    import subprocess as sp
    import sys
    vus = []

    def popen(argv, **kw):
        vus.append(argv)
        p = sp.Popen([sys.executable, "-c", script], **kw)
        vus.append(p)
        return p
    s.popen = popen
    return vus


def test_ecran_relaie_les_octets_colores_puis_fin(srv):
    import base64
    vus = _faux_attach(srv, "import sys; sys.stdout.write('\\x1b[31mrouge\\x1b[0m'); sys.stdout.flush()")
    c, r = _ecran_ouvrir(srv)
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    corps = r.read().decode()  # le flux se termine avec le processus
    c.close()
    assert vus[0] == ["hc", "attach", "--observe", "--history", "300", "t1"]  # observe : jamais une touche
    assert 'event: taille\ndata: {"cols": 120, "rows": 40}' in corps
    octets = b"".join(base64.b64decode(l[6:]) for l in corps.splitlines() if l.startswith("data: ") and not l.startswith("data: {"))
    assert b"\x1b[31mrouge\x1b[0m" in octets
    assert "event: fin" in corps
    assert vus[1].poll() is not None


def test_ecran_tue_le_processus_quand_la_page_coupe(srv):
    import time
    srv.ecran_ping = 0.2  # une écriture par 0,2 s : la coupure se voit vite
    vus = _faux_attach(srv, "import time; time.sleep(30)")
    c, r = _ecran_ouvrir(srv)
    assert r.status == 200
    r.fp.readline()  # event: taille
    r.close()  # la réponse tient le socket : sans elle, c.close() ne coupe rien
    c.close()
    fin = time.time() + 6
    while time.time() < fin and vus[1].poll() is None:
        time.sleep(0.1)
    assert vus[1].poll() is not None, "hc attach doit mourir avec la page"
    fin = time.time() + 3  # la place est rendue
    while time.time() < fin and srv.ecrans._value != 8:
        time.sleep(0.1)
    assert srv.ecrans._value == 8


def test_ecran_borne_le_nombre_de_vues(srv):
    for _ in range(8):
        srv.ecrans.acquire()
    r, _ = req(srv, "GET", "/api/agents/t1/ecran")
    assert r.status == 429
    for _ in range(8):
        srv.ecrans.release()


def test_ouvrir_dans_le_dossier_de_l_agent_d_origine(srv2):
    """L'utilisateur : « un nouveau claude démarre dans /home/user au lieu de là où est le space depuis celui
    que j'ai cliqué » — la page envoie le dossier de l'agent d'origine, il prime sur celui du space."""
    (srv2.home / "depot").mkdir()
    r, b = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "terminal", "folder": str(srv2.home / "depot")})
    assert r.status == 200, b
    argv, _ = srv2.run_fake.appels[0]
    assert argv[5:7] == ["--folder", str(srv2.home / "depot")]


@pytest.mark.parametrize("folder", ["/etc", "relatif", "/nexiste/pas", 7])
def test_ouvrir_refuse_un_dossier_hors_du_home(srv2, folder):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "terminal", "folder": folder})
    assert r.status == 400 and srv2.run_fake.appels == []


def test_choices_porte_le_contexte_de_la_question(srv):
    """AskUserQuestion à plusieurs questions : la carte dit QUELLE question est posée (onglets
    ☐/☒ et texte), et la page s'en sert pour voir que l'écran a tourné."""
    srv.hc_fake.screen = "←  ☒ A  ☐ B  ✔ Submit  →\n\nQuelle taille ?\n\n❯ 1. Petit\n  2. Grand\n"
    r, b = req(srv, "GET", "/api/agents/t1/choices")
    d = json.loads(b)
    assert r.status == 200 and [c["label"] for c in d["choices"]] == ["Petit", "Grand"]
    assert d["contexte"] == "←  ☒ A  ☐ B  ✔ Submit  →\nQuelle taille ?"



def test_choices_panneau_btw_sans_faux_choix(srv):
    """Panneau /btw ouvert (agent `blocked`, « dialog open ») : la liste numérotée de la réponse
    n'est pas un choix ; la page propose de le fermer (2026-11-17)."""
    srv.hc_fake.screen = "   /btw q\n     1. Léa\n     2. Hugo\n\n   c to copy · f to fork · Esc to close\n"
    r, b = req(srv, "GET", "/api/agents/t1/choices")
    d = json.loads(b)
    assert r.status == 200 and d["panneau"] is True
    assert d["choices"] == [] and d["menu"] is None and d["contexte"] == ""

_Q2 = "←  ☒ A  ☐ B  ✔ Submit  →\n\nQuelle taille ?\n\n❯ 1. Petit\n  2. Grand\n"


def test_answer_refuse_un_chiffre_pour_une_question_qui_n_est_plus_a_l_ecran(srv):
    """Réserve D2 de la PR #22 : terminal et page côte à côte, la question 1 répondue dans le
    terminal, l'écran porte la question 2 (même msg, mêmes chiffres) ; la page, restée sur la 1,
    envoie ce qu'elle montre : 409 perime, aucune touche."""
    srv.hc_fake.screen = _Q2
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    vue_q1 = {"contexte": "←  ☐ A  ☐ B  ✔ Submit  →\nQuelle couleur ?", "libelles": ["Rouge", "Vert"]}
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", **vue_q1})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    # mêmes libellés, autre question : le contexte seul suffit à refuser
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m",
                                                      "contexte": vue_q1["contexte"], "libelles": ["Petit", "Grand"]})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    # même question, libellés changés (case cochée d'un multiSelect) : refusé aussi
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m",
                                                      "contexte": "←  ☒ A  ☐ B  ✔ Submit  →\nQuelle taille ?",
                                                      "libelles": ["Petit"]})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    # un chiffre que la question d'avant portait mais pas l'écran : perime, pas 400 (la page réaffiche)
    srv.hc_fake.screen = "Quelle taille ?\n\n1. Petit\n2. Grand\n"
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "contexte": "Quelle couleur ?",
                                                      "libelles": ["Rouge", "Vert", "Bleu"]})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    assert not [c for c in srv.hc_fake.calls if c[0] == "terminal.keys"]


def test_answer_accepte_la_question_que_la_page_montre(srv):
    srv.hc_fake.screen = _Q2
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m",
                                                      "contexte": "←  ☒ A  ☐ B  ✔ Submit  →\nQuelle taille ?",
                                                      "libelles": ["Petit", "Grand"]})
    assert r.status == 200
    assert ("terminal.keys", {"terminal": "t1", "keys": ["2"]}) in srv.hc_fake.calls


@pytest.mark.parametrize("vue", [{"contexte": 7, "libelles": []}, {"contexte": "", "libelles": "Petit"},
                                 {"contexte": "", "libelles": [1, 2]}, {"libelles": ["Petit", "Grand"]},
                                 {"contexte": "Quelle taille ?"}])
def test_answer_signature_mal_formee_400(srv, vue):
    srv.hc_fake.screen = _Q2
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", **vue})
    assert r.status == 400
    assert not [c for c in srv.hc_fake.calls if c[0] == "terminal.keys"]


def test_answer_d_une_page_ancienne_sans_signature_reste_accepte(srv):
    """Compatibilité : une page d'avant la PR #22 (encore en cache) n'envoie que digit et msg ;
    elle garde la garde d'avant (chiffre à l'écran, msg égal)."""
    srv.hc_fake.screen = _Q2
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m"})
    assert r.status == 200
    assert ("terminal.keys", {"terminal": "t1", "keys": ["1"]}) in srv.hc_fake.calls
