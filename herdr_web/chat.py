"""Conversation d'un agent Claude Code, lue dans son journal de session (.jsonl)."""
import base64
import binascii
import functools
import hashlib
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Message tapé par hc dans le prompt d'un agent (compte rendu d'un enfant, envoi d'un autre terminal,
# alerte « bloqué / terminé ») : une enveloppe ENTIÈRE `<hc from="<adresse> <nom>">…</hc>` ou `<hc>…</hc>`.
# Une enveloppe parmi du texte (un collage de l'utilisateur) ne matche pas : l'ancre ^…$ et le test « pas d'autre
# <hc » écartent tout ce qui n'est pas une enveloppe seule. Mesuré le 2026-11-15 sur 828 journaux.
_HC = re.compile(r"^\s*<hc(?P<attrs>(?:\s+[a-z]+=\"[^\"]*\")*)\s*>(?P<corps>.*)</hc>\s*$", re.S)
_HC_FROM = re.compile(r'\sfrom="([^"]*)"')
_HC_ALERTE = re.compile(r"^(t[0-9A-Za-z]+)(?: (\S+))? (?:is blocked|exited)\b")


def image_root() -> Path:
    """Dossier où herdr-core dépose les photos (`terminal.put`) : sous le HOME du service, relu à
    chaque appel (avant : /home/user/ en dur dans la regex, un autre utilisateur n'y voyait rien)."""
    return Path.home() / ".local/state/herdr-core"


@functools.lru_cache(maxsize=4)
def _image_re(root: str) -> re.Pattern:
    return re.compile(re.escape(root) + r"/\S+?/files/\S+?\.(?:png|jpe?g|gif|webp)", re.I)


# Image COLLÉE dans Claude Code (Ctrl+V au PC) : un bloc `image` base64 dans la ligne user, à côté
# du texte « [Image #1] » ; la page n'en montrait rien. Gardée en mémoire sous l'empreinte de son
# base64 (le journal est relu à chaque tail, donc le cache se reremplit), servie par /api/collee/.
COLLEE_MAX = 8 * 1024 * 1024  # octets décodés, comme une photo envoyée
COLLEES_TOTAL = 32 * 1024 * 1024  # octets base64 gardés, les plus anciens sortent d'abord
COLLEE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
_collees: OrderedDict = OrderedDict()  # empreinte -> (type, base64)
_collees_taille = 0
_collees_lock = threading.Lock()


def _collee(b: dict) -> str | None:
    """Range l'image collée d'un bloc `image` ; rend son empreinte, None si illisible ou hors liste."""
    global _collees_taille
    src = b.get("source")
    if not isinstance(src, dict) or src.get("type") != "base64":
        return None
    mt, data = src.get("media_type"), src.get("data")
    if mt not in COLLEE_TYPES or not isinstance(data, str) or not data or len(data) > COLLEE_MAX * 4 // 3 + 4:
        return None
    cle = hashlib.sha256(data.encode("utf-8", "replace")).hexdigest()
    with _collees_lock:
        if cle not in _collees:
            _collees[cle] = (mt, data)
            _collees_taille += len(data)
    return cle


def _garder(items: list) -> list:
    """Après une lecture : les images collées rendues passent en tête, dans l'ordre du fil, puis
    le cache est borné. Pas à l'insertion : tail() lit du plus récent au plus ancien, et une
    éviction pendant la lecture sortait justement les plus récentes."""
    global _collees_taille
    cles = [i.collee for i in items if i.collee]
    if not cles and _collees_taille <= COLLEES_TOTAL:
        return items
    with _collees_lock:
        for c in cles:
            if c in _collees:
                _collees.move_to_end(c)
        while _collees_taille > COLLEES_TOTAL and len(_collees) > 1:
            _, (_, vieux) = _collees.popitem(last=False)
            _collees_taille -= len(vieux)
    return items


def image_collee(cle: str) -> tuple[str, bytes] | None:
    """(type, octets) d'une image collée vue dans un journal, None si inconnue ou base64 invalide."""
    with _collees_lock:
        v = _collees.get(cle)
    if v is None:
        return None
    try:
        return v[0], base64.b64decode(v[1], validate=True)
    except (binascii.Error, ValueError):
        return None


CONTRAT_FIN = "=== FIN DU CONTRAT — ce qui suit est ta tâche ==="
_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass
class ChatItem:
    role: str  # moi | agent | outil | resultat | attente | retrait (file d'attente de Claude Code)
    #            | shell (`!cmd` : tool = input | stdout | stderr) | commande (`/cmd`) | notif (tâche de fond)
    #            | hc (message tapé par hc : de = adresse émettrice, nom, text = le corps)
    text: str
    tool: str | None = None
    image: str | None = None
    ts: str = ""
    ref: str | None = None  # id du tool_use : relie un « resultat » à son « outil »
    error: bool = False  # resultat en erreur (is_error)
    collee: str | None = None  # empreinte d'une image collée dans Claude Code (/api/collee/<empreinte>)
    de: str | None = None  # hc : adresse du terminal émetteur (None : herdr lui-même)
    nom: str | None = None  # hc : nom de l'agent émetteur, s'il est donné


def resolve_session(pid: int, home: Path = Path.home()) -> Path | None:
    try:
        info = json.loads((home / ".claude" / "sessions" / f"{pid}.json").read_text())
        projects = home / ".claude" / "projects"
        nom = f"{info['sessionId']}.jsonl"
        path = projects / re.sub(r"[^A-Za-z0-9]", "-", info["cwd"]) / nom
        if path.is_file():
            return path
        return next((c for c in sorted(projects.glob(f"*/{nom}")) if c.is_file()), None)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def subagent_path(session: Path, agent_id: str) -> Path | None:
    """Journal d'un sous-agent, à côté de celui de sa session parente :
    <dossier du projet>/<session>/subagents/agent-<id>.jsonl. None s'il n'existe pas."""
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        return None
    f = session.parent / session.stem / "subagents" / f"agent-{agent_id}.jsonl"
    return f if f.is_file() else None


def resolve_subagent(pid: int, agent_id: str, home: Path = Path.home()) -> Path | None:
    """Journal d'un sous-agent in-process : ~/.claude/projects/*/<session>/subagents/agent-<id>.jsonl,
    la session étant lue dans ~/.claude/sessions/<pid de claude>.json (comme herdr-sousagent.py).
    Cherché d'abord à côté du journal de la session, puis dans tous les projets."""
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        return None
    session = resolve_session(pid, home=home)
    direct = subagent_path(session, agent_id) if session else None
    if direct:
        return direct
    try:
        sid = json.loads((home / ".claude" / "sessions" / f"{pid}.json").read_text())["sessionId"]
        if not isinstance(sid, str) or not _AGENT_ID.fullmatch(sid.replace("-", "_")):
            return None
        trouves = sorted((home / ".claude" / "projects").glob(
            f"*/{sid}/subagents/agent-{agent_id}.jsonl"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return next((c for c in trouves if c.is_file()), None)


def strip_contract(items: list) -> list:
    """Retire d'une consigne le contrat injecté en tête de chaque prompt de sous-agent."""
    for it in items:
        if it.role == "moi" and CONTRAT_FIN in it.text:
            it.text = it.text.split(CONTRAT_FIN, 1)[1].strip()
    return items


_RESUME = ("command", "file_path", "pattern", "description", "prompt", "url", "query")


def _tool_text(name: str, inp: dict) -> str:
    for cle in _RESUME:  # même ordre que herdr-sousagent.py (resume_outil)
        if isinstance(inp.get(cle), str) and inp[cle]:
            return f"{name}: {inp[cle]}"
    return name


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "[image]" if b.get("type") == "image" else "")
                        for b in content if isinstance(b, dict)).strip()
    return ""


# un message tapé pendant un tour n'est pas une ligne « user » : Claude Code le range en
# `attachment` queued_command (mesuré le 2026-11-14 : 170 dans 40 journaux, aucun réécrit ensuite
# en ligne user). Seuls ceux de l'utilisateur s'affichent ; notifications de tâche et rapports d'agents non.
_FILE_ORIGINES = ("human", "channel")


def _hc(text: str, ts: str) -> ChatItem | None:
    """Un item `hc` si le texte est ENTIÈREMENT une enveloppe hc, sinon None (le message reste à l'utilisateur)."""
    m = _HC.match(text)
    if not m or "<hc" in m.group("corps"):
        return None
    f = _HC_FROM.search(m.group("attrs") or "")
    de, _, nom = (f.group(1).strip() if f else "").partition(" ")
    corps = m.group("corps").strip()
    if not de and (a := _HC_ALERTE.match(corps)):  # `<hc>t44 harmonie-web-2 is blocked …</hc>` : l'émetteur est dans le texte
        de, nom = a.group(1), a.group(2) or ""
    return ChatItem("hc", corps, None, None, ts, de=de or None, nom=nom.strip() or None)


def _moi(text: str, ts: str) -> ChatItem:
    img = _image_re(str(image_root())).search(text)
    return ChatItem("moi", text, None, img.group(0) if img else None, ts)


def _balise(text: str, nom: str) -> str | None:
    m = re.search(rf"<{nom}>(.*?)</{nom}>", text, re.S)
    return m.group(1) if m else None


def _pseudo(text: str, ts: str) -> list[ChatItem] | None:
    """Les pseudo-balises que Claude Code écrit en message « user » : commande `!` (bash-input, puis
    bash-stdout/bash-stderr), commande `/` (command-name) et sa sortie locale, fin de tâche de fond.
    None si le texte n'en est pas une (c'est alors une vraie bulle de l'utilisateur). Mesuré le 2026-11-15
    sur 803 journaux : bash-input 39, bash-stdout 37, command-name 360, local-command-stdout 298,
    task-notification 674 ; local-command-caveat, channel et system-reminder sont déjà isMeta."""
    t = text.lstrip()
    if t.startswith("<hc"):
        h = _hc(text, ts)
        return [h] if h else None
    if t.startswith("<bash-input>"):
        cmd = _balise(t, "bash-input")
        return [ChatItem("shell", cmd.strip(), "input", None, ts)] if cmd and cmd.strip() else []
    if t.startswith(("<bash-stdout>", "<bash-stderr>")):
        return [ChatItem("shell", s, nom, None, ts)
                for nom in ("stdout", "stderr") if (s := (_balise(t, f"bash-{nom}") or "").strip("\n")).strip()]
    if t.startswith("<local-command-stdout>"):
        s = (_balise(t, "local-command-stdout") or "").strip("\n")
        return [ChatItem("shell", s, "stdout", None, ts)] if s.strip() else []
    if t.startswith(("<command-name>", "<command-message>")):
        nom = (_balise(t, "command-name") or "").strip()
        if not nom:
            nom = "/" + (_balise(t, "command-message") or "").strip().lstrip("/")
        args = (_balise(t, "command-args") or "").strip()
        return [ChatItem("commande", f"{nom} {args}".strip(), None, None, ts)]
    if t.startswith("<task-notification>"):
        r = (_balise(t, "summary") or "").strip()
        return [ChatItem("notif", r or "tâche de fond terminée", None, None, ts)]
    return None


def _parse(line: dict) -> list[ChatItem]:
    kind = line.get("type")
    if kind == "queue-operation":
        # message tapé pendant un tour : Claude Code le montre en grisé tant qu'il n'est pas pris
        # (« enqueue »), puis le retire (« remove ») ; la page fait de même. Seul un texte nu vient
        # de l'utilisateur : <task-notification>, <agent-message>, <hc from=…>, <channel> sont des machines
        # ou Discord (mesuré le 2026-11-14 : 68 textes nus sur 360 mises en file). Un « dequeue »
        # (fin de tour) ne dit pas lequel : la page retire l'attente quand le message arrive.
        c = line.get("content")
        op = line.get("operation")
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<") and op in ("enqueue", "remove"):
            return [ChatItem("attente" if op == "enqueue" else "retrait", c, None, None,
                             line.get("timestamp") or "")]
        return []
    if kind == "attachment" and not line.get("isSidechain"):
        a = line.get("attachment")
        if (isinstance(a, dict) and a.get("type") == "queued_command"
                and (a.get("origin") or {}).get("kind") in _FILE_ORIGINES
                and isinstance(a.get("prompt"), str) and a["prompt"].strip()):
            ts = line.get("timestamp") or a.get("timestamp") or ""
            p = _pseudo(a["prompt"], ts)
            return [_moi(a["prompt"], ts)] if p is None else p
        return []
    if kind not in ("user", "assistant") or line.get("isMeta"):
        return []
    ts = line.get("timestamp", "")
    content = (line.get("message") or {}).get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    items = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and kind == "user":
            p = _pseudo(b.get("text", ""), ts)
            items.extend([_moi(b.get("text", ""), ts)] if p is None else p)
        elif t == "image" and kind == "user":
            cle = _collee(b)
            if cle:
                items.append(ChatItem("moi", "", None, None, ts, collee=cle))
        elif t == "text":
            items.append(ChatItem("agent", b.get("text", ""), None, None, ts))
        elif t == "tool_use":
            name = b.get("name", "")
            items.append(ChatItem("outil", _tool_text(name, b.get("input") or {}), name, None, ts,
                                  ref=b.get("id")))
        elif t == "tool_result":
            items.append(ChatItem("resultat", _result_text(b.get("content")), None, None, ts,
                                  ref=b.get("tool_use_id"), error=bool(b.get("is_error"))))
    return items


QUESTION_FENETRE = 4 * 1024 * 1024  # fin du journal relue pour trouver un AskUserQuestion en attente
_Q_TEXTE, _Q_APERCU, _Q_OPTIONS = 2000, 8000, 9


def _texte(v, n: int) -> str:
    return v[:n] if isinstance(v, str) else ""


def question_en_attente(path: Path, fenetre: int = QUESTION_FENETRE) -> list[dict] | None:
    """Les questions du dernier appel AskUserQuestion du journal qui n'a pas encore de résultat
    (`tool_result` au même id) : [{question, header, multiSelect, options: [{label, description,
    preview}]}], champs nettoyés et bornés. None s'il n'y en a pas ou si le journal est illisible.
    Mesuré le 2026-11-15 (agent d'essai bloqué, Claude Code 2.1.325) : l'appel est écrit dans le
    journal avant la réponse, c'est sa dernière ligne."""
    try:
        with path.open("rb") as f:
            taille = os.fstat(f.fileno()).st_size
            f.seek(max(0, taille - fenetre))
            data = f.read()
    except OSError:
        return None
    if taille > fenetre:
        data = data[data.find(b"\n") + 1:]  # première ligne coupée
    appel, repondus = None, set()
    for raw in data.splitlines():
        if b"AskUserQuestion" not in raw and b"tool_result" not in raw:
            continue
        try:
            ligne = json.loads(raw)
        except ValueError:
            continue
        contenu = (ligne.get("message") or {}).get("content") if isinstance(ligne, dict) else None
        for b in contenu if isinstance(contenu, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                appel = (b.get("id"), (b.get("input") or {}).get("questions"))
            elif b.get("type") == "tool_result":
                repondus.add(b.get("tool_use_id"))
    if appel is None or appel[0] in repondus or not isinstance(appel[1], list):
        return None
    out = []
    for q in appel[1]:
        if not isinstance(q, dict) or not isinstance(q.get("options"), list):
            return None
        out.append({"question": _texte(q.get("question"), _Q_TEXTE), "header": _texte(q.get("header"), 200),
                    "multiSelect": q.get("multiSelect") is True,
                    "options": [{"label": _texte(o.get("label"), 500), "description": _texte(o.get("description"), _Q_TEXTE),
                                 "preview": _texte(o.get("preview"), _Q_APERCU)}
                                for o in q["options"][:_Q_OPTIONS] if isinstance(o, dict)]})
    return out or None


_BLOC = 64 * 1024
_TAIL_MAX = 32 * 1024 * 1024  # plafond de lecture de tail() : au-delà, le fil rendu est plus court


class ChatReader:
    def __init__(self, path: Path, offset: int = 0):
        self.path = path
        self._offset = offset  # read_new() repart de là (fin de ligne rendue par .offset)
        self._inode: int | None = None
        self.reset = False  # vrai si la dernière lecture est repartie de 0 (journal tronqué/remplacé)
        self.skipped = 0  # lignes écartées (illisibles ou atypiques), cumul
        self.lus = 0  # octets lus par la dernière lecture (mesure, et sonde des tests)

    def _convert(self, raw: bytes) -> list[ChatItem]:
        try:
            return _parse(json.loads(raw))
        except Exception:
            self.skipped += 1
            return []

    def read_new(self) -> list[ChatItem]:
        """Ce qui a été ajouté depuis l'offset. Journal tronqué ou remplacé : `reset` vrai et les
        derniers éléments par tail(), jamais une analyse depuis 0 (293 Mo lus sur un gros fil)."""
        self.reset = False
        with self.path.open("rb") as f:
            st = os.fstat(f.fileno())
            if self._inode is not None and (st.st_ino != self._inode or st.st_size < self._offset):
                log.warning("journal %s tronqué ou remplacé : relecture de la fin", self.path)
                remplace = True
            else:
                remplace = False
                self._inode = st.st_ino
                f.seek(self._offset)
                data = f.read()
                self.lus = len(data)
        if remplace:
            items = self.tail()
            self.reset = True
            return items
        end = data.rfind(b"\n") + 1  # une dernière ligne incomplète reste à relire
        items = []
        for raw in data[:end].splitlines():
            items.extend(self._convert(raw))
        self._offset += end
        return _garder(items)

    @property
    def offset(self) -> int:
        return self._offset

    def tail(self, n: int = 200, end: int | None = None) -> list[ChatItem]:
        """Les n derniers éléments, en remontant le fichier bloc par bloc depuis la fin.

        Chaque octet est lu et analysé une fois au plus, et jamais plus de _TAIL_MAX octets
        (avant : blocs recollés en tête, comptes refaits et relecture depuis la fin à chaque
        doublement, 5,3 s et +87 Mo pour 140 Mo). `end` borne la lecture à cet offset."""
        self.reset = False
        lus = 0
        groupes = []  # éléments par ligne, de la plus récente à la plus ancienne
        total = 0
        with self.path.open("rb") as f:
            st = os.fstat(f.fileno())
            self._inode = st.st_ino
            pos = st.st_size if end is None else min(end, st.st_size)
            fin = None  # offset de fin de la dernière ligne complète
            reste = b""  # début de la zone déjà lue, dont le début de ligne n'est pas encore lu
            while pos > 0 and total < n and lus < _TAIL_MAX:
                debut = max(0, pos - _BLOC)
                f.seek(debut)
                bloc = f.read(pos - debut)
                lus += len(bloc)
                pos = debut
                buf = bloc + reste
                if fin is None:
                    k = buf.rfind(b"\n")
                    if k < 0:
                        reste = buf
                        continue
                    fin = pos + k + 1
                    buf = buf[:k + 1]
                if pos > 0:
                    j = buf.find(b"\n")
                    if j < 0:
                        reste = buf
                        continue
                    reste, corps = buf[:j + 1], buf[j + 1:]
                else:
                    reste, corps = b"", buf
                for raw in reversed(corps.splitlines()):
                    its = self._convert(raw)
                    if its:
                        groupes.append(its)
                        total += len(its)
                        if total >= n:
                            break
            self._offset = fin if fin is not None else pos
        self.lus = lus
        return _garder([i for g in reversed(groupes) for i in g][-n:])
