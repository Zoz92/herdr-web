"""Notifications Web Push : abonnements (fichier 600), envoi, fil d'envoi hors du chemin de refresh."""
import base64
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from herdr_web import textes

log = logging.getLogger(__name__)

TTL = 3600
SEND_TIMEOUT = 10  # secondes (requests.post) ; sans cela, webpush transmet None = aucun délai
MAX_SUBS = 20
QUEUE_MAX = 100
GONE = (404, 410)


class Push:
    def __init__(self, state_dir, vapid_private_pem: str, subject: str, send=webpush):
        self._dir = Path(state_dir)
        self._file = self._dir / "subscriptions.json"
        self._subject = subject
        self._send = send
        self._lock = threading.Lock()
        # langue du titre des notifications : le serveur la branche sur ses réglages (Server.__init__)
        self.langue = lambda: textes.DEFAUT
        try:
            self._vapid = Vapid.from_pem(vapid_private_pem.encode())
            raw = self._vapid.public_key.public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        except Exception:  # jamais le détail : il pourrait citer la valeur
            raise ValueError("clé privée VAPID illisible (PEM attendu)") from None
        self._public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def public_key(self) -> str:
        return self._public

    # -- stockage (appelé sous self._lock) --
    def _read(self) -> list[dict]:
        try:
            raw = self._file.read_text()
        except OSError:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except ValueError:
            pass
        # corrompu : mis de côté (jamais écrasé), sans en journaliser le contenu
        log.warning("subscriptions.json illisible : mis de côté en .corrompu")
        try:
            os.chmod(self._file, 0o600)
            os.replace(self._file, self._file.with_name("subscriptions.json.corrompu"))
        except OSError:
            log.warning("mise de côté de subscriptions.json impossible")
        return []

    def _write(self, subs: list[dict]) -> None:
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._dir / f".subscriptions.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(subs, f)
            os.replace(tmp, self._file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def subscribe(self, sub: dict) -> None:
        with self._lock:
            subs = [s for s in self._read() if s.get("endpoint") != sub["endpoint"]]
            subs.append(sub)
            subs = subs[-MAX_SUBS:]  # le plus ancien est évincé
            self._write(subs)

    def appareils(self) -> list[str]:
        """Service de push de chaque abonnement (hôte de l'endpoint seul : le reste identifie l'appareil)."""
        with self._lock:
            subs = self._read()
        out = []
        for sub in subs:
            ep = sub.get("endpoint") if isinstance(sub, dict) else None
            host = urlsplit(ep).hostname if isinstance(ep, str) else None
            out.append(host or "?")
        return out

    def _remove(self, endpoints: set[str]) -> None:
        with self._lock:
            self._write([s for s in self._read() if s.get("endpoint") not in endpoints])

    def notify(self, agent) -> int:
        with self._lock:
            subs = self._read()
        nom = getattr(agent, 'label', '') or agent.name or agent.title or agent.address
        data = json.dumps({"title": textes.t(self.langue(), "push.attend", nom=nom),
                           "body": agent.msg, "addr": agent.address}, ensure_ascii=False)
        ok, gone = 0, set()
        for sub in subs:
            try:
                self._send(subscription_info=sub, data=data, vapid_private_key=self._vapid,
                           vapid_claims={"sub": self._subject}, ttl=TTL,
                           timeout=SEND_TIMEOUT)
                ok += 1
            except WebPushException as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in GONE:
                    gone.add(sub.get("endpoint"))
                else:
                    log.warning("envoi push refusé (code %s)", code)
            except Exception as e:  # réseau, etc. : ni abonnement ni clé dans le journal
                log.warning("envoi push impossible (%s)", type(e).__name__)
        if gone:
            self._remove(gone)
        return ok


class NotifyWorker:
    """Consomme une file dans un fil daemon : le rappel de AgentBoard ne fait que `submit`."""

    def __init__(self, push: Push):
        self._push = push
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = object()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def submit(self, agent) -> None:
        try:
            self._q.put_nowait(agent)
        except queue.Full:  # jamais bloquant pour le rappel de AgentBoard
            log.warning("file de notifications pleine : %s abandonné", agent.address)

    def _run(self):
        while True:
            a = self._q.get()
            try:
                if a is self._stop:
                    return
                self._push.notify(a)
            except Exception as e:  # le fil ne doit jamais mourir
                log.warning("notification push échouée (%s)", type(e).__name__)
            finally:
                self._q.task_done()

    def join(self, timeout=5.0) -> bool:
        end = time.monotonic() + timeout
        while self._q.unfinished_tasks:
            if time.monotonic() > end:
                return False
            time.sleep(0.01)
        return True

    def stop(self) -> None:
        try:
            self._q.put_nowait(self._stop)
        except queue.Full:
            pass  # fil daemon : il s'éteint avec le processus
