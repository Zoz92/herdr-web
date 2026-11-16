import json
import subprocess
import sys

import pytest

from herdr_web.reglages import Reglages
from herdr_web.statusbar import StatusBar, jauge

# deux relevés de /proc/stat à 0,4 s d'écart : 1000 jiffies, dont 750 inactifs (idle 700 + iowait 50)
STAT_1 = "cpu  1000 0 500 8000 100 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT_2 = "cpu  1150 0 600 8700 150 0 0 0 0 0\ncpu0 1 2 3 4\n"
MEMINFO = "MemTotal:       32000000 kB\nMemFree:  1000 kB\nMemAvailable:   15223000 kB\n"
# utilisée = (32000000 - 15223000) kB = 16,0 Gio ; 52 % du total : jauge ◑


class Proc:
    """/proc simulé : chaque lecture de /proc/stat rend le relevé suivant (le dernier ensuite)."""

    def __init__(self, stats=(STAT_1, STAT_2), meminfo=MEMINFO):
        self.stats, self.meminfo, self.lus = (None if stats is None else list(stats)), meminfo, []

    def __call__(self, chemin):
        self.lus.append(chemin)
        if chemin == "/proc/stat":
            if self.stats is None:
                raise FileNotFoundError(chemin)
            return self.stats.pop(0) if len(self.stats) > 1 else self.stats[0]
        if chemin == "/proc/meminfo":
            if self.meminfo is None:
                raise FileNotFoundError(chemin)
            return self.meminfo
        raise AssertionError(chemin)


class Run:
    def __init__(self, sortie="◔ 7J 15% ↻mer 19:00\n", rc=0):
        self.sortie, self.rc, self.appels = sortie, rc, []

    def __call__(self, argv, **kw):
        self.appels.append((argv, kw))
        if isinstance(self.sortie, Exception):
            raise self.sortie
        return subprocess.CompletedProcess(argv, self.rc, self.sortie, "")


def barre(quota=None, run=None, proc=None, now=None):
    t = now if now is not None else {"now": 0.0}
    return StatusBar(run=run or Run(), clock=lambda: t["now"], python="/py", lire=proc or Proc(),
                     dormir=lambda s: None, quota_script=lambda: quota)


def test_cpu_et_memoire_calcules_depuis_proc_au_format_de_la_barre():
    proc = Proc()
    sb = barre(proc=proc)
    sb.refresh()
    # (1000 - 750) / 1000 = 25 % ; même rendu que le segment de herdr-app : jauge, « CPU 25% », virgule
    assert sb.snapshot() == {"cpu": "◔ CPU 25%", "mem": "◑ MEM 16,0G", "quota": None}
    assert proc.lus == ["/proc/stat", "/proc/stat", "/proc/meminfo"]


def test_deux_releves_espaces_de_0_4_s():
    dodos = []
    sb = StatusBar(run=Run(), clock=lambda: 0.0, lire=Proc(), dormir=dodos.append, quota_script=lambda: None)
    sb.refresh()
    assert dodos == [0.4]


@pytest.mark.parametrize("pct, signe", [(0, "○"), (12, "○"), (13, "◔"), (37, "◔"), (38, "◑"), (62, "◑"),
                                        (63, "◕"), (87, "◕"), (88, "●"), (100, "●")])
def test_jauge_par_quart(pct, signe):
    assert jauge(pct) == signe


def test_hors_linux_tirets_sans_erreur():
    sb = barre(proc=Proc(stats=None, meminfo=None))
    sb.refresh()
    assert sb.snapshot() == {"cpu": "—", "mem": "—", "quota": None}


@pytest.mark.parametrize("stats, meminfo, attendu", [  # chaque segment tombe seul
    (["cpu  x y\n"], MEMINFO, ("—", "◑ MEM 16,0G")),             # relevé CPU abîmé
    (["intr 1 2\n"], MEMINFO, ("—", "◑ MEM 16,0G")),             # pas de ligne cpu
    ([STAT_1, STAT_1], MEMINFO, ("○ CPU 0%", "◑ MEM 16,0G")),     # aucun jiffy écoulé : 0 %, pas de division
    ([STAT_1, STAT_2], "MemTotal: 0 kB\nMemAvailable: 0 kB\n", ("◔ CPU 25%", "—")),
    ([STAT_1, STAT_2], "MemFree: 3 kB\n", ("◔ CPU 25%", "—"))])
def test_proc_illisible_rend_un_tiret_par_segment(stats, meminfo, attendu):
    sb = barre(proc=Proc(stats=stats, meminfo=meminfo))
    sb.refresh()
    v = sb.snapshot()
    assert (v["cpu"], v["mem"]) == attendu


def test_sans_script_de_quota_le_segment_est_absent_et_rien_ne_tourne():
    run = Run()
    sb = barre(run=run)
    sb.refresh()
    assert sb.snapshot()["quota"] is None
    assert run.appels == []  # aucun sous-processus : herdr-web ne dépend d'aucun script


def test_script_de_quota_executable_lance_tel_quel(tmp_path):
    s = tmp_path / "quota"
    s.write_text("#!/bin/sh\n")
    s.chmod(0o755)
    run = Run()
    sb = barre(quota=str(s), run=run)
    sb.refresh()
    assert sb.snapshot()["quota"] == "◔ 7J 15% ↻mer 19:00"
    (argv, kw), = run.appels
    assert argv == [str(s)] and "shell" not in kw and kw["timeout"]


def test_script_de_quota_non_executable_lance_par_python(tmp_path):
    s = tmp_path / "quota.py"
    s.write_text("print('x')\n")
    s.chmod(0o644)
    run = Run()
    barre(quota=str(s), run=run).refresh()
    assert [a for a, _ in run.appels] == [["/py", str(s)]]


@pytest.mark.parametrize("sortie, rc", [("", 0), ("  \n", 0), ("ok\n", 1), (subprocess.TimeoutExpired("x", 30), 0),
                                        (OSError("absent"), 0)])
def test_script_de_quota_en_echec_rend_un_tiret(tmp_path, sortie, rc):
    sb = barre(quota=str(tmp_path / "q"), run=Run(sortie, rc))
    sb.refresh()
    assert sb.snapshot()["quota"] == "—"


def test_cadences_5_s_et_60_s(tmp_path):
    run, proc, t = Run(), Proc(), {"now": 0.0}
    sb = barre(quota=str(tmp_path / "q"), run=run, proc=proc, now=t)
    sb.refresh()
    for _ in range(12):  # 60 s
        t["now"] += 5
        sb.refresh()
    assert proc.lus.count("/proc/meminfo") == 13 and len(run.appels) == 2


def test_script_pose_apres_le_demarrage_pris_au_tour_suivant(tmp_path):
    run, t, quota = Run(), {"now": 0.0}, {"chemin": None}
    sb = StatusBar(run=run, clock=lambda: t["now"], python="/py", lire=Proc(), dormir=lambda s: None,
                   quota_script=lambda: quota["chemin"])
    sb.refresh()
    assert sb.snapshot()["quota"] is None
    quota["chemin"] = str(tmp_path / "q")
    t["now"] += 1  # sans attendre la cadence de 60 s
    sb.refresh()
    assert sb.snapshot()["quota"] == "◔ 7J 15% ↻mer 19:00" and len(run.appels) == 1
    quota["chemin"] = None  # retiré : le segment disparaît
    t["now"] += 1
    sb.refresh()
    assert sb.snapshot()["quota"] is None
    quota["chemin"] = str(tmp_path / "q")  # remis aussitôt : relancé sans attendre la fin des 60 s
    t["now"] += 1
    sb.refresh()
    assert sb.snapshot()["quota"] == "◔ 7J 15% ↻mer 19:00" and len(run.appels) == 2


def test_abonnes_prevenus_au_changement_seulement():
    t, stats = {"now": 0.0}, [STAT_1, STAT_2]
    proc = Proc(stats=stats)
    sb = barre(proc=proc, now=t)
    vus = []
    sb.subscribe(vus.append)
    sb.refresh()
    t["now"] += 5
    proc.stats = [STAT_1, STAT_2]
    sb.refresh()  # même mesure : pas de nouvel envoi
    proc.stats = [STAT_1, "cpu  1300 0 600 8550 150 0 0 0 0 0\n"]  # 400 actifs sur 1000 : 40 %
    t["now"] += 5
    sb.refresh()
    assert [v["cpu"] for v in vus] == ["◔ CPU 25%", "◑ CPU 40%"]


def test_vraie_machine_linux():
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux seulement")
    sb = StatusBar(run=Run(), quota_script=lambda: None)
    sb.refresh()
    v = sb.snapshot()
    assert v["cpu"].split()[1:2] == ["CPU"] and v["cpu"].endswith("%")
    assert v["mem"].split()[1:2] == ["MEM"] and v["mem"].endswith("G")


# -- réglage serveur quota_script : posé à la main dans reglages.json, jamais par la page --

@pytest.mark.parametrize("valeur, attendu", [
    (None, None), ("", None), ("   ", None), ("relatif/quota.py", None), ("~/quota.py", None), (3, None),
    (["/a"], None), ("/opt/quota.py", "/opt/quota.py"), ("  /opt/quota.py ", "/opt/quota.py")])
def test_reglage_quota_script(tmp_path, valeur, attendu):
    f = tmp_path / "reglages.json"
    f.write_text(json.dumps({} if valeur is None else {"quota_script": valeur}))
    assert Reglages(f).quota_script() == attendu


def test_reglage_quota_script_fichier_absent_ou_abime(tmp_path):
    assert Reglages(tmp_path / "absent.json").quota_script() is None
    (tmp_path / "x.json").write_text("[1")
    assert Reglages(tmp_path / "x.json").quota_script() is None


def test_quota_script_ni_montre_ni_perdu_par_la_page(tmp_path):
    f = tmp_path / "reglages.json"
    f.write_text(json.dumps({"quota_script": "/opt/quota.py"}))
    r = Reglages(f)
    assert "quota_script" not in r.lire()  # la page ne voit pas le chemin
    r.ecrire(langue="fr")  # une écriture depuis la page garde le réglage du serveur
    assert json.loads(f.read_text())["quota_script"] == "/opt/quota.py"
    assert r.quota_script() == "/opt/quota.py"


def test_service_branche_le_quota_sur_les_reglages_du_serveur(tmp_path, monkeypatch):
    from herdr_web import __main__ as m, server
    f = tmp_path / "reglages.json"
    monkeypatch.setattr(server, "REGLAGES", f)
    run = Run()
    sb = m.barre_etat(run=run, lire=Proc(), dormir=lambda s: None)
    sb.refresh()
    assert sb.snapshot()["quota"] is None and run.appels == []
    f.write_text(json.dumps({"quota_script": str(tmp_path / "q")}))  # posé à la main, service en marche
    sb.refresh()
    assert sb.snapshot()["quota"] == "◔ 7J 15% ↻mer 19:00"
