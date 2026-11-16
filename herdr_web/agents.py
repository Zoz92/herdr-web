"""Tableau des agents : terminaux live de herdr-core, triés, avec transitions vers « attend »."""
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from herdr_web import osid
from herdr_web.chat import resolve_session, subagent_path

log = logging.getLogger(__name__)

_ORDRE = {"blocked": 0, "done": 1, "working": 2, "idle": 3}


@dataclass
class Agent:
    address: str
    name: str | None
    title: str
    cwd: str
    program: str
    pid: int | None
    state: str  # idle|working|done|blocked|error|unknown
    kind: str | None
    msg: str
    space: str | None
    tab: str | None
    turn: int
    since_ms: int | None = None  # dernier changement d'état (status_ms de hc), epoch ms
    model: str | None = None
    context: int | None = None  # tokens reçus par le modèle au dernier tour (usage du journal)
    context_max: int | None = None  # fenêtre de compaction (autoCompactWindow), sinon la fenêtre
    dir: str = ""  # dossier court : « dépôt » ou « dépôt › worktree »
    subagents: list = field(default_factory=list)  # [{id, title, state, model}] (records de hc)
    branch: str | None = None  # branche git du cwd, None hors dépôt
    os: str = ""  # clé de logo du système de la machine (herdr_web/osid.py) ; "" = ordinateur générique
    os_wsl: bool = False  # la machine est un WSL : badge Windows en plus du logo de la distribution
    machine: str = ""  # machine qui porte le terminal (cet ordinateur, ou la machine hc distante)
    role: str = "terminal"  # template hc connu (maker, concepteur, checker…), sinon claude | terminal
    activity: str | None = None  # « shell » tant qu'un Bash lancé n'a pas rendu son résultat
    cols: int | None = None  # largeur du terminal hc en colonnes (ajuste la police de la vue brute)
    label: str = ""  # « space · détail » comme la liste d'agents de herdr-app (voir libelle())
    parent: str | None = None  # adresse du terminal hc qui l'a lancé (`hc run` depuis un agent), sinon None
    pinned: bool = False  # label `herdr.pinned` du terminal (posé par `terminal.pin` de herdr-app)
    seen: int | None = None  # label `herdr.seen` : dernier tour regardé (herdr-app, ou la page via /seen)


def label_seen(labels) -> int | None:
    """Le tour vu (`herdr.seen`, entier écrit en texte) ; None si absent ou illisible."""
    v = labels.get("herdr.seen") if isinstance(labels, dict) else None
    return int(v) if isinstance(v, str) and v.isascii() and v.isdigit() else None


def etat_vu(state: str, turn: int, seen: int | None) -> str:
    """Un `done` dont le tour a été regardé se lit `idle`, comme dans herdr-app : regarder un agent
    fini, n'importe où (fenêtre ou page), le passe en idle des deux côtés. Jamais vu : `done`."""
    return "idle" if state == "done" and seen is not None and seen >= turn else state


def sort_key(a: Agent) -> tuple:
    return (_ORDRE.get(a.state, 4), (a.label or a.name or a.title or a.address).lower(), a.address)


def _net(s: str) -> str:
    """Titre sans ses glyphes de tête (« ✳ », spinner) : le clean() du mod agents de herdr-app."""
    s = s or ""
    i = 0
    while i < len(s) and not s[i].isalnum():
        i += 1
    return s[i:].strip()


def libelle(name, space, tab, title, program, partage: bool = False) -> str:
    """Libellé d'un agent, porté depuis le mod agents de herdr-app (`title()` puis la ligne
    « space · détail » de sa vue par priorité) : un nom hc explicite l'emporte ; sinon le space
    (qui suit le worktree, voir lieu_du_dossier) suivi du détail — l'onglet s'il a été nommé, le
    titre de l'agent s'il dit quelque chose, rien si l'onglet n'est qu'un numéro. Un onglet que
    plusieurs terminaux partagent ne distingue personne : on y prend le titre de chacun."""
    if name:
        return name
    title = title or ""
    live = _net(title)
    prog = (program or "").lower()
    muets = {prog, prog.replace("-", " "), prog.split("-")[0]} - {""}
    chemin = re.match(r"^[~/]", re.sub(r"^[^\w~/]+", "", title)) is not None
    parlant = bool(live) and live.lower() not in muets and not chemin and "@" not in live
    tab = tab or ""
    nomme = bool(tab) and not tab.isdigit() and not partage
    detail = live if (not nomme or tab.lower() in muets) and parlant else tab
    if detail.isdigit():
        detail = ""
    return " · ".join(x for x in (space, detail) if x)


def lieu_du_dossier(cwd: str, home: Path) -> str:
    """Nom que herdr-app donne à un space jamais renommé, d'après le dossier vivant de son 1er
    terminal : le dépôt git (pour un worktree lié, son propre dossier — c'est ce qui fait suivre
    le worktree), sinon le dossier, « ~ » à la maison. Repli quand la fenêtre est fermée."""
    if not cwd:
        return ""
    p = Path(cwd)
    for d in (p, *p.parents):
        try:
            if (d / ".git").exists():
                return d.name or "/"
        except OSError:
            break
    if p == Path(home):
        return "~"
    return p.name or cwd


_WT = re.compile(r"^(.*)/\.claude/worktrees/([^/]+)")


PROC = Path("/proc")  # les tests le pointent vers un dossier vide : leurs pid inventés sont réels ici


def cwd_reel(pid, proc: Path = PROC) -> str | None:
    """Dossier courant réel d'un processus local. Celui que rapporte hc peut être périmé : mesuré le
    2026-11-14, t1c annonçait un worktree supprimé alors que claude et son zsh étaient dans le
    clone ; la branche lue dans ce dossier disparu restait vide."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        return os.readlink(proc / str(pid) / "cwd")
    except OSError:
        return None


def short_dir(cwd: str) -> str:
    """Nom du dépôt qui contient `cwd` (« dépôt › worktree » pour un worktree git), sinon le
    dernier segment du chemin."""
    if not cwd:
        return ""
    p = Path(cwd)
    for d in (p, *p.parents):
        g = d / ".git"
        try:
            if g.is_dir():
                return d.name or "/"
            if g.is_file():
                txt = g.read_text(errors="replace")
                m = re.search(r"^gitdir:\s*(.+?)\s*$", txt, re.M)
                if m and "/.git/worktrees/" in m.group(1):
                    principal = m.group(1).split("/.git/worktrees/")[0]
                    return f"{Path(principal).name} › {d.name}"
                return d.name
        except OSError:
            break
    m = _WT.match(cwd.rstrip("/"))
    if m:
        return f"{Path(m.group(1)).name} › {m.group(2)}"
    return p.name or cwd


def model_from_argv(argv) -> str | None:
    """Valeur de `--model X` ou `--model=X` dans l'argv du processus au premier plan."""
    argv = [str(a) for a in (argv or [])]
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--model="):
            return a.split("=", 1)[1] or None
    return None


_FIN = 256 * 1024
_MODEL_MAX = 16 * 1024 * 1024  # au-delà, on garde le dernier modèle connu


_USAGE = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _contexte(msg: dict) -> int | None:
    """Ce que le modèle a reçu à ce tour : entrée +
    cache lu + cache créé ; la sortie n'en fait pas partie."""
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    try:
        return sum(int(u.get(k) or 0) for k in _USAGE)
    except (TypeError, ValueError):
        return None


def _scan(data: bytes):
    """(dernier modèle, ids des Bash lancés, ids des résultats rendus, contexte) d'un bloc de lignes.
    Contexte : usage du dernier message assistant hors `isSidechain` (un tour de sous-agent n'est
    pas le contexte de la session) ; à défaut, celui d'une ligne isSidechain — le fil d'un
    sous-agent n'a que celles-là."""
    model, bash, rendus = None, [], set()
    ctx, ctx_side = None, None
    for raw in data.splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        m = msg.get("model")
        if d.get("type") == "assistant" and isinstance(m, str) and m and not m.startswith("<"):
            model = m
        if d.get("type") == "assistant":
            c = _contexte(msg)
            if c is not None:
                if d.get("isSidechain"):
                    ctx_side = c
                else:
                    ctx = c
        content = msg.get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                bash.append(b.get("id"))
            elif b.get("type") == "tool_result":
                rendus.add(b.get("tool_use_id"))
    return model, bash, rendus, (ctx if ctx is not None else ctx_side)


def tail_info(path: Path) -> tuple[str | None, str | None, int | None]:
    """(modèle, activité, contexte) lus dans la fin du journal : `message.model` et l'usage de la
    dernière ligne assistant réelle (cherchés à rebours, par blocs, si la fin n'en a pas), et
    « shell » si un `tool_use` Bash de la fin n'a pas encore son `tool_result`."""
    with path.open("rb") as f:
        taille = f.seek(0, os.SEEK_END)
        debut = max(0, taille - _FIN)
        f.seek(debut)
        data = f.read()
        model, bash, rendus, ctx = _scan(data)
        activity = "shell" if any(i not in rendus for i in bash) else None
        reste = data[: data.find(b"\n") + 1] if debut else b""  # 1re ligne peut-être coupée
        while (model is None or ctx is None) and debut > 0 and taille - debut < _MODEL_MAX:
            fin = debut
            debut = max(0, fin - _FIN)
            f.seek(debut)
            bloc = f.read(fin - debut) + reste
            coupe = bloc.find(b"\n") + 1 if debut else 0
            reste = bloc[:coupe]
            m, _, _, c = _scan(bloc[coupe:])
            model = model if model is not None else m
            ctx = ctx if ctx is not None else c
    return model, activity, ctx


def last_model(path: Path) -> str | None:
    return tail_info(path)[0]


FENETRE = 1_000_000  # repli : fenêtre d'un modèle absent du catalogue de Claude Code (opus[1m]), comme contexte-palier.py
_CATALOGUES: dict = {}  # chemin -> (mtime_ns, {id du modèle: max_input_tokens})


def fenetres_modeles(home: Path) -> dict:
    """Fenêtre de chaque modèle, lue dans le catalogue que Claude Code écrit lui-même
    (~/.claude/cache/model-catalog/*-cc.json, `runtime.max_input_tokens`). Vide si illisible."""
    try:
        fichiers = sorted((home / ".claude" / "cache" / "model-catalog").glob("*-cc.json"),
                          key=lambda f: f.stat().st_mtime_ns)
        if not fichiers:
            return {}
        f = fichiers[-1]
        mt = f.stat().st_mtime_ns
        hit = _CATALOGUES.get(f)
        if hit and hit[0] == mt:
            return hit[1]
        out = {}
        for m in json.loads(f.read_text())["catalog"]["config"]["models"]:
            n = (m.get("runtime") or {}).get("max_input_tokens")
            if isinstance(m.get("id"), str) and isinstance(n, int) and not isinstance(n, bool) and n > 0:
                out[m["id"]] = n
        _CATALOGUES[f] = (mt, out)
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def fenetre_modele(model: str | None, home: Path) -> int:
    """Fenêtre du modèle (identifiant du journal, ex. claude-opus-5-5) ; FENETRE s'il est inconnu."""
    if not isinstance(model, str) or not model:
        return FENETRE
    return fenetres_modeles(home).get(model.split("[")[0], FENETRE)


def _reglages(chemin: Path) -> dict:
    try:
        d = json.loads(chemin.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def compact_window(home: Path = Path.home(), cwd: str | None = None, model: str | None = None) -> int:
    """Seuil où Claude Code compacte, celui que la jauge regarde : min(autoCompactWindow effectif,
    fenêtre du modèle). autoCompactWindow : le premier des réglages du projet (settings.local.json,
    puis settings.json, du dossier de l'agent et de ses parents) puis de ~/.claude/settings.json qui
    le définit ; la compaction coupée (autoCompactEnabled false) le laisse de côté. Sans réglage, ou
    sans modèle connu, c'est la fenêtre du modèle (FENETRE à défaut)."""
    couches = []
    if cwd:
        for d in [Path(cwd), *Path(cwd).parents]:
            if d != home:
                couches += [_reglages(d / ".claude" / "settings.local.json"), _reglages(d / ".claude" / "settings.json")]
    couches.append(_reglages(home / ".claude" / "settings.json"))
    actif = next((c["autoCompactEnabled"] for c in couches if "autoCompactEnabled" in c), True)
    w = next((c["autoCompactWindow"] for c in couches if "autoCompactWindow" in c), None)
    plafond = fenetre_modele(model, home)
    if actif is not False and isinstance(w, int) and not isinstance(w, bool) and w > 0:
        return min(w, plafond)
    return plafond


BRANCH_TTL = 30
ROLES = ("maker", "concepteur", "checker", "checker-conception")  # templates du mod agents-panel


# Point 35 : un sous-agent in-process n'a pas de template hc ; son rôle se déduit du premier mot
# de son titre (la `description` de l'outil Agent), sans casse ni accents.
_MOTS_MAKER = {"implement", "implementer", "fix", "corriger", "build", "ecrire", "write", "refactor"}
_MOTS_CHECKER = {"check", "checker", "verify", "verifier"}
# point 39 : la relecture a son rôle et son animation, distincts du checker
_MOTS_REVIEWER = {"review", "re-review", "reviewer", "revue", "relire", "relecture", "audit"}
_REVIEW_MOT = re.compile(r"\breview", re.I)  # début de mot : review, re-review, code-reviewer ; pas preview


def role_inferred(title) -> str | None:
    import unicodedata
    if not isinstance(title, str):
        return None
    mots = title.strip().split()
    if not mots:
        return None
    mot = unicodedata.normalize("NFKD", mots[0]).encode("ascii", "ignore").decode().lower()
    mot = mot.strip(".,;:!?()[]«»\"'")
    if mot in _MOTS_MAKER:
        return "maker"
    if mot in _MOTS_REVIEWER:
        return "reviewer"
    if mot in _MOTS_CHECKER:
        return "checker"
    return None


def role_of(template, claude: bool, name=None) -> str:
    # point 39 : un template ou un nom de terminal qui contient le MOT « review » (pas « preview »)
    if any(isinstance(x, str) and _REVIEW_MOT.search(x) for x in (template, name)):
        return "reviewer"
    if template in ROLES:
        return template
    return "claude" if claude else "terminal"


class BranchCache:
    """Branche git d'un dossier (`git rev-parse --abbrev-ref HEAD`), en cache 30 s par dossier."""

    def __init__(self, run=None, clock=time.monotonic, ttl: float = BRANCH_TTL):
        self._run, self._clock, self._ttl = run, clock, ttl
        self._cache: dict[str, tuple[float, str | None]] = {}

    def get(self, cwd: str) -> str | None:
        if not cwd:
            return None
        now = self._clock()
        hit = self._cache.get(cwd)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            r = (self._run or subprocess.run)(["git", "--no-optional-locks", "-C", cwd, "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True, timeout=5)
            b = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            b = ""
        self._cache[cwd] = (now, b or None)
        return b or None


def family(model: str | None) -> str | None:
    """opus | sonnet | haiku | fable, sinon le modèle tel quel (None si inconnu)."""
    if not model:
        return None
    m = re.search(r"opus|sonnet|haiku|fable", model, re.I)
    return m.group(0).lower() if m else model


def model_counts(agents) -> dict:
    """Agents et sous-agents actifs par famille de modèle. Un agent compte s'il a un état connu
    (un terminal sans état n'est pas un agent) ; un sous-agent tant qu'il n'est ni fini ni inactif."""
    out: dict[str, int] = {}
    for a in agents:
        modeles = []
        if a.state in ("idle", "working", "done", "blocked", "error"):
            modeles.append(a.model)
        modeles += [s.get("model") for s in a.subagents if s.get("state") not in ("done", "idle")]
        for m in modeles:
            f = family(m)
            if f:
                out[f] = out.get(f, 0) + 1
    return out


def herdr_app_ls():
    """Disposition de herdr-app (JSON de `herdr-app ls`) ; None si la fenêtre est fermée."""
    try:
        r = subprocess.run(["herdr-app", "ls"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _places(layout) -> dict:
    """terminal -> (space, onglet, onglet partagé avec un autre terminal)."""
    out = {}
    for sp in (layout or {}).get("spaces", []):
        for tab in sp.get("tabs", []):
            terms = tab.get("terminals", [])
            for t in terms:
                out[t.get("terminal")] = (sp.get("name"), tab.get("name"), len(terms) > 1)
    return out


class AgentBoard:
    def __init__(self, hc, layout: Callable[[], dict | None] = herdr_app_ls,
                 home: Path | None = None, branches: BranchCache | None = None):
        self._hc = hc
        self._layout = layout
        self._home = home or Path.home()
        self._proc = PROC
        self._branches = branches or BranchCache()
        self._machine = socket.gethostname().lower()
        self._os, self._wsl = osid.cette_machine()
        self._spaces: list[dict] = []  # spaces de herdr-app : [{id, name, folder, machine}]
        self._infos: dict[Path, tuple] = {}  # journal -> ((taille, mtime), (modèle, activité))
        self._models_vus: dict[Path, str] = {}  # dernier modèle vu par journal : jamais de régression
        self._sessions: dict[int, tuple] = {}  # pid -> (mtime de sessions/<pid>.json, journal)
        self._vus: set[Path] = set()  # journaux référencés pendant le refresh en cours
        self._lock = threading.Lock()          # données (_agents, _prev)
        self._refresh_lock = threading.Lock()  # sérialise tout refresh
        self._gate = threading.Lock()          # coalescence des demandes
        self._wanted = False
        self._running = False
        self._agents: list[Agent] = []
        self._prev: dict[str, str] | None = None  # None tant qu'aucun instantané
        self._subs = []
        self._blocked_cbs = []
        hc.on_event(self._on_event)
        hc.on_state(self._on_state)

    def snapshot(self) -> list[Agent]:
        with self._lock:
            return list(self._agents)

    def subscribe(self, cb):
        self._subs.append(cb)

    def on_blocked(self, cb):
        self._blocked_cbs.append(cb)

    def _on_event(self, ev):
        if ev.get("method") in ("event", "missed"):
            self._refresh_safe()

    def _on_state(self, ok):
        if ok:
            self._refresh_safe()

    def _refresh_safe(self):
        """Un seul refresh en vol ; les demandes arrivées pendant ce temps
        donnent un unique rattrapage."""
        with self._gate:
            self._wanted = True
            if self._running:
                return
            self._running = True
        while True:
            with self._gate:
                if not self._wanted:
                    self._running = False
                    return
                self._wanted = False
            try:
                self.refresh()
            except Exception:
                log.exception("rafraîchissement du tableau des agents")

    def refresh(self):
        with self._refresh_lock:
            self._refresh()

    def spaces(self) -> list[dict]:
        with self._lock:
            return list(self._spaces)

    def _session(self, pid) -> Path | None:
        """Journal de la session du processus `pid`, relu seulement si sessions/<pid>.json change."""
        try:
            mt = (self._home / ".claude" / "sessions" / f"{pid}.json").stat().st_mtime_ns
        except OSError:
            self._sessions.pop(pid, None)
            return None
        hit = self._sessions.get(pid)
        # un None ne se garde pas : un Claude neuf écrit son .json tout de suite et son journal au
        # premier message, sans retoucher le .json (réserve de la revue UI v2)
        if hit and hit[0] == mt and hit[1] is not None and hit[1].is_file():
            return hit[1]
        path = resolve_session(pid, home=self._home)
        self._sessions[pid] = (mt, path)
        return path

    def _info(self, path: Path | None) -> tuple[str | None, str | None, int | None]:
        """(modèle, activité) du journal, relus seulement quand le journal a bougé. Un modèle déjà
        vu pour ce journal ne régresse pas vers None (fenêtre de lecture sans ligne assistant)."""
        if path is None:
            return None, None, None
        self._vus.add(path)
        try:
            st = path.stat()
            cle = (st.st_size, st.st_mtime_ns)
            cache = self._infos.get(path)
            if cache and cache[0] == cle:
                return cache[1]
            model, act, ctx = tail_info(path)
        except OSError:
            return None, None, None
        if model:
            self._models_vus[path] = model
        info = (model or self._models_vus.get(path), act, ctx)
        self._infos[path] = (cle, info)
        return info

    def _last_model(self, path: Path | None) -> str | None:
        return self._info(path)[0]

    def _model_activity(self, fg: dict, session: Path | None) -> tuple[str | None, str | None]:
        # le dernier modèle réellement utilisé (un /model change le transcript, pas l'argv) ;
        # l'argv ne sert qu'avant la première réponse
        m, act, _ = self._info(session)
        return m or model_from_argv(fg.get("argv")), act

    def _seuil(self, path: Path | None, cwd: str | None) -> int:
        """Seuil de compaction de l'agent : relu à chaque passe (un réglage changé suit), au modèle
        de SON journal (le journal est déjà en cache, `_info`)."""
        return compact_window(self._home, cwd, self._info(path)[0])

    def _subagents(self, records, session: Path | None, cwd: str | None = None) -> list:
        if not isinstance(records, dict):
            return []
        out = []
        for k, r in records.items():
            if not (isinstance(r, dict) or r is None):
                continue
            path = subagent_path(session, str(k)) if session else None
            ctx = self._info(path)[2]
            title = (r or {}).get("title") or ""
            role = role_inferred(title)
            out.append({"id": str(k), "title": title,
                        "state": (r or {}).get("state") or "unknown",
                        "model": self._last_model(path),
                        "context": ctx, "context_max": self._seuil(path, cwd) if ctx is not None else None,
                        "role": role, "role_infere": role is not None})
        return out

    def _refresh(self):
        res = self._hc.request("terminal.list", {})
        try:
            layout = self._layout()
            places = _places(layout)
            spaces = [{"id": sp.get("id"), "name": sp.get("name"), "folder": sp.get("folder"),
                       "machine": sp.get("machine"),
                       "tabs": [{"id": t.get("id"), "name": t.get("name")}
                                for t in sp.get("tabs") or [] if t.get("id")]}
                      for sp in (layout or {}).get("spaces", []) if sp.get("id")]
        except Exception:
            log.warning("herdr-app ls indisponible, liste plate")
            places, spaces = {}, []
        agents = []
        self._vus = set()
        pids = set()
        for t in res.get("terminals", []):
            addr = t.get("address", "")
            if t.get("state") != "live" or "/" in addr:
                continue
            st = t.get("status") or {}
            fg = t.get("foreground") or {}
            space, tab, partage = places.get(addr, (None, None, False))
            program = st.get("app") or fg.get("name") or ""
            claude = program in ("claude-code", "claude")
            pid = fg.get("pid") or t.get("pid")
            cwd = cwd_reel(pid, self._proc) or t.get("cwd") or ""
            session = None
            if claude and fg.get("pid"):
                pids.add(fg["pid"])
                session = self._session(fg["pid"])
            model, activity = self._model_activity(fg, session)
            context = self._info(session)[2]
            turn = t.get("turn") or 0
            seen = label_seen(t.get("labels"))
            agents.append(Agent(
                address=addr, name=t.get("name"), title=t.get("title") or "",
                cwd=cwd, program=program,
                pid=pid, state=etat_vu(st.get("state") or "unknown", turn, seen), seen=seen,
                kind=st.get("kind"), msg=st.get("msg") or "",
                space=space, tab=tab, turn=turn,
                since_ms=t.get("status_ms") if isinstance(t.get("status_ms"), int) else None,
                model=model, activity=activity, dir=short_dir(cwd),
                context=context, context_max=self._seuil(session, cwd) if context is not None else None,
                subagents=self._subagents(t.get("records"), session, cwd),
                branch=self._branches.get(cwd), machine=self._machine,
                os="" if "/" in addr else self._os, os_wsl=False if "/" in addr else self._wsl,  # hc ne dit pas l'OS d'une machine distante
                role=role_of(t.get("template"), claude, t.get("name")),
                cols=t.get("cols") if isinstance(t.get("cols"), int) and t.get("cols") > 0 else None,
                parent=t.get("parent") if isinstance(t.get("parent"), str) and t.get("parent") else None,
                pinned=isinstance(t.get("labels"), dict) and bool(t["labels"].get("herdr.pinned")),
                label=libelle(t.get("name"), space or lieu_du_dossier(cwd, self._home), tab,
                              t.get("title") or "", program, partage)))
        agents.sort(key=sort_key)
        # élagage : ni journal ni session qui ne soit plus référencé
        for cache in (self._infos, self._models_vus):
            for path in [p for p in cache if p not in self._vus]:
                del cache[path]
        for pid in [p for p in self._sessions if p not in pids]:
            del self._sessions[pid]
        with self._lock:
            prev, self._prev = self._prev, {a.address: a.state for a in agents}
            changed = agents != self._agents
            self._agents = agents
            self._spaces = spaces
        if prev is not None:
            for a in agents:
                if a.state == "blocked" and prev.get(a.address) != "blocked":
                    self._call(self._blocked_cbs, a)
        if changed:
            self._call(self._subs, list(agents))

    @staticmethod
    def _call(cbs, arg):
        for cb in cbs:
            try:
                cb(arg)
            except Exception:
                log.exception("callback du tableau des agents a levé")
