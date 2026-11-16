"""I2 (relecture finale) : mémoire du service bornée et journalisée."""
import logging
import queue
import threading
from pathlib import Path

from herdr_web import sante

UNIT = Path(__file__).parent.parent / "deploy" / "herdr-web.service"


def test_unit_borne_la_memoire():
    lignes = UNIT.read_text().splitlines()
    for attendu in ("Environment=MALLOC_ARENA_MAX=2", "MemoryHigh=400M", "MemoryMax=800M"):
        assert attendu in lignes, attendu


class _Hc:
    def __init__(self, n):
        self._events = queue.Queue()
        for i in range(n):
            self._events.put(i)


def test_ligne_de_sante(tmp_path):
    st = tmp_path / "status"
    st.write_text("Name:\tpython\nVmRSS:\t  204800 kB\nThreads:\t9\n")
    assert sante.ligne(_Hc(3), st) == "santé : RSS 200.0 Mo, file d'événements hc 3"
    assert sante.ligne(object(), tmp_path / "absent") == "santé : RSS ?, file d'événements hc ?"


def test_journalisee_a_chaque_periode(caplog):
    stop = threading.Event()
    with caplog.at_level(logging.INFO, logger="herdr_web.sante"):
        sante.demarrer(_Hc(1), stop, periode=0.05)
        for _ in range(100):
            if sum("santé : RSS" in r.getMessage() for r in caplog.records) >= 2:
                break
            threading.Event().wait(0.02)
        stop.set()
    assert sum("santé : RSS" in r.getMessage() for r in caplog.records) >= 2
    assert sante.rss_ko() and sante.rss_ko() > 0  # /proc/self/status lu pour de vrai
