"""Barre d'état : CPU et mémoire de la machine, et le quota du plan Claude si un script le fournit.

CPU et mémoire sont calculés ici, sans dépendance, toutes les 5 s : deux relevés de /proc/stat à 0,4 s
d'écart (un seul donnerait la moyenne depuis le démarrage), et MemTotal − MemAvailable de /proc/meminfo
(« utilisée » au sens de `free`, sans le cache disque). Hors Linux, ou /proc illisible : « — ».

Le quota vient d'un script choisi par l'utilisateur : `quota_script` dans reglages.json (chemin absolu,
posé à la main, jamais par la page), relancé toutes les 60 s ; sa première ligne est le segment. Sans
script, le segment est absent (`None`) et aucun processus n'est lancé. Script en échec : « — ».
Rien ne tourne si personne ne regarde (`demand`).
"""
import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

PERIODES = {"sys": 5.0, "quota": 60.0}
DELAI_QUOTA = 30
ECART_CPU = 0.4
TIRET = "—"


def jauge(pct) -> str:
    """Quart de cercle : la part utilisée, au quart près (même signe que les segments de herdr-app)."""
    return "○◔◑◕●"[min(4, max(0, int((pct + 12.5) // 25)))]


def _lire(chemin) -> str:
    with open(chemin) as f:
        return f.read()


def _jiffies(texte):
    """(total, inactif) de la ligne `cpu` de /proc/stat ; inactif = idle + iowait."""
    ligne = texte.splitlines()[0].split()
    if not ligne or ligne[0] != "cpu":
        raise ValueError("pas de ligne cpu")
    champs = [int(x) for x in ligne[1:]]
    return sum(champs), champs[3] + (champs[4] if len(champs) > 4 else 0)


class StatusBar:
    def __init__(self, run=subprocess.run, clock=time.monotonic, python=sys.executable, lire=_lire,
                 dormir=time.sleep, quota_script=lambda: None):
        self._run, self._clock, self._python = run, clock, python
        self._lire, self._dormir, self._quota_script = lire, dormir, quota_script
        self._lock = threading.Lock()
        self._vals = {"cpu": TIRET, "mem": TIRET, "quota": None}
        self._next = {"sys": 0.0, "quota": 0.0}
        self._subs = []
        self._stop = threading.Event()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._vals)

    def subscribe(self, cb):
        self._subs.append(cb)

    def _cpu(self) -> str:
        try:
            t1, i1 = _jiffies(self._lire("/proc/stat"))
            self._dormir(ECART_CPU)
            t2, i2 = _jiffies(self._lire("/proc/stat"))
        except (OSError, ValueError, IndexError):
            return TIRET
        dt = t2 - t1
        p = 0.0 if dt <= 0 else 100.0 * (dt - (i2 - i1)) / dt
        return f"{jauge(p)} CPU {p:.0f}%"

    def _mem(self) -> str:
        try:
            info = {}
            for ligne in self._lire("/proc/meminfo").splitlines():
                cle, _, val = ligne.partition(":")
                if val.split():
                    info[cle.strip()] = int(val.split()[0])  # kB
            total, utilise = info["MemTotal"], info["MemTotal"] - info["MemAvailable"]
            pct = 100 * utilise / total
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            return TIRET
        return f"{jauge(pct)} MEM {utilise / 1024 / 1024:.1f}G".replace(".", ",")

    def _quota(self, script) -> str:
        argv = [script] if os.access(script, os.X_OK) else [self._python, script]
        try:
            r = self._run(argv, capture_output=True, text=True, timeout=DELAI_QUOTA)
        except (OSError, subprocess.SubprocessError):
            return TIRET
        out = (r.stdout or "").strip().splitlines()
        return out[0].strip() if r.returncode == 0 and out and out[0].strip() else TIRET

    def refresh(self):
        now = self._clock()
        vals = self.snapshot()
        if now >= self._next["sys"]:
            self._next["sys"] = now + PERIODES["sys"]
            vals["cpu"], vals["mem"] = self._cpu(), self._mem()
        script = self._quota_script()
        if not script:
            vals["quota"] = None
            self._next["quota"] = 0.0  # posé plus tard : lancé au tour suivant, sans attendre 60 s
        elif now >= self._next["quota"]:
            self._next["quota"] = now + PERIODES["quota"]
            vals["quota"] = self._quota(script)
        with self._lock:
            changed = vals != self._vals
            self._vals = vals
        if changed:
            for cb in self._subs:
                try:
                    cb(dict(vals))
                except Exception:
                    log.exception("abonné de la barre d'état a levé")

    def start(self, demand=lambda: True, tick: float = 1.0):
        def boucle():
            while not self._stop.wait(tick):
                if demand():
                    try:
                        self.refresh()
                    except Exception:
                        log.exception("barre d'état")
        threading.Thread(target=boucle, daemon=True, name="barre-etat").start()

    def stop(self):
        self._stop.set()
