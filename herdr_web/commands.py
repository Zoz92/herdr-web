"""Commandes `/` de Claude Code proposées dans le champ de saisie de la page.

Quatre sources : intégrées (figées dans claude_commands.json, mesurées sur claude 2.1.325 depuis
la doc officielle), skills et commandes de l'utilisateur, celles des plugins activés
(`enabledPlugins`, préfixées `plugin:nom`), celles du projet de l'agent. Cache 60 s par dossier.
"""
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

BUILTINS = Path(__file__).resolve().parent / "claude_commands.json"
TTL = 60
_DESC_MAX = 200


def frontmatter(texte: str) -> dict | None:
    """Sous-ensemble YAML d'un frontmatter `---` : `clé: valeur`, valeurs pliées `>`/`|`.
    None si absent ou jamais fermé."""
    if not texte.startswith("---"):
        return None
    lignes = texte.splitlines()[1:]
    try:
        fin = lignes.index("---")
    except ValueError:
        return None
    out, cle = {}, None
    for l in lignes[:fin]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", l)
        if m:
            cle, val = m.group(1), m.group(2).strip()
            if val in (">", ">-", "|", "|-", ">+", "|+"):
                val = ""
            out[cle] = val.strip("\"'") if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'" else val
        elif cle and l.startswith((" ", "\t")):
            out[cle] = (out[cle] + " " + l.strip()).strip()
    return out


def _ligne(s: str) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= _DESC_MAX else s[:_DESC_MAX - 1] + "…"


def _lire(f: Path) -> str | None:
    try:
        return f.read_text(errors="replace")
    except OSError:
        return None


def _skills(racine: Path, prefixe: str, source: str) -> list:
    out = []
    for f in sorted(racine.glob("*/SKILL.md")):
        fm = frontmatter(_lire(f) or "")
        if not fm:
            continue  # frontmatter absent ou cassé : ignoré
        nom = fm.get("name") or f.parent.name
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", nom):
            continue
        out.append({"name": prefixe + nom, "description": _ligne(fm.get("description")),
                    "source": source, "interactive": False, "kind": "skill"})
    return out


def _commands(racine: Path, prefixe: str, source: str) -> list:
    out = []
    for f in sorted(racine.glob("*.md")):
        texte = _lire(f)
        if texte is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", f.stem):
            continue
        fm = frontmatter(texte)
        if fm is not None:
            desc = fm.get("description", "")
        else:
            desc = next((l for l in texte.splitlines() if l.strip()), "")
        out.append({"name": prefixe + f.stem, "description": _ligne(desc), "source": source,
                    "interactive": False, "kind": "command"})
    return out


class CommandIndex:
    def __init__(self, home: Path | None = None, clock=time.monotonic, builtins: Path = BUILTINS):
        self._home = Path(home) if home else Path.home()
        self._clock, self._builtins = clock, builtins
        self._cache: dict[str, tuple[float, list]] = {}
        self._vus: set[str] = set()

    def _builtin(self) -> list:
        try:
            d = json.loads(self._builtins.read_text())
        except (OSError, ValueError):
            log.warning("commandes intégrées illisibles : %s", self._builtins)
            return []
        out = []
        for c in d.get("commands", []):
            out.append({"name": c["name"], "description": _ligne(c.get("description")),
                        "source": "builtin", "interactive": bool(c.get("interactive")),
                        "kind": c.get("kind", "builtin"), "args": c.get("args", "")})
            if c["name"] == "model":
                for forme in d.get("model_forms", []):
                    out.append({"name": f"model {forme}", "description": f"Passer au modèle {forme}",
                                "source": "builtin", "interactive": False, "kind": "builtin"})
        return out

    def _plugins(self) -> list:
        c = self._home / ".claude"
        try:
            actifs = json.loads((c / "settings.json").read_text()).get("enabledPlugins") or {}
            installes = json.loads((c / "plugins" / "installed_plugins.json").read_text()).get("plugins") or {}
        except (OSError, ValueError, AttributeError):
            return []
        out = []
        for cle, actif in sorted(actifs.items()):
            if actif is not True or not isinstance(installes.get(cle), list):
                continue
            entrees = [e for e in installes[cle] if isinstance(e, dict) and e.get("installPath")]
            entrees.sort(key=lambda e: e.get("scope") != "user")  # l'installation utilisateur d'abord
            if not entrees:
                continue
            racine = Path(entrees[0]["installPath"])
            prefixe = cle.split("@", 1)[0] + ":"
            out += _skills(racine / "skills", prefixe, "plugin")
            out += _commands(racine / "commands", prefixe, "plugin")
        return out

    def _charger(self, cwd: str) -> list:
        c = self._home / ".claude"
        sources = {
            "builtin": self._builtin(),
            "user": _skills(c / "skills", "", "user") + _commands(c / "commands", "", "user"),
            "plugin": self._plugins(),
            "project": [],
        }
        if cwd and Path(cwd).resolve() != self._home.resolve():
            p = Path(cwd) / ".claude"
            sources["project"] = _skills(p / "skills", "", "project") + _commands(p / "commands", "", "project")
        if cwd not in self._vus:  # un inventaire se lit à son compte
            self._vus.add(cwd)
            log.info("commandes / pour %s : %s", cwd or "?",
                     " ".join(f"{k}={len(v)}" for k, v in sources.items()))
        vus, out = set(), []
        for liste in sources.values():  # la première source qui nomme une commande l'emporte
            for cmd in liste:
                if cmd["name"] not in vus:
                    vus.add(cmd["name"])
                    out.append(cmd)
        return out

    def get(self, cwd: str) -> list:
        now = self._clock()
        hit = self._cache.get(cwd)
        if hit and now - hit[0] < TTL:
            return hit[1]
        cmds = self._charger(cwd)
        self._cache[cwd] = (now, cmds)
        return cmds
