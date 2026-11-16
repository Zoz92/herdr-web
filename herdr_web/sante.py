"""Une ligne de santé par minute dans le journal : mémoire du processus et file d'événements hc.

Relecture finale (I2) : la mémoire montait sans que rien ne le dise ; ces deux mesures suffisent
à voir une fuite ou une file qui ne se vide plus."""
import logging
import threading
from pathlib import Path

log = logging.getLogger("herdr_web.sante")
PERIODE_S = 60.0


def rss_ko(status: Path = Path("/proc/self/status")) -> int | None:
    try:
        for ligne in status.read_text().splitlines():
            if ligne.startswith("VmRSS:"):
                return int(ligne.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def ligne(hc, status: Path = Path("/proc/self/status")) -> str:
    rss = rss_ko(status)
    file = getattr(hc, "_events", None)
    try:
        n = file.qsize() if file is not None else None
    except Exception:
        n = None
    return (f"santé : RSS {'?' if rss is None else f'{rss / 1024:.1f} Mo'}, "
            f"file d'événements hc {'?' if n is None else n}")


def demarrer(hc, stop: threading.Event | None = None, periode: float = PERIODE_S) -> threading.Thread:
    stop = stop or threading.Event()

    def boucle():
        while not stop.wait(periode):
            log.info(ligne(hc))

    t = threading.Thread(target=boucle, daemon=True, name="sante")
    t.start()
    return t
