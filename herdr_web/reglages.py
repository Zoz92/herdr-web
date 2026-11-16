"""Réglages partagés entre appareils (fichier sous ~/.config/herdr-web/) et ce que la page en montre :
paramètres réels de la voix (Handy, ffmpeg), version déployée, session."""
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import threading
from pathlib import Path

from herdr_web import actions, textes

log = logging.getLogger(__name__)

DEFAUTS = {"handy": True, "langue": textes.DEFAUT}  # voix activée, page en anglais, tant que rien n'est écrit
HANDY_SETTINGS = Path.home() / ".local" / "share" / "com.pais.handy" / "settings_store.json"
HANDY_DEPOT = "https://github.com/cjpais/Handy"  # dépôt officiel : identifiant com.pais.handy (tauri.conf.json)

# Paramètres de transcription de herdr-web, copiés de Handy puis modifiables depuis la page, sans
# toucher à Handy. `handy --transcribe-file` (cli.rs, 0.9.8 et 0.10.0) accepte `--model` ; langue et
# vocabulaire n'ont pas d'option : Handy les lit dans sa propre configuration.
VOIX_DEFAUT = {"modele": None, "langue": None, "vocabulaire": [], "source": None}
_MODELE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,199}")
_LANGUE = re.compile(r"auto|[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?")
MOT_MAX, MOTS_MAX = 64, 200
_VERSION_STORE = re.compile(r"/nix/store/[a-z0-9]{32}-handy-([0-9][A-Za-z0-9.+-]*)/")
_CACHE_SW = re.compile(r"""const\s+CACHE\s*=\s*["']([^"']{1,80})["']""")


NOM_MAX = 60
NOM_DEFAUT = {"fr": "Vous", "en": "You"}  # compte sans nom lisible : « Vous » dans la langue de la page


class ErreurVoix(ValueError):
    """Paramètre de transcription refusé ; `motif` et `params` : ce que la page traduit (static/i18n)."""

    def __init__(self, texte, motif, **params):
        super().__init__(texte)
        self.motif, self.params = motif, params


def nom_compte(getpwuid=pwd.getpwuid, uid=os.getuid) -> str:
    """Nom affiché par défaut : le nom complet du compte qui fait tourner le service (GECOS, 1re partie),
    sinon son login, « Vous » si les deux sont vides ou illisibles."""
    try:
        pw = getpwuid(uid())
    except (KeyError, OSError):
        return "Vous"
    return (pw.pw_gecos or "").split(",")[0].strip()[:NOM_MAX] or (pw.pw_name or "").strip()[:NOM_MAX] or "Vous"


class Reglages:
    """Un petit JSON ; écrit en entier dans un fichier temporaire puis renommé (jamais à moitié)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def lire(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        out = dict(DEFAUTS)
        if isinstance(d, dict):
            out.update({k: d[k] for k in DEFAUTS if isinstance(d.get(k), type(DEFAUTS[k]))})
        if out["langue"] not in textes.LANGUES:  # écrit à la main : la langue par défaut
            out["langue"] = textes.DEFAUT
        n = d.get("nom_affiche") if isinstance(d, dict) else None
        compte = nom_compte()
        if compte == NOM_DEFAUT["fr"]:
            compte = NOM_DEFAUT[out["langue"]]
        out["nom_affiche"] = n.strip()[:NOM_MAX] if isinstance(n, str) and n.strip() else compte
        try:
            out["voix"] = verifier_voix(d.get("voix") if isinstance(d, dict) else None)
        except ValueError:
            out["voix"] = dict(VOIX_DEFAUT)  # fichier abîmé à la main : la voix de Handy, telle quelle
        return out

    def ecrire(self, **vals) -> dict:
        with self._lock:
            try:
                d = json.loads(self.path.read_text())
            except (OSError, ValueError):
                d = {}
            d = d if isinstance(d, dict) else {}
            d.update({k: v for k, v in self.lire().items() if k in DEFAUTS and k not in d})
            d.update(vals)
            if isinstance(d.get("nom_affiche"), str):
                d["nom_affiche"] = d["nom_affiche"].strip()
            if "nom_affiche" in d and not (isinstance(d["nom_affiche"], str) and d["nom_affiche"].strip()):
                del d["nom_affiche"]  # champ vide : retour au nom du compte
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(d, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return self.lire()

    def quota_script(self) -> str | None:
        """Script du segment quota de la barre d'état : réglage du serveur, posé à la main dans le fichier
        (la page ne le lit ni ne l'écrit). Chemin absolu, sinon None (segment absent)."""
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        v = d.get("quota_script") if isinstance(d, dict) else None
        v = v.strip() if isinstance(v, str) else ""
        return v if os.path.isabs(v) else None

    def voix_active(self) -> bool:
        return self.lire()["handy"]

    def langue(self) -> str:
        return self.lire()["langue"]


def verifier_voix(v) -> dict:
    """Paramètres de transcription reçus (page) ou relus (fichier) : ValueError au moindre écart."""
    if v is None:
        return dict(VOIX_DEFAUT)
    if not isinstance(v, dict) or set(v) - set(VOIX_DEFAUT):
        raise ValueError("voix : objet {modele, langue, vocabulaire} attendu")
    out = dict(VOIX_DEFAUT)
    for k, motif in (("modele", _MODELE), ("langue", _LANGUE)):
        x = v.get(k)
        if isinstance(x, str):
            x = x.strip() or None
        if x is not None and not (isinstance(x, str) and motif.fullmatch(x)):
            raise ErreurVoix(f"voix.{k} : identifiant invalide", "voix_" + k)
        out[k] = x
    mots = v.get("vocabulaire", [])
    if not isinstance(mots, list) or len(mots) > MOTS_MAX:
        raise ErreurVoix(f"voix.vocabulaire : liste de {MOTS_MAX} mots au plus", "voix_liste", max=MOTS_MAX)
    propres = []
    for m in mots:
        if not isinstance(m, str) or len(m.strip()) > MOT_MAX or any(ord(c) < 32 or ord(c) == 127 for c in m):
            raise ErreurVoix(f"voix.vocabulaire : mots de {MOT_MAX} caractères au plus, sans caractère de contrôle",
                             "voix_mot", max=MOT_MAX)
        if m.strip() and m.strip() not in propres:
            propres.append(m.strip())
    out["vocabulaire"] = propres
    src = v.get("source")
    if src not in (None, "handy", "page"):
        raise ValueError("voix.source : handy ou page")
    out["source"] = src
    return out


def handy_import(settings=None) -> dict:
    """Modèle, langue et mots personnalisés lus dans le store de Handy — ces trois clés seulement,
    jamais une clé d'API. Un mot ou un identifiant hors format est écarté, pas copié.
    OSError / ValueError si le store est absent ou illisible."""
    s = json.loads(Path(settings or HANDY_SETTINGS).read_text()).get("settings")
    if not isinstance(s, dict):
        raise ValueError("pas d'objet settings dans le store de Handy")
    out = dict(VOIX_DEFAUT, source="handy")
    for cle, k, motif in (("selected_model", "modele", _MODELE), ("selected_language", "langue", _LANGUE)):
        x = s.get(cle)
        out[k] = x if isinstance(x, str) and motif.fullmatch(x) else None
    mots = s.get("custom_words")
    mots = [m.strip() for m in mots if isinstance(m, str)] if isinstance(mots, list) else []
    mots = [m for m in mots if m and len(m) <= MOT_MAX and not any(ord(c) < 32 or ord(c) == 127 for c in m)]
    out["vocabulaire"] = list(dict.fromkeys(mots))[:MOTS_MAX]
    return out


TACHE_INSTALLER_HANDY = (
    f"Installe Handy (transcription vocale locale, open source : {HANDY_DEPOT}) sur CET ordinateur, pour que "
    "herdr-web transcrive les messages vocaux avec `handy --transcribe-file`.\n"
    "1. Détecte le système (/etc/os-release) et dis si Handy est déjà installé.\n"
    "2. Installe-le par la voie propre au système. NixOS ou GLF-OS : configuration déclarative seulement "
    "(entrée de flake github:cjpais/Handy épinglée sur un tag de release, ou paquet nixpkgs), jamais nix-env, "
    "go install ni curl | sh ; montre le diff et demande-moi avant nixos-rebuild. Autre distribution : paquet "
    "officiel (.deb de la page des releases, installé par apt) ou release signée dont tu vérifies la signature "
    "(minisign, clé publique de src-tauri/tauri.conf.json) ; jamais curl | sh.\n"
    "3. Configure-le pour --transcribe-file : un modèle installé et sélectionné (handy --list-models ; s'il n'y "
    "en a aucun, dis comment le télécharger depuis l'application), et ~/.config/autostart/Handy.desktop dont la "
    "ligne Exec= donne le chemin absolu du binaire (herdr-web le lit là).\n"
    "4. Vérifie : fabrique un WAV court 16 kHz mono 16 bits (ffmpeg ; une phrase synthétisée si espeak est "
    "installé), lance handy --transcribe-file <wav> --json et colle la sortie.\n"
    "5. Rends compte : système détecté, voie choisie, fichiers modifiés, sortie de la vérification. "
    "Ne lis ni n'affiche les clés d'API du fichier de réglages de Handy."
)


def handy_info(desktop=None, settings=None, which=shutil.which) -> dict:
    """Ce que la voix utilise vraiment. Rien n'est exécuté : la version vient du chemin du store,
    modèle et langue de la config de Handy (seulement ces deux clés, jamais une clé d'API).
    `present` : le binaire nommé par le .desktop existe et s'exécute — sinon la page suggère Handy."""
    desktop, settings = desktop or actions.DESKTOP, settings or HANDY_SETTINGS
    # bin_erreur : le texte d'avant (serveurs et pages d'avant) ; bin_motif et bin_params : ce que la page traduit
    out = {"bin": None, "bin_erreur": None, "bin_motif": None, "bin_params": None, "present": False, "version": None, "modele": None, "langue": None,
           "config": None, "delai_s": actions.HANDY_TIMEOUT, "ffmpeg": which("ffmpeg")}
    try:
        b = actions.resolve_handy_bin(desktop)
        out["bin"] = b
        m = _VERSION_STORE.search(b)
        out["version"] = m.group(1) if m else None
        chemin = b if os.path.isabs(b) else which(b)  # Exec= relatif : le PATH, comme le lanceur
        out["present"] = bool(chemin) and os.path.isfile(chemin) and os.access(chemin, os.X_OK)
        if not out["present"]:
            out["bin_erreur"] = f"binaire introuvable : {Path(b).name}"
            out["bin_motif"], out["bin_params"] = "bin_introuvable", {"nom": Path(b).name}
    except (OSError, RuntimeError) as e:
        out["bin_erreur"] = f"{type(e).__name__} : {Path(desktop).name}"
        out["bin_motif"], out["bin_params"] = "bin_desktop", {"erreur": type(e).__name__, "nom": Path(desktop).name}
    try:
        s = json.loads(Path(settings).read_text()).get("settings", {})
        if isinstance(s, dict):
            mod, lang = s.get("selected_model"), s.get("selected_language")
            out["modele"] = mod if isinstance(mod, str) and mod else None
            out["langue"] = lang if isinstance(lang, str) and lang else None
            out["config"] = str(settings)
    except (OSError, ValueError, AttributeError):
        pass
    return out


def commit_servi(repo) -> str | None:
    """Commit du code chargé au démarrage (lu une fois : un pull après coup n'est pas « servi »)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", sha) else None


def cache_sw(static_dir) -> str | None:
    try:
        m = _CACHE_SW.search((Path(static_dir) / "sw.js").read_text())
    except OSError:
        return None
    return m.group(1) if m else None
