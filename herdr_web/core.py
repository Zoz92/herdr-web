"""Client JSON-RPC pour herdr-core (`hc connect`, un objet JSON par ligne)."""
import json
import logging
import queue
import subprocess
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable


class HcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class HcGone(Exception):
    """Le processus herdr-core est mort ou injoignable."""


class HcTimeout(HcGone):
    """La requête n'a pas reçu de réponse à temps (la connexion peut vivre)."""


log = logging.getLogger(__name__)


def spawn_hc_connect():
    return subprocess.Popen(
        ["hc", "connect"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1,
    )


class _Channel:
    """Une connexion `hc connect` : son processus, ses requêtes en vol, sa reconnexion."""

    def __init__(self, client, name, subscribe):
        self._c = client
        self.name = name
        self.subscribe = subscribe
        self.ready = False
        self._proc = None
        self._next_id = 0
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._attempt = 0
        self._gen = 0   # change à chaque mort de connexion, sous self._lock

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _send(self, method, params, proc, timeout):
        fut = Future()
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._pending[rid] = fut
            try:
                proc.stdin.write(json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
                proc.stdin.flush()
            except Exception as e:
                self._pending.pop(rid, None)
                raise HcGone(str(e))
        try:
            msg = fut.result(timeout=timeout)
        except FutureTimeout:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if isinstance(msg, HcGone):
            raise msg
        if "error" in msg:
            e = msg["error"]
            raise HcError(e.get("code"), e.get("message", ""), e.get("data"))
        return msg.get("result")

    def _handshake(self, proc, gen):
        try:
            self._send("hello", {}, proc, 10)
            if self.subscribe:
                self._send("events.subscribe", {}, proc, 10)
        except Exception as e:
            log.warning("poignée de main herdr-core échouée (canal %s) : %s", self.name, e)
            try:
                proc.kill()
            except Exception:
                log.exception("kill du processus herdr-core")
            return
        with self._lock:
            # une poignée de main tardive ne ressuscite pas une connexion déjà morte
            if gen != self._gen or self._proc is not proc or proc.poll() is not None:
                return
            self._attempt = 0
            self.ready = True
        self._c._refresh_state()

    def _run(self):
        while not self._c._stopped:
            try:
                proc = self._c._spawn()
            except Exception:
                proc = None
            if proc is not None:
                with self._lock:
                    self._proc = proc
                    gen = self._gen
                threading.Thread(target=self._handshake, args=(proc, gen), daemon=True).start()
                self._read(proc)
                with self._lock:
                    self.ready = False
                    self._proc = None
                    self._gen += 1
                self._c._refresh_state()
                self._cleanup(proc)
                with self._lock:
                    pend, self._pending = self._pending, {}
                for f in pend.values():
                    f.set_result(HcGone("processus herdr-core terminé"))
            delay = self._c.backoff[min(self._attempt, len(self._c.backoff) - 1)]
            self._attempt += 1
            time.sleep(delay)

    def _cleanup(self, proc):
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            log.warning("wait du processus herdr-core a échoué")

    def _read(self, proc):
        while True:
            try:
                line = proc.stdout.readline()
            except Exception:
                return
            if not line:
                return
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    fut = self._pending.pop(msg["id"], None)
                if fut:
                    fut.set_result(msg)
            elif "method" in msg and self.subscribe:
                self._c._events.put(msg)

    def request(self, method, params, timeout):
        proc = self._proc
        if not self.ready or proc is None:
            raise HcGone("herdr-core déconnecté")
        try:
            return self._send(method, params, proc, timeout)
        except FutureTimeout:
            raise HcTimeout(f"délai dépassé pour {method}")


class HcClient:
    """Deux connexions : une abonnée aux événements (elle ne sert plus de requêtes une fois
    abonnée), une pour les requêtes."""

    def __init__(self, spawn: Callable = spawn_hc_connect):
        self._spawn = spawn
        self.backoff = [2, 5, 10, 30]
        self.connected = False
        self._state_lock = threading.Lock()
        self._state_q: queue.Queue = queue.Queue()
        self._last_broadcast = False
        self._epoch = 0           # +1 à chaque transition True -> False, sous _state_lock
        self._last_epoch = 0      # époque du dernier état diffusé
        self._event_cbs = []
        self._state_cbs = []
        self._first = threading.Event()
        self._stopped = False
        self._events: queue.Queue = queue.Queue()
        self._dispatcher = None
        self._ev_chan = _Channel(self, "evenements", True)
        self._rq_chan = _Channel(self, "requetes", False)

    def on_event(self, cb):
        self._event_cbs.append(cb)

    def on_state(self, cb):
        self._state_cbs.append(cb)

    def start(self):
        self._dispatcher = threading.Thread(target=self._dispatch, daemon=True)
        self._dispatcher.start()
        threading.Thread(target=self._notify, daemon=True).start()
        self._ev_chan.start()
        self._rq_chan.start()
        self._first.wait(timeout=30)

    # -- interne --
    def _compute_state(self):
        """Recalcule `connected` sous le verrou : jamais un ok périmé."""
        with self._state_lock:
            ok = self._ev_chan.ready and self._rq_chan.ready
            if self.connected and not ok:
                self._epoch += 1
            self.connected = ok
            return ok

    def _refresh_state(self):
        if self._compute_state():
            self._first.set()   # les rappels partent d'un autre fil : ils ne bloquent pas start()
        self._state_q.put(None)

    def _notify(self):
        """Un seul fil appelle les rappels, dans l'ordre, sur l'état relu juste avant."""
        while True:
            self._state_q.get()
            with self._state_lock:
                ok = self._ev_chan.ready and self._rq_chan.ready
                if self.connected and not ok:
                    self._epoch += 1
                self.connected = ok
                epoch = self._epoch
            seq = []
            if ok and self._last_broadcast and epoch != self._last_epoch:
                # une coupure est passée pendant un rappel lent : la rediffuser, pour que
                # le consommateur se rafraîchisse (événements perdus pendant la coupure)
                seq = [False, True]
            elif ok != self._last_broadcast:
                seq = [ok]
            self._last_broadcast = ok
            self._last_epoch = epoch
            for v in seq:
                for cb in list(self._state_cbs):
                    try:
                        cb(v)
                    except Exception:
                        log.exception("callback on_state a levé")

    def _dispatch(self):
        """Regroupe les rafales : tout ce qui attend dans la file forme un lot, et chaque méthode
        n'y est livrée qu'une fois (son dernier message). Sans cela, 20 événements donnaient 20
        rafraîchissements complets du tableau, et la file grossissait sans borne."""
        while True:
            lot = [self._events.get()]
            while True:
                try:
                    lot.append(self._events.get_nowait())
                except queue.Empty:
                    break
            derniers = {}
            for msg in lot:
                cle = msg.get("method") if isinstance(msg, dict) else None
                derniers.pop(cle, None)
                derniers[cle] = msg  # ordre : celui de la dernière occurrence
            for msg in derniers.values():
                for cb in list(self._event_cbs):
                    try:
                        cb(msg)
                    except Exception:
                        log.exception("callback on_event a levé")

    def request(self, method, params, timeout=100.0):
        if not self.connected:
            raise HcGone("herdr-core déconnecté")
        return self._rq_chan.request(method, params, timeout)
