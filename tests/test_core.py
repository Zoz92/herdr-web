import json
import queue
import threading
import time

import pytest

import logging

from herdr_web.core import HcClient, HcError, HcGone, HcTimeout, _Channel


class FakeProc:
    """Processus factice fidèle à hc : une connexion qui a reçu events.subscribe ne répond
    plus aux requêtes (mesuré sur le vrai hc). hello et events.subscribe sont répondus seuls
    quand auto=True ; les autres requêtes vont dans `sent` (et reçoivent leur réponse de `feed`)."""

    def __init__(self, auto=True, echo=False, die_after_subscribe=False):
        self.auto = auto
        self.echo = echo          # répond aux requêtes d'une connexion non abonnée
        self.die_after_subscribe = die_after_subscribe
        self.subscribed = False
        self.sent = queue.Queue()      # requêtes hors poignée de main (ou tout, si auto=False)
        self._out = queue.Queue()      # lignes lues par le client
        self.dead = False
        self.closed = False
        self.killed = False
        self.waited = False
        self.stdin = self
        self.stdout = self

    # stdin
    def write(self, s):
        for line in s.splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            if self.auto and msg["method"] == "hello":
                self.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {"version": "0.1.21"}})
            elif self.auto and msg["method"] == "events.subscribe":
                self.subscribed = True
                self.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                if self.die_after_subscribe:
                    self.die()         # meurt juste après sa réponse
            elif self.subscribed:
                continue               # fidèle : une connexion abonnée ne répond plus aux requêtes
            elif self.echo:
                self.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {"terminals": []}})
            else:
                self.sent.put(msg)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def wait(self, timeout=None):
        self.waited = True
        return 1

    # stdout
    def readline(self):
        return self._out.get()

    def __iter__(self):
        return iter(self.readline, "")

    # contrôle
    def feed(self, obj):
        self._out.put(json.dumps(obj) + "\n")

    def poll(self):
        return 1 if self.dead else None

    def kill(self):
        self.killed = True
        self.die()

    def die(self):
        self.dead = True
        self._out.put("")

    def next_sent(self, timeout=3):
        return self.sent.get(timeout=timeout)


def wait_for(cond, t=5):
    end = time.time() + t
    while not cond() and time.time() < end:
        time.sleep(0.01)
    return cond()


def make_client(procs=None, delays=None, auto=True):
    procs = procs if procs is not None else []

    def spawn():
        p = FakeProc(auto=auto)
        procs.append(p)
        return p

    c = HcClient(spawn=spawn)
    if delays is not None:
        c.backoff = delays
    return c, procs


def roles(procs):
    """(canal d'événements, canal de requêtes) parmi les processus lancés."""
    ev = next((p for p in procs if p.subscribed), None)
    rq = next((p for p in procs if not p.subscribed), None)
    return ev, rq


def started(c, procs):
    """Démarre ; rend le processus du canal de requêtes."""
    c.start()
    assert c.connected
    return roles(procs)[1]


def started_both(c, procs):
    c.start()
    assert c.connected
    return roles(procs)


def test_request_rend_le_result():
    c, procs = make_client()
    p = started(c, procs)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.request("x.y", {"a": 1})))
    t.start()
    msg = p.next_sent()
    assert msg["method"] == "x.y" and msg["params"] == {"a": 1}
    p.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})
    t.join(3)
    assert out["r"] == {"ok": True}


def test_request_leve_hcerror():
    c, procs = make_client()
    p = started(c, procs)
    out = {}

    def run():
        try:
            c.request("x.y", {})
        except HcError as e:
            out["e"] = e

    t = threading.Thread(target=run)
    t.start()
    msg = p.next_sent()
    p.feed({"jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32602, "message": "missing field"}})
    t.join(3)
    assert out["e"].code == -32602
    assert "missing field" in out["e"].message


def test_ids_croissants_et_apparies():
    c, procs = make_client()
    p = started(c, procs)
    res = {}
    ts = [threading.Thread(target=lambda n=n: res.update({n: c.request("m", {"n": n})}))
          for n in (1, 2)]
    m1 = None
    ts[0].start()
    m1 = p.next_sent()
    ts[1].start()
    m2 = p.next_sent()
    assert m2["id"] > m1["id"]
    # réponses inversées
    for m in (m2, m1):
        p.feed({"jsonrpc": "2.0", "id": m["id"], "result": {"echo": m["params"]["n"]}})
    for t in ts:
        t.join(3)
    assert res == {1: {"echo": 1}, 2: {"echo": 2}}


def test_notification_event_va_au_callback():
    c, procs = make_client()
    got = queue.Queue()
    c.on_event(got.put)
    ev, rq = started_both(c, procs)
    ev.feed({"jsonrpc": "2.0", "method": "event", "params": {"k": "v"}})
    m = got.get(timeout=3)
    assert m["method"] == "event" and m["params"] == {"k": "v"}


def test_requete_apres_abonnement_repond():
    """Régression : le vrai hc ne répond plus aux requêtes d'une connexion abonnée."""
    c, procs = make_client()
    ev, rq = started_both(c, procs)
    assert ev is not rq and ev.subscribed and not rq.subscribed
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.request("terminal.list", {}, timeout=3)))
    t.start()
    m = rq.next_sent()
    assert m["method"] == "terminal.list"
    rq.feed({"jsonrpc": "2.0", "id": m["id"], "result": {"terminals": []}})
    t.join(3)
    assert out["r"] == {"terminals": []}
    assert ev.sent.empty()


def test_start_rapide():
    c, procs = make_client()
    t0 = time.time()
    c.start()
    assert time.time() - t0 < 2
    assert c.connected


def test_on_state_ne_bloque_pas_start():
    """Un rappel on_state qui fait une requête (comme AgentBoard) ne retarde pas start()."""
    c, procs = make_client()

    def cb(ok):
        if ok:
            try:
                c.request("terminal.list", {}, timeout=1.5)
            except Exception:
                pass

    c.on_state(cb)
    t0 = time.time()
    c.start()
    assert time.time() - t0 < 1.0


def test_reconnexion_canal_evenements_rappelle_on_state():
    c, procs = make_client(delays=[0.05])
    states = []
    c.on_state(states.append)
    ev, rq = started_both(c, procs)
    assert wait_for(lambda: states == [True])
    states.clear()
    ev.die()
    assert wait_for(lambda: states == [False, True])
    assert wait_for(lambda: len(procs) == 3 and procs[2].subscribed)
    # le canal de requêtes reste utilisable
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.request("q", {}, timeout=3)))
    t.start()
    m = rq.next_sent()
    rq.feed({"jsonrpc": "2.0", "id": m["id"], "result": {"r": 1}})
    t.join(3)
    assert out["r"] == {"r": 1}
    assert not rq.dead


def test_mort_du_canal_requetes_reconnecte():
    c, procs = make_client(delays=[0.05])
    states = []
    c.on_state(states.append)
    ev, rq = started_both(c, procs)
    assert wait_for(lambda: states == [True])
    states.clear()
    rq.die()
    assert wait_for(lambda: states == [False, True])
    assert not ev.dead
    assert len(procs) == 3


def test_requetes_en_vol_hcgone_si_canal_requetes_meurt():
    c, procs = make_client(delays=[30])
    ev, rq = started_both(c, procs)
    out = {}

    def run():
        try:
            c.request("lent", {}, timeout=5)
        except Exception as e:
            out["e"] = e

    t = threading.Thread(target=run)
    t.start()
    rq.next_sent()
    rq.die()
    t.join(3)
    assert isinstance(out.get("e"), HcGone)
    assert not isinstance(out["e"], HcTimeout)


def test_request_pendant_deconnexion_leve_hcgone():
    c, procs = make_client(delays=[30])
    ev, rq = started_both(c, procs)
    rq.die()
    assert wait_for(lambda: not c.connected, 3)
    with pytest.raises(HcGone):
        c.request("x.y", {}, timeout=1)


def test_echec_du_hello_tue_et_reconnecte(caplog):
    c, procs = make_client(delays=[0.05], auto=False)
    th = threading.Thread(target=c.start, daemon=True)
    with caplog.at_level(logging.WARNING):
        th.start()
        assert wait_for(lambda: len(procs) >= 2)
        p1 = procs[0]
        m = p1.next_sent()
        p1.feed({"jsonrpc": "2.0", "id": m["id"],
                 "error": {"code": -1, "message": "boom"}})
        assert wait_for(lambda: p1.killed)
        assert wait_for(lambda: len(procs) >= 3)
    assert any("boom" in r.getMessage() or "hello" in r.getMessage() for r in caplog.records)


def test_processus_mort_est_nettoye():
    c, procs = make_client(delays=[0.05])
    ev, rq = started_both(c, procs)
    ev.die()
    assert wait_for(lambda: ev.closed and ev.waited)


def test_callback_qui_leve_est_journalise_et_suivant_appele(caplog):
    c, procs = make_client()
    got = queue.Queue()

    def bad(ev):
        raise ValueError("cb-casse")

    c.on_event(bad)
    c.on_event(got.put)
    with caplog.at_level(logging.ERROR):
        ev, rq = started_both(c, procs)
        ev.feed({"jsonrpc": "2.0", "method": "event", "params": {}})
        got.get(timeout=3)
    assert any("cb-casse" in (r.exc_text or "") or "callback" in r.getMessage()
               for r in caplog.records)


def test_callback_event_peut_appeler_request():
    c, procs = make_client()
    res = queue.Queue()
    c.on_event(lambda e: res.put(c.request("q", {}, timeout=3)))
    ev, rq = started_both(c, procs)
    ev.feed({"jsonrpc": "2.0", "method": "event", "params": {}})
    m = rq.next_sent()
    assert m["method"] == "q"
    rq.feed({"jsonrpc": "2.0", "id": m["id"], "result": {"r": 1}})
    assert res.get(timeout=3) == {"r": 1}


def test_timeout_leve_hctimeout():
    c, procs = make_client()
    started(c, procs)
    with pytest.raises(HcTimeout):
        c.request("lent", {}, timeout=0.1)
    assert c.connected
    assert issubclass(HcTimeout, HcGone)


def test_requete_aboutit_sur_faux_qui_repond_hors_abonnement():
    """Rouge sur l'ancien code par HcTimeout (une seule connexion, abonnée donc muette)."""
    procs = []

    def spawn():
        p = FakeProc(echo=True)
        procs.append(p)
        return p

    c = HcClient(spawn=spawn)
    c.start()
    assert c.request("terminal.list", {}, timeout=1.5) == {"terminals": []}


def test_poignee_tardive_ne_ressuscite_pas_un_canal_mort():
    """hc meurt juste après sa réponse à events.subscribe : le canal n'est pas prêt."""
    procs = []
    n = {"ev": 0}

    def spawn():
        # les connexions abonnantes sont les 1re (événements) et celles qui suivent la mort
        p = FakeProc(echo=True)
        procs.append(p)
        return p

    c = HcClient(spawn=spawn)
    c.backoff = [0.5]
    states = []
    c.on_state(states.append)
    c.start()
    ev, rq = roles(procs)
    assert wait_for(lambda: states == [True])
    states.clear()

    # la reconnexion du canal d'événements meurt aussitôt après events.subscribe
    def spawn2():
        p = FakeProc(echo=True, die_after_subscribe=(n["ev"] == 0))
        procs.append(p)
        return p

    c._spawn = spawn2
    ev.die()
    assert wait_for(lambda: len(procs) >= 3, 3)
    assert wait_for(lambda: procs[2].dead, 3)
    time.sleep(0.15)
    assert not c.connected          # canal mort : jamais « connecté »
    n["ev"] = 1
    assert wait_for(lambda: len(procs) >= 4 and c.connected, 5)
    assert states[0] is False and states[-1] is True


class _LecturePiegee(_Channel):
    """`ready` : lire False depuis le fil « A » dort (préemption entre la lecture et le verrou)."""

    @property
    def ready(self):
        v = self.__dict__.get("_r", False)
        if not v and threading.current_thread().name == "A":
            time.sleep(0.3)
        return v

    @ready.setter
    def ready(self, v):
        self.__dict__["_r"] = v


def test_etat_calcule_sous_verrou_pas_de_faux_ok():
    """Un ok=False périmé (lu avant que le canal de requêtes devienne prêt) ne doit pas écraser
    connected=True : l'état est calculé sous le verrou."""
    c = HcClient(spawn=lambda: None)
    c._rq_chan.__class__ = _LecturePiegee
    c._ev_chan.ready = True
    a = threading.Thread(target=c._compute_state, name="A")
    a.start()
    time.sleep(0.1)                       # A a lu rq.ready=False et dort
    c._rq_chan.ready = True
    c._compute_state()
    a.join(3)
    assert c._ev_chan.ready and c._rq_chan.ready
    assert c.connected


def test_coupure_pendant_un_rappel_lent_est_rediffusee():
    """Un rappel on_state(True) lent est en cours ; le canal d'événements meurt puis revient :
    un nouvel on_state(True) doit suivre (le consommateur se rafraîchit)."""
    c, procs = make_client(delays=[0.05])
    gate = threading.Event()
    states = []

    def cb(ok):
        states.append(ok)
        if len(states) == 1:
            gate.wait(5)                  # rappel lent : AgentBoard.refresh

    c.on_state(cb)
    ev, rq = started_both(c, procs)
    assert wait_for(lambda: states == [True])
    ev.die()
    assert wait_for(lambda: len(procs) == 3 and c.connected and c._ev_chan.ready)
    gate.set()
    assert wait_for(lambda: states.count(True) >= 2)
    assert states[-1] is True


def test_rappels_on_state_en_ordre_et_sans_doublon():
    c, procs = make_client(delays=[0.05])
    states = []
    c.on_state(states.append)
    ev, rq = started_both(c, procs)
    assert wait_for(lambda: states == [True])
    rq.die()
    assert wait_for(lambda: states == [True, False, True])


def test_dispatch_regroupe_une_rafale_en_un_tour():
    """I1 (relecture finale) : 20 événements en file = un seul tour de rappels, pas 20."""
    c = HcClient(spawn=lambda: None)
    vus = []
    c.on_event(vus.append)
    for i in range(20):
        c._events.put({"jsonrpc": "2.0", "method": "event", "params": {"n": i}})
    c._events.put({"jsonrpc": "2.0", "method": "missed", "params": {}})
    t = threading.Thread(target=c._dispatch, daemon=True)
    t.start()
    assert wait_for(lambda: len(vus) >= 2)
    time.sleep(0.2)
    assert [m["method"] for m in vus] == ["event", "missed"]
    assert vus[0]["params"] == {"n": 19}  # le dernier de sa méthode
    c._events.put({"jsonrpc": "2.0", "method": "event", "params": {"n": 20}})
    assert wait_for(lambda: len(vus) == 3)  # le lot suivant est bien servi
