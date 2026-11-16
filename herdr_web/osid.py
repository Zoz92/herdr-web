"""Système d'exploitation d'une machine, résolu en une clé de logo — table UNIQUE, copiée à l'identique
dans un mod de herdr-app.

Règle : `ID` de /etc/os-release ; inconnu → chaque valeur d'`ID_LIKE` dans l'ordre → « linux » (Tux).
macOS (platform.system() == "Darwin") → « macos », Windows natif → « windows ». Rien de lisible → "" (ordinateur
générique). WSL (/proc/version contient « microsoft » ou WSL_DISTRO_NAME) : la clé reste celle de la distribution,
`os_wsl` vaut True et l'affichage ajoute le petit badge Windows.
Easter egg : un nom de machine contenant « quantum », « quantique », « qubit » ou « qpu » prend la clé « quantum »
(ordinateur quantique), quel que soit l'OS.
Icône : static/os/<clé>.svg ; « glfos » → static/os-logo.png ; "" → static/os/generic.svg.
"""
import os
import platform
from pathlib import Path

IDS = {
    "ubuntu": "ubuntu", "debian": "debian", "fedora": "fedora", "arch": "arch", "archarm": "arch",
    "linuxmint": "mint", "pop": "pop", "manjaro": "manjaro", "manjaro-arm": "manjaro",
    "opensuse": "opensuse", "opensuse-tumbleweed": "opensuse", "opensuse-leap": "opensuse",
    "opensuse-microos": "opensuse", "nixos": "nixos", "glfos": "glfos", "endeavouros": "endeavouros",
    "cachyos": "cachyos", "bazzite": "bazzite", "steamos": "steamos", "gentoo": "gentoo", "alpine": "alpine",
    "void": "void", "kali": "kali", "elementary": "elementary", "zorin": "zorin", "rhel": "rhel",
    "rocky": "rocky", "almalinux": "almalinux", "centos": "centos", "raspbian": "raspios",
    "windows": "windows", "macos": "macos",
}
# clés qu'on peut rendre : les valeurs de IDS + les replis
QUANTIQUE = ("quantum", "quantique", "qubit", "qpu")
CLES = sorted(set(IDS.values()) | {"linux", "quantum", ""})


def fichier(cle: str) -> str:
    """Chemin de l'icône sous static/ pour une clé (celle d'`resoudre`)."""
    if cle == "glfos":
        return "os-logo.png"
    return f"os/{cle or 'generic'}.svg"


def lire(texte: str) -> dict:
    """os-release → {CLÉ: valeur} (guillemets retirés, minuscules)."""
    out = {}
    for ligne in texte.splitlines():
        k, sep, v = ligne.partition("=")
        if sep and k.strip():
            out[k.strip().upper()] = v.strip().strip('"\'').lower()
    return out


def resoudre(release: dict, systeme: str = "Linux") -> str:
    if systeme == "Darwin":
        return "macos"
    if systeme == "Windows":
        return "windows"
    if systeme != "Linux":
        return ""
    ident = release.get("ID", "")
    if ident in IDS:
        return IDS[ident]
    for like in release.get("ID_LIKE", "").split():
        if like in IDS:
            return IDS[like]
    return "linux" if ident or release.get("ID_LIKE") else ""


def est_wsl(proc_version: Path = Path("/proc/version"), env=os.environ) -> bool:
    if env.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in proc_version.read_text().lower()
    except OSError:
        return False


def cette_machine(chemin: Path = Path("/etc/os-release"), systeme=platform.system,
                  hote=platform.node) -> tuple[str, bool]:
    """(clé, wsl) de la machine locale."""
    if any(m in hote().lower() for m in QUANTIQUE):
        return "quantum", False
    sys_ = systeme()
    try:
        rel = lire(chemin.read_text(errors="replace"))
    except OSError:
        rel = {}
    cle = resoudre(rel, sys_)
    return cle, bool(cle) and sys_ == "Linux" and est_wsl()
