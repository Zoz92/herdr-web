"""Infos d'agents pour le mod agents-panel de herdr-app : modèle, contexte, dossier, branche, machine,
depuis quand (since_ms, status_ms de hc), message d'un agent bloqué (msg) et sous-agents in-process.

Usage : python3 tools/agents_info.py <adresse hc>...  →  JSON {adresse: {...}} sur la sortie.

Le mod n'a ni le journal de Claude Code ni la fenêtre de compaction : il les demande ici, au
même code que la page herdr-web (AgentBoard), pour que les deux affichent la même chose.
`msg` est émis quel que soit l'état de l'agent (le dernier message de hc, même hors « blocked ») :
les consommateurs filtrent sur `state == "blocked"` avant de l'afficher (la page herdr-web le fait).
Contrat de `os` / `os_wsl` (lu par la page ET le mod) : `os` = clé de logo de la machine locale
(herdr_web/osid.py : « glfos », « ubuntu », « arch », « windows », « macos », « linux » = Tux, "" = rien de lisible) ;
`os_wsl` = vrai sous WSL (logo de la distribution + badge Windows). Même table dans le mod.
Lecture seule. Une adresse que hc ne connaît pas est omise ; hc injoignable : {} et rc 0.
"""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from herdr_web import osid  # noqa: E402
from herdr_web.agents import AgentBoard  # noqa: E402

CHAMPS = ("model", "context", "context_max", "dir", "branch", "machine", "role", "activity", "label",
          "since_ms")
# un sous-agent in-process (outil Agent) : les champs que la page affiche (spec du 2026-11-15)
CHAMPS_SOUS = ("id", "title", "state", "model", "context", "context_max", "role")


def os_id(chemin=Path("/etc/os-release")) -> str:
    """ID de /etc/os-release (« glfos », « nixos », « debian »…), "" s'il ne se lit pas."""
    try:
        for ligne in chemin.read_text().splitlines():
            if ligne.startswith("ID="):
                return ligne[3:].strip().strip('"').lower()
    except OSError:
        pass
    return ""


def hc_get(addr, run=subprocess.run):
    try:
        r = run(["hc", "get", addr, "--json"], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class _Hc:
    """Le minimum qu'AgentBoard demande : terminal.list, servi par les `hc get` déjà faits."""

    def __init__(self, terminals):
        self.terminals = terminals

    def on_event(self, cb):
        pass

    def on_state(self, cb):
        pass

    def request(self, method, params=None, timeout=100.0):
        return {"terminals": self.terminals, "hidden_finished": 0, "machines": []}


def infos(adresses, get=hc_get, board_cls=AgentBoard, os_lu=osid.cette_machine) -> dict:
    terms = [t for t in (get(a) for a in adresses if "/" not in a) if isinstance(t, dict)]
    b = board_cls(_Hc(terms), layout=lambda: None)
    b.refresh()
    ident, wsl = os_lu()
    out = {}
    for a in b.snapshot():
        d = asdict(a)
        out[a.address] = {k: d.get(k) for k in CHAMPS} | {
            "os": ident, "os_wsl": wsl,
            "msg": a.msg or None,  # émis quel que soit l'état : les consommateurs filtrent sur « blocked »
            "subagents": [{k: s.get(k) for k in CHAMPS_SOUS} for s in a.subagents if isinstance(s, dict)]}
    return out


def main(argv):
    print(json.dumps(infos(argv), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
