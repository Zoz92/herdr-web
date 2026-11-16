"""Suggestion grisée de Claude Code : la suite qu'il propose dans son prompt à la fin d'un tour,
acceptée par Tab ou →.

Le texte brut (`terminal.read`) ne la distingue pas d'un brouillon tapé. Le premier dessin de
`hc attach --observe` porte les attributs. Mesuré le 2026-11-15 sur un vrai Claude Code (t4w, t4x) :

- suggestion  : `❯\\u00a0` + `ESC[2m` (dim) + texte, curseur posé en colonne 3 (début du texte) ;
- brouillon   : `❯\\u00a0` + texte sans attribut, curseur en fin de texte ;
- Claude neuf : placeholder `Try "…"` encodé comme une suggestion, au tour 0.

D'où les gardes : tout le texte du prompt est dim, rien sur la ligne suivante, curseur en
colonne 3, motif `Try "…"` refusé (le tour 0 est écarté par le serveur). Au moindre doute :
pas de suggestion, jamais un brouillon pris pour une suggestion.
"""
import fcntl
import os
import re
import select
import struct
import subprocess
import termios
import time

INVITE = "❯ ".encode()
FIN_DESSIN = b"\x1b]7501;"  # fait d'état posé par hc à la fin du premier dessin
DELAI = 2.0  # secondes pour recevoir le premier dessin
_CSI = re.compile(rb"\x1b\[([0-9;:?<=>!]*)[ -/]*([@-~])")
_OSC = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC = re.compile(rb"\x1b[()][0-9A-Za-z]|\x1b[^\[\]()]")
_CUP = re.compile(rb"\x1b\[(\d+);(\d+)H")
PLACEHOLDER = re.compile(r'Try ".*"')


def _dim(params: bytes, dim: bool) -> bool:
    """État dim après un SGR (2 l'allume ; 0, vide et 22 l'éteignent ; 38/48 sautent leur couleur)."""
    p = [int(x) if x.isdigit() else 0 for x in params.replace(b":", b";").split(b";")] if params else [0]
    i = 0
    while i < len(p):
        if p[i] in (38, 48, 58):
            i += 5 if i + 1 < len(p) and p[i + 1] == 2 else 3
            continue
        if p[i] == 2:
            dim = True
        elif p[i] in (0, 22):
            dim = False
        i += 1
    return dim


def _cellules(ligne: bytes) -> list[tuple[str, bool]]:
    """(caractère, dim) de chaque caractère visible d'une ligne du dessin."""
    out, dim, i = [], False, 0
    while i < len(ligne):
        if ligne[i] == 0x1b:
            m = _CSI.match(ligne, i)
            if m:
                if m.group(2) == b"m":
                    dim = _dim(m.group(1), dim)
                i = m.end()
                continue
            m = _OSC.match(ligne, i) or _ESC.match(ligne, i)
            i = m.end() if m else i + 1
            continue
        j = i
        while j < len(ligne) and ligne[j] != 0x1b:
            j += 1
        out += [(c, dim) for c in ligne[i:j].decode("utf-8", "replace") if c >= " "]
        i = j
    return out


def lire(dessin: bytes) -> str | None:
    """Le texte de la suggestion grisée du premier dessin, ou None (brouillon, prompt vide,
    placeholder, dessin incomplet ou ambigu)."""
    if FIN_DESSIN not in dessin:
        return None
    dessin = dessin[:dessin.rfind(FIN_DESSIN)]
    i = dessin.rfind(INVITE)
    if i < 0:
        return None
    fin = dessin.find(b"\r\n", i)
    if fin < 0:
        return None
    cases = _cellules(dessin[i + len(INVITE):fin])
    texte = "".join(c for c, _ in cases).strip()
    if not texte or any(not d and not c.isspace() for c, d in cases):
        return None  # vide, ou au moins un caractère tapé (non dim) : brouillon
    suivante = dessin[fin + 2:dessin.find(b"\r\n", fin + 2)] if b"\r\n" in dessin[fin + 2:] else b""
    vus = "".join(c for c, _ in _cellules(suivante)).strip()
    if vus and set(vus) != {"─"}:
        return None  # texte sur plusieurs lignes : pas mesuré, on ne devine pas
    curseurs = _CUP.findall(dessin)
    if not curseurs or int(curseurs[-1][1]) != 3:
        return None  # curseur ailleurs qu'au début du texte : pas une suggestion
    if PLACEHOLDER.fullmatch(texte):
        return None
    return texte


def capturer(popen, addr: str, cols: int, rows: int, delai: float = DELAI) -> bytes:
    """Le premier dessin du terminal par `hc attach --observe` (jamais une touche, jamais un
    redimensionnement), dans un pty à sa taille. Rendu dès la fin du dessin, ou au délai."""
    maitre, esclave = os.openpty()
    proc = None
    try:
        fcntl.ioctl(esclave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        try:
            proc = popen(["hc", "attach", "--observe", addr], stdin=esclave, stdout=esclave,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        finally:
            os.close(esclave)
        recu, limite = b"", time.monotonic() + delai
        while True:
            reste = limite - time.monotonic()
            if reste <= 0:
                break
            prets, _, _ = select.select([maitre], [], [], reste)
            if not prets:
                break
            try:
                bloc = os.read(maitre, 65536)
            except OSError:  # EIO : hc attach est sorti
                break
            if not bloc:
                break
            recu += bloc
            k = recu.find(FIN_DESSIN)
            if k >= 0 and b"\x1b\\" in recu[k:]:
                break
        return recu
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        os.close(maitre)
