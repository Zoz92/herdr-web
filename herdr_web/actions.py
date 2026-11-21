"""Actions vers un agent : texte, photo, message vocal transcrit."""
import base64
import json
import logging
import os
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from herdr_web.choices import panneau_btw
from herdr_web.core import HcError

log = logging.getLogger(__name__)

MAX_PHOTO = 8 * 1024 * 1024
SEND_TIMEOUT = 90  # délai CLIENT : retenir un brouillon peut prendre jusqu'à 60 s
DESKTOP = Path.home() / ".config" / "autostart" / "Handy.desktop"


class AgentBlocked(Exception):
    """terminal.send refusé : l'agent est bloqué."""


class TooLarge(Exception):
    """Photo au-delà de 8 Mo."""


class Busy(Exception):
    """Un envoi vers cette adresse est déjà en cours."""


class EmptyTranscript(Exception):
    """La transcription est vide : rien n'est envoyé."""


class TranscriptionFailed(Exception):
    """ffmpeg ou Handy a échoué ou dépassé son délai ; le message porte la fin de leur stderr."""


class EchecCode(TranscriptionFailed):
    """Le programme a répondu par un code de sortie non nul (pas un délai dépassé)."""


FFMPEG_TIMEOUT = 30
HANDY_TIMEOUT = 90  # spec : au-delà de 90 s, la page affiche un échec
STDERR_MAX = 300


def _etape(run, nom, argv, delai, **kw):
    try:
        return run(argv, check=True, capture_output=True, timeout=delai, **kw)
    except subprocess.TimeoutExpired:
        raise TranscriptionFailed(f"{nom} : délai de {delai} s dépassé") from None
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise EchecCode(f"{nom} : rc={e.returncode} {err.strip()[-STDERR_MAX:]}".strip()) from None
    except OSError as e:
        raise TranscriptionFailed(f"{nom} : {type(e).__name__}") from None


_inflight: set[str] = set()
_lock = threading.Lock()


@contextmanager
def _en_vol(address: str):
    """Un envoi à la fois par terminal (texte ou touche) : le second lève Busy (409 occupe). Un
    terminal.send peut tenir jusqu'à 60 s quand un brouillon est retenu ; une touche glissée au
    milieu (Échap, Entrée) se mêlerait à sa séquence."""
    with _lock:
        if address in _inflight:
            raise Busy(address)
        _inflight.add(address)
    try:
        yield
    finally:
        with _lock:
            _inflight.discard(address)


def send_text(hc, address: str, text: str) -> dict:
    with _en_vol(address):
        try:
            return hc.request("terminal.send", {"terminal": address, "text": text},
                              timeout=SEND_TIMEOUT)
        except HcError as e:
            if e.code == -32000 and isinstance(e.data, dict) and e.data.get("kind") == "blocked":
                raise AgentBlocked(e.message) from e
            raise


# Touches nommées que la page peut envoyer (Ctrl+L, palette « Agent ouvert ») : liste BLANCHE, noms de
# `hc keys`. Jamais quand l'agent est `blocked` — Entrée ou ↓ répondraient au dialogue à la place de
# la personne ; le dialogue se répond par la carte de choix (choices.py), qui garde le msg vu.
TOUCHES = ("esc", "ctrl+c", "ctrl+l", "enter", "up", "down")


def send_key(hc, address: str, key) -> None:
    if not isinstance(key, str) or key not in TOUCHES:
        raise ValueError(f"touche hors liste : {key!r}")
    with _en_vol(address):
        status = hc.request("terminal.get", {"terminal": address}).get("status") or {}
        if status.get("state") == "blocked" and not (key == "esc" and _ecran_btw(hc, address)):
            raise AgentBlocked(address)
        hc.request("terminal.keys", {"terminal": address, "keys": [key]})


def _ecran_btw(hc, address: str) -> bool:
    """Panneau /btw : un « dialogue » que seul Échap ferme, sans réponse à donner à la place de personne."""
    return panneau_btw(hc.request("terminal.read", {"terminal": address, "lines": 0}).get("text") or "")


def put_photo(hc, address: str, name: str, data: bytes) -> str:
    """Dépose l'image chez l'agent sans rien envoyer ; rend la référence à citer dans un message.
    Plusieurs photos : un dépôt chacune, puis UN message qui les cite toutes (un seul tour)."""
    if len(data) > MAX_PHOTO:
        raise TooLarge(f"{len(data)} octets")
    res = hc.request("terminal.put", {
        "terminal": address, "name": name,
        "data": base64.b64encode(data).decode("ascii"),
    })
    return f"[image : {res['path']}]"


def send_photo(hc, address: str, name: str, data: bytes, caption: str = "") -> dict:
    ref = put_photo(hc, address, name, data)
    return send_text(hc, address, f"{caption} {ref}" if caption else ref)


def resolve_handy_bin(desktop: Path = DESKTOP) -> str:
    for line in Path(desktop).read_text().splitlines():
        if line.startswith("Exec="):
            return line[len("Exec="):].split()[0]
    raise RuntimeError(f"pas de ligne Exec= dans {desktop}")


def transcribe(audio: bytes, run=subprocess.run, handy_bin: str | None = None, modele: str | None = None) -> str:
    return transcrire(audio, run, handy_bin, modele)[0]


def transcrire(audio: bytes, run=subprocess.run, handy_bin: str | None = None,
               modele: str | None = None) -> tuple[str, str | None]:
    """(texte, avertissement). `modele` : identifiant passé à `--model` (réglages de herdr-web) ; None = le
    modèle choisi dans Handy. Si Handy refuse le modèle forcé (code non nul : modèle supprimé, id inconnu),
    UN second essai sans `--model`, et l'avertissement le dit. Un délai dépassé ne rejoue rien (90 s de plus)."""
    handy = handy_bin or resolve_handy_bin()
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _etape(run, "ffmpeg", ["ffmpeg", "-y", "-i", "pipe:", "-ar", "16000", "-ac", "1", wav],
               FFMPEG_TIMEOUT, input=audio)
        argv = [handy, "--transcribe-file", wav, "--json"]
        avert = None
        try:
            done = _etape(run, "Handy", argv + (["--model", modele] if modele else []), HANDY_TIMEOUT, text=True)
        except EchecCode as e:
            if not modele:
                raise
            log.warning("Handy refuse le modèle %r (%s) : second essai avec son modèle", modele, e)
            done = _etape(run, "Handy", argv, HANDY_TIMEOUT, text=True)
            avert = f"modèle « {modele} » refusé par Handy : transcrit avec le modèle choisi dans Handy"
        return json.loads(done.stdout.strip().splitlines()[-1]).get("text", ""), avert
    finally:
        try:
            os.unlink(wav)
        except FileNotFoundError:
            pass


def send_voice(hc, address: str, audio: bytes, run=subprocess.run, modele: str | None = None) -> dict:
    """{"text", "avertissement"} : l'avertissement (repli sans --model) remonte jusqu'à la page."""
    text, avert = transcrire(audio, run=run, modele=modele)
    text = text.strip()
    if not text:
        raise EmptyTranscript()
    send_text(hc, address, text)
    return {"text": text, "avertissement": avert}
