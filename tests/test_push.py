import base64
import json
import os
import stat
import types

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException

from herdr_web.agents import Agent
from herdr_web.push import NotifyWorker, Push

SUBJECT = "mailto:herdr-web@localhost"


def pem() -> str:
    """Clé VAPID jetable, en mémoire : jamais écrite."""
    k = ec.generate_private_key(ec.SECP256R1())
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


def sub(n=1):
    return {"endpoint": f"https://push.test/{n}", "keys": {"p256dh": "p" + str(n), "auth": "a" + str(n)}}


def agent(name="claude", title="", addr="t1", msg="Autoriser ?"):
    return Agent(addr, name, title, "/w", "claude-code", 7, "blocked", None, msg, None, None, 0)


class Fake:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    def __call__(self, **kw):
        self.calls.append(kw)
        code = self.fail.get(kw["subscription_info"]["endpoint"])
        if code:
            raise WebPushException("refus", response=types.SimpleNamespace(status_code=code))


@pytest.fixture
def mk(tmp_path):
    def make(fake=None):
        f = fake or Fake()
        return Push(tmp_path / "etat", pem(), SUBJECT, send=f), f
    return make


def stored(tmp_path):
    return json.loads((tmp_path / "etat" / "subscriptions.json").read_text())


def test_subscribe_dedoublonne(mk, tmp_path):
    p, _ = mk()
    p.subscribe(sub(1))
    p.subscribe(sub(2))
    nouveau = sub(1)
    nouveau["keys"]["auth"] = "neuf"
    p.subscribe(nouveau)
    s = stored(tmp_path)
    assert len(s) == 2
    assert [x for x in s if x["endpoint"].endswith("/1")][0]["keys"]["auth"] == "neuf"


def test_fichier_en_600(mk, tmp_path):
    p, _ = mk()
    p.subscribe(sub())
    d = tmp_path / "etat"
    assert stat.S_IMODE((d / "subscriptions.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert os.listdir(d) == ["subscriptions.json"]  # pas de fichier temporaire


def test_notify_appelle_send_par_abonnement(mk):
    p, f = mk()
    p.subscribe(sub(1))
    p.subscribe(sub(2))
    assert p.notify(agent()) == 2
    assert {c["subscription_info"]["endpoint"] for c in f.calls} == {"https://push.test/1",
                                                                      "https://push.test/2"}
    assert all(c["vapid_claims"] == {"sub": SUBJECT} and c["ttl"] == 3600 for c in f.calls)


def test_abonnement_410_retire(mk, tmp_path):
    p, _ = mk(Fake(fail={"https://push.test/1": 410, "https://push.test/2": 404}))
    for n in (1, 2, 3):
        p.subscribe(sub(n))
    assert p.notify(agent()) == 1
    assert [x["endpoint"] for x in stored(tmp_path)] == ["https://push.test/3"]


def test_autre_echec_garde_l_abonnement(mk, tmp_path):
    p, _ = mk(Fake(fail={"https://push.test/1": 500}))
    p.subscribe(sub(1))
    assert p.notify(agent()) == 0
    assert len(stored(tmp_path)) == 1


def test_charge_utile(mk):
    p, f = mk()
    p.subscribe(sub())
    p.notify(agent(name="alpha", msg="Lancer ?"))
    p.notify(agent(name=None, title="titre", addr="t2", msg=""))
    p.notify(agent(name=None, title="", addr="t3", msg="m"))
    charges = [json.loads(c["data"]) for c in f.calls]
    # titre dans la langue de la page : anglais par défaut, le serveur branche p.langue sur ses réglages
    assert charges[0] == {"title": "alpha is waiting", "body": "Lancer ?", "addr": "t1"}
    assert charges[1]["title"] == "titre is waiting" and charges[1]["addr"] == "t2"
    assert charges[2]["title"] == "t3 is waiting"
    p.langue = lambda: "fr"
    p.notify(agent(name="alpha", msg="Lancer ?"))
    assert json.loads(f.calls[-1]["data"]) == {"title": "alpha attend", "body": "Lancer ?", "addr": "t1"}


def test_cle_publique_base64url_non_compressee(mk):
    p, _ = mk()
    k = p.public_key()
    raw = base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))
    assert len(raw) == 65 and raw[0] == 4 and "=" not in k


def test_pem_illisible_refuse_sans_la_valeur(tmp_path):
    with pytest.raises(ValueError) as e:
        Push(tmp_path, "SECRET-PAS-UN-PEM", SUBJECT, send=Fake())
    assert "SECRET-PAS-UN-PEM" not in str(e.value)


def test_cle_privee_jamais_ecrite(mk, tmp_path):
    p, _ = mk()
    p.subscribe(sub())
    assert b"PRIVATE" not in (tmp_path / "etat" / "subscriptions.json").read_bytes()


def test_fil_de_notification_isole_les_exceptions(mk):
    p, _ = mk()
    appels = []

    def notify(a):
        appels.append(a.address)
        if a.address == "t1":
            raise RuntimeError("boum")
        return 1

    p.notify = notify
    w = NotifyWorker(p)
    w.submit(agent(addr="t1"))
    w.submit(agent(addr="t2"))
    assert w.join(timeout=3) and appels == ["t1", "t2"]  # t1 a levé, t2 est passé
    w.stop()


def test_make_push_sans_cle_desactive(tmp_path):
    from herdr_web.__main__ import make_push

    class Board:
        cbs = []

        def on_blocked(self, cb):
            self.cbs.append(cb)

    b = Board()
    assert make_push({"HERDR_WEB_STATE": str(tmp_path)}, b) is None and b.cbs == []


def test_make_push_cle_illisible_refuse(tmp_path):
    from herdr_web.__main__ import make_push
    with pytest.raises(ValueError) as e:
        make_push({"HERDR_WEB_VAPID_KEY": "PAS-UN-PEM-SECRET", "HERDR_WEB_STATE": str(tmp_path)}, object())
    assert "PAS-UN-PEM-SECRET" not in str(e.value)


def test_make_push_branche_on_blocked(tmp_path):
    from herdr_web.__main__ import make_push

    class Board:
        cbs = []

        def on_blocked(self, cb):
            self.cbs.append(cb)

    b = Board()
    p = make_push({"HERDR_WEB_VAPID_KEY": pem(), "HERDR_WEB_STATE": str(tmp_path / "s")}, b)
    assert p is not None and len(b.cbs) == 1
    sent = []
    p._send = lambda **kw: sent.append(kw)
    p.subscribe(sub())
    b.cbs[0](agent())  # le rappel ne bloque pas : l'envoi part du fil dédié
    import time
    for _ in range(200):
        if sent:
            break
        time.sleep(0.01)
    assert len(sent) == 1 and sent[0]["vapid_claims"] == {"sub": "mailto:herdr-web@localhost"}


def test_send_porte_un_timeout(mk):
    p, f = mk()
    p.subscribe(sub())
    p.notify(agent())
    assert f.calls[0]["timeout"] == 10


def test_plafond_20_abonnements_plus_ancien_evince(mk, tmp_path):
    p, _ = mk()
    for n in range(1, 22):
        p.subscribe(sub(n))
    eps = [x["endpoint"] for x in stored(tmp_path)]
    assert len(eps) == 20 and "https://push.test/1" not in eps and eps[-1].endswith("/21")
    p.subscribe(sub(2))  # réabonnement : remis en tête, rien d'évincé
    eps = [x["endpoint"] for x in stored(tmp_path)]
    assert len(eps) == 20 and eps[-1].endswith("/2") and eps[0].endswith("/3")


def test_fichier_corrompu_mis_de_cote(mk, tmp_path, caplog):
    p, _ = mk()
    p.subscribe(sub(1))
    f = tmp_path / "etat" / "subscriptions.json"
    f.write_text("{pas du json SECRET")
    with caplog.at_level("WARNING"):
        p.subscribe(sub(2))
    c = tmp_path / "etat" / "subscriptions.json.corrompu"
    assert c.read_text() == "{pas du json SECRET" and stat.S_IMODE(c.stat().st_mode) == 0o600
    assert [x["endpoint"] for x in stored(tmp_path)] == ["https://push.test/2"]
    assert "corrompu" in caplog.text and "SECRET" not in caplog.text


def test_file_pleine_n_abandonne_sans_bloquer(mk, caplog):
    import threading
    p, _ = mk()
    gate = threading.Event()
    p.notify = lambda a: gate.wait(5)
    w = NotifyWorker(p)
    with caplog.at_level("WARNING"):
        for i in range(150):
            w.submit(agent(addr=f"t{i}"))  # ne doit jamais bloquer
    assert "pleine" in caplog.text
    gate.set()
    assert w.join(timeout=5)
    w.stop()


def test_sujet_https_signe_pour_de_vrai_mais_pas_avec_un_port():
    """py_vapid (non remplacé) : https://hôte passe, https://hôte:port est rejeté (mesuré le 2026-11-14)."""
    from py_vapid import Vapid02, VapidException
    v = Vapid02()
    v.generate_keys()
    claims = {"aud": "https://push.test", "exp": 9999999999}
    assert "Authorization" in v.sign({**claims, "sub": "https://herdr.example.org"})
    with pytest.raises(VapidException):
        v.sign({**claims, "sub": "https://herdr.example.org:10370"})
