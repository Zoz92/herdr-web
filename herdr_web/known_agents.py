"""Point 34 : les agents du menu « + » (« Start an agent »), comme le menu de herdr-app.

herdr-app tient sa liste dans sa table de programmes (entrées qui ont un `run`), complétée par
`~/.config/herdr-app/programs.json`, et ne l'expose qu'aux mods (`knownAgents()` de herdr.d.ts) :
aucune commande `herdr-app` ni `hc` ne la rend. On reprend donc les entrées `run` de sa table
(Claude Code, OpenCode, Grok, Codex, Pi : celles de son menu), on y applique `programs.json` comme
lui (ajout ou remplacement par entrée), et on vérifie l'installation comme lui : `command -v` dans
un shell interactif de l'utilisateur, au plus une fois par minute ; repli sur `shutil.which` avec
le PATH du service plus `~/.local/bin` si ce shell ne répond pas. Passé la minute, la liste connue
est rendue tout de suite et relue en fond : une requête n'attend le shell (≤ 15 s) qu'au tout premier
appel. Un alias ne compte pas : `"$@"` ne le développe pas, il passerait pour installé sans se lancer.
"""
import json
import os
import pwd
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

# entrées `run` de la table embarquée de herdr-app, dans l'ordre de son menu
BUILTIN = (
    ("claude", "Claude Code", ["claude"]),
    ("opencode", "OpenCode", ["opencode"]),
    ("grok", "Grok", ["grok"]),
    ("codex", "Codex", ["codex", "--no-daemon"]),  # AGENTS.md : un démon Codex partagé mêle les identités
    ("pi", "Pi", ["pi"]),
)
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
NAME_RE = re.compile(r"[A-Za-z0-9_.+-]{1,64}")
TTL_S = 60.0
CHECK_TIMEOUT_S = 15
# $0 vaut « herdr-web », les noms arrivent en arguments : jamais interpolés dans le script.
# unalias d'abord : un alias qui masque un programme rend le programme, un alias seul ne rend rien
CHECK_SCRIPT = ('for n in "$@"; do unalias -- "$n" 2>/dev/null; p=$(command -v -- "$n" 2>/dev/null) '
                '&& printf "@@herdr@@%s\\t%s\\n" "$n" "$p"; done')


def _programs(app_home: Path) -> list:
    """BUILTIN, puis programs.json par-dessus (une entrée sans `run` valide est ignorée)."""
    out = {k: {"id": k, "name": n, "run": list(r)} for k, n, r in BUILTIN}
    try:
        d = json.loads((app_home / "programs.json").read_text())
        progs = d.get("programs") if isinstance(d, dict) else None
    except (OSError, ValueError):
        progs = None
    for k, v in (progs or {}).items() if isinstance(progs, dict) else ():
        if not (isinstance(k, str) and ID_RE.fullmatch(k) and isinstance(v, dict)):
            continue
        run = v.get("run")
        if run is None and k in out:  # change d'autres champs : garder le run connu
            run = out[k]["run"]
        if not _run_ok(run):
            continue
        name = v.get("name") if isinstance(v.get("name"), str) and v.get("name") else (
            out[k]["name"] if k in out else k)
        out[k] = {"id": k, "name": name[:64], "run": list(run)}
    return list(out.values())


class KnownAgents:
    def __init__(self, home=None, run=subprocess.run, shell=None, clock=time.monotonic, fond=None):
        self.home = Path(home) if home else Path.home()
        self._run, self._clock = run, clock
        self._fond = fond or (lambda f: threading.Thread(target=f, daemon=True,
                                                          name="agents-connus").start())
        self.shell = shell or _login_shell()
        self._lock = threading.Lock()
        self._cache = None  # (instant, liste)
        self._relecture = False  # une relecture en fond est en cours

    def list(self) -> list:
        """[{id, name, installed, argv}] ; argv est None quand l'agent n'est pas installé."""
        with self._lock:
            if self._cache is None:  # premier appel : rien à rendre, on attend le shell
                self._cache = (self._clock(), self._calculer())
                return self._cache[1]
            lancer = self._clock() - self._cache[0] >= TTL_S and not self._relecture
            if lancer:
                self._relecture = True
            out = self._cache[1]
        if lancer:  # hors du verrou : la relecture le prend pour poser son résultat
            self._fond(self._relire)
        return out

    def _relire(self):
        try:
            out = self._calculer()
            with self._lock:
                self._cache = (self._clock(), out)
        finally:
            with self._lock:
                self._relecture = False

    def _calculer(self) -> list:
        progs = _programs(self.home / ".config" / "herdr-app")
        found = self._check([p["run"][0] for p in progs])
        out = []
        for p in progs:
            cmd, rest = p["run"][0], p["run"][1:]
            where = found.get(cmd)
            if where and where.startswith("/"):
                argv = [where, *rest]  # chemin absolu : le core n'a pas le PATH du shell
            elif where:  # fonction du shell : la lancer par ce shell
                argv = [self.shell, "-ic", '"$@"', "herdr-web", cmd, *rest]
            else:
                argv = None
            out.append({"id": p["id"], "name": p["name"], "installed": argv is not None,
                        "argv": argv})
        return out

    def public(self) -> list:
        return [{"id": a["id"], "name": a["name"], "installed": a["installed"]} for a in self.list()]

    def installed(self) -> dict:
        return {a["id"]: a for a in self.list() if a["installed"]}

    def _check(self, names) -> dict:
        names = [n for n in dict.fromkeys(names)]
        try:
            r = self._run([self.shell, "-ic", CHECK_SCRIPT, "herdr-web", *names],
                          capture_output=True, text=True, timeout=CHECK_TIMEOUT_S,
                          stdin=subprocess.DEVNULL)
            if r.returncode == 0 or "@@herdr@@" in (r.stdout or ""):
                found = {}
                for line in (r.stdout or "").splitlines():
                    if line.startswith("@@herdr@@") and "\t" in line:
                        n, p = line[len("@@herdr@@"):].split("\t", 1)
                        if n in names and p.strip() and not p.startswith("alias "):
                            found[n] = p.strip()
                return found
        except (OSError, subprocess.SubprocessError):
            pass
        path = os.pathsep.join([os.environ.get("PATH", ""), str(self.home / ".local" / "bin")])
        return {n: w for n in names if (w := shutil.which(n, path=path))}


def _run_ok(run) -> bool:
    """Une liste de textes non vides ; le programme est un nom simple ou un chemin absolu."""
    if not (isinstance(run, list) and run and all(isinstance(x, str) and x for x in run)):
        return False
    if any(ord(c) < 32 for x in run for c in x):
        return False
    return bool(NAME_RE.fullmatch(run[0])) or run[0].startswith("/")


def _login_shell() -> str:
    try:
        sh = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        sh = ""
    return sh if sh and Path(sh).is_file() else "/bin/sh"
