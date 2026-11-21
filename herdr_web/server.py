"""Serveur HTTPS : jeton, contrôles d'origine, routes JSON, flux SSE."""
import base64
import fcntl
import hmac
import json
import os
import logging
import mimetypes
import queue
import re
import select
import socket
import ssl
import struct
import subprocess
import termios
import threading
import time
from dataclasses import asdict
from email.parser import BytesParser
from email import policy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from herdr_web import actions, appairage as appairage_mod, choices as choices_mod, reglages as reglages_mod
from herdr_web import suggestion as suggestion_mod, textes
from herdr_web.agents import model_counts
from herdr_web.known_agents import KnownAgents
from herdr_web.commands import CommandIndex
from herdr_web.chat import (ChatReader, image_collee, image_root, question_en_attente, resolve_session,
                            resolve_subagent, strip_contract)
from herdr_web.core import HcError, HcGone, HcTimeout

log = logging.getLogger(__name__)

MAX_BODY = 12 * 1024 * 1024
LOGIN_MAX = 4096  # /login est lu avant toute authentification
MAX_CONNEXIONS = 64  # connexions servies en même temps ; au-delà, fermées tout de suite
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,}")
SSE_QUEUE = 256
DEFAULT_HOSTS = ["herdr.example.org:10370"]
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE = 8 * 1024 * 1024
IMAGE_ROOT = image_root()
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp"}
PING_EVERY = 15
# vue terminal en couleurs : `hc attach --observe` (jamais une touche, jamais un redimensionnement)
# dans un pty à la taille du terminal, ses octets relayés en SSE ; un processus par vue ouverte
ECRANS_MAX = 8
ECRAN_HISTORIQUE = 300  # lignes de défilement envoyées d'abord (écran principal seulement)
ECRAN_LOT = 64 * 1024
CHAT_POLL = 2
READ_FAILS = 3  # échecs consécutifs avant d'oublier un lecteur (ré-résolu au prochain accès)
_SUB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
PEEK_TIMEOUT = 10
SUGGESTION_TTL = 2.0  # une capture sert toutes les pages pendant 2 s (un hc attach par capture)
HTTP_METHOD = re.compile(rb"(GET|HEAD|POST|PUT|DELETE|OPTIONS|PATCH|TRACE|CONNECT) ")
SAFE_PATH = re.compile(r"/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,2047}")  # pas de //hôte
TIRETS = {"cpu": "—", "mem": "—", "quota": "—"}
SPACE_NAME_MAX = 64
HERDR_APP_TIMEOUT = 20
OPEN_MODELS = {None: None, "default": None, "opus": "opus", "sonnet": "sonnet"}  # liste blanche
# tâche -> (clé du nom d'onglet dans herdr_web/textes.py, message) : l'onglet suit la langue des réglages,
# le message, adressé à l'agent, reste tel quel
TACHES = {"installer-handy": ("tache.installer-handy", reglages_mod.TACHE_INSTALLER_HANDY)}
_ADDR = r"([^/]+)"
COOKIE_MAX_AGE = 31536000  # un an : durée du cookie hw posé par /login
APPAIRER = re.compile(r"/appairer/([^/]{0,256})")  # lien d'appairage : la seule route ouverte sans cookie
REGLAGES = Path.home() / ".config" / "herdr-web" / "reglages.json"  # remplacé par les tests
REPO = Path(__file__).resolve().parent.parent
_PAGES = {"/": "index.html", "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js"}
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/javascript", ".js")


class VoixDesactivee(Exception):
    """Handy est désactivé dans les réglages : aucun envoi vocal n'est transcrit."""


class HttpError(Exception):
    """`detail` : texte technique (français, ou sortie d'un programme) ; `motif` et `params` : une erreur
    de saisie que la page traduit (static/i18n, clés « motif.… »)."""

    def __init__(self, status, code, detail="", motif=None, **params):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.motif, self.params = motif, params


def _map_error(e: Exception) -> HttpError:
    if isinstance(e, HttpError):
        return e
    table = [
        (choices_mod.ChoiceStale, 409, "perime"), (actions.AgentBlocked, 409, "bloque"),
        (actions.Busy, 409, "occupe"), (actions.TooLarge, 413, "trop_gros"),
        (VoixDesactivee, 403, "voix_desactivee"),
        (actions.EmptyTranscript, 422, "vide"), (actions.TranscriptionFailed, 502, "transcription"),
        (ValueError, 400, "invalide"),
        (HcTimeout, 504, "delai"), (HcGone, 503, "indisponible"), (HcError, 502, "herdr"),
    ]
    for cls, status, code in table:  # HcTimeout avant HcGone (sous-classe)
        if isinstance(e, cls):
            return HttpError(status, code, str(e), getattr(e, "motif", None), **(getattr(e, "params", None) or {}))
    log.exception("erreur non prévue")
    return HttpError(500, "interne", "")


def parse_multipart(content_type: str, body: bytes) -> dict:
    """{nom: (nom_de_fichier|None, type, octets)} pour un corps multipart/form-data."""
    msg = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    out = {}
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[name] = (part.get_filename(), part.get_content_type(),
                         part.get_payload(decode=True) or b"")
    return out


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, hc, board, token, hosts, static_dir, push=None, ctx=None,
                 image_root=IMAGE_ROOT, statusbar=None, run=subprocess.run, home=None, known=None,
                 reglages=None):
        super().__init__(addr, Handler)
        self.ctx = ctx  # TLS posé par connexion, après avoir regardé le premier octet
        self.places = threading.BoundedSemaphore(MAX_CONNEXIONS)
        self.hc, self.board, self.push = hc, board, push
        self.statusbar, self.run = statusbar, run
        self.popen = subprocess.Popen  # remplacé par les tests
        self.ecrans = threading.BoundedSemaphore(ECRANS_MAX)
        self.ecran_ping = PING_EVERY
        self.sugg_lock = threading.Lock()  # une capture à la fois ; les autres lisent le cache
        self.sugg_cache: dict[str, tuple] = {}  # addr -> (clé d'état, instant, suggestion)
        self.home = Path(home) if home else Path.home()
        self.commands = CommandIndex(home=self.home)
        self.known = known if known is not None else KnownAgents(home=self.home)
        self.image_root = Path(image_root)
        # réglages partagés (téléphone et PC) : fichier sous ~/.config/herdr-web/, écrit en entier
        self.reglages = reglages if reglages is not None else reglages_mod.Reglages(REGLAGES)
        if push is not None:
            push.langue = self.reglages.langue  # titre des notifications dans la langue de la page
        self.commit = reglages_mod.commit_servi(REPO)  # le code chargé maintenant, pas un pull d'après
        self.appairage = appairage_mod.Appairage()  # codes à usage unique, en mémoire seulement
        self.token, self.hosts, self.static_dir = token, hosts, Path(static_dir)
        self.clients: set[queue.Queue] = set()
        self.clients_lock = threading.Lock()
        self.readers: dict[str, tuple] = {}  # addr -> (pid, ChatReader)
        self.readers_lock = threading.Lock()
        self.read_fails: dict[str, int] = {}  # addr -> échecs consécutifs (fil de suivi seul)
        self.suivis: dict[str, int] = {}  # addr -> pid : fils ouverts par une page, ré-résolus à chaque passe
        self.stopping = threading.Event()
        # rappels : seulement pousser dans les files (jamais board.refresh : verrou non réentrant)
        board.subscribe(lambda agents: self.broadcast("agents", [asdict(a) for a in agents]))
        board.subscribe(lambda agents: self.broadcast("barre", self.barre(agents)))
        if statusbar is not None:
            statusbar.subscribe(lambda vals: self.broadcast("barre", self.barre(vals=vals)))
        hc.on_state(lambda ok: self.broadcast("etat", {"connecte": bool(ok)}))
        self.watcher = threading.Thread(target=self._watch_chats, daemon=True)
        self.watcher.start()

    def broadcast(self, event, data):
        with self.clients_lock:
            for q in list(self.clients):
                try:
                    q.put_nowait((event, data))
                except queue.Full:
                    q.dropped = True  # client trop lent : retiré, sa connexion se ferme
                    self.clients.discard(q)

    def barre(self, agents=None, vals=None) -> dict:
        """Segments de la barre d'état + décompte des agents actifs par modèle."""
        if vals is None:
            vals = self.statusbar.snapshot() if self.statusbar is not None else dict(TIRETS)
        if agents is None:
            agents = self.board.snapshot()
        return {**vals, "models": model_counts(agents)}

    def spaces(self) -> list:
        sp = getattr(self.board, "spaces", None)
        return sp() if sp else []

    def agent(self, addr):
        for a in self.board.snapshot():
            if a.address == addr:
                return a
        raise HttpError(404, "introuvable", addr)

    def reader(self, a):
        """Lecteur partagé du fil d'un agent, en cache sur (pid, chemin résolu) : un /clear garde
        le pid mais change de session, donc de chemin. L'agent devient « suivi » : chaque passe
        le ré-résout, même si plus aucune page ne refait GET /chat (relecture finale, I3)."""
        path = resolve_session(a.pid) if a.pid else None
        with self.readers_lock:
            if a.pid:
                self.suivis[a.address] = a.pid
            cur = self.readers.get(a.address)
            if cur and cur[0] == (a.pid, path):
                return cur[1]
            if path is None:
                self.readers.pop(a.address, None)
                return None
            rd = ChatReader(path)
            rd.tail(1)  # l'offset partagé démarre à la fin : le flux ne rejoue pas l'historique
            self.readers[a.address] = ((a.pid, path), rd)
            return rd

    def _reresoudre(self):
        """Agents suivis : lecteur retiré après des échecs, ou session changée (même pid, autre
        chemin) ⇒ nouveau lecteur et un `chat` reset avec la fin du nouveau fil."""
        with self.readers_lock:
            suivis = dict(self.suivis)
        if not suivis:
            return
        try:
            agents = {a.address: a for a in self.board.snapshot()}
        except Exception:
            return
        for addr in suivis:
            a = agents.get(addr)
            if a is None or a.program not in ("claude-code", "claude") or not a.pid:
                with self.readers_lock:
                    self.suivis.pop(addr, None)
                    self.readers.pop(addr, None)
                continue
            path = resolve_session(a.pid)
            if path is None:
                continue
            with self.readers_lock:
                cur = self.readers.get(addr)
            if cur and cur[0] == (a.pid, path):
                continue
            rd = ChatReader(path)
            try:
                items = rd.tail()
            except OSError:
                continue
            with self.readers_lock:
                self.readers[addr] = ((a.pid, path), rd)
                self.suivis[addr] = a.pid
            self.read_fails.pop(addr, None)
            log.info("fil de %s ré-résolu : %s", addr, path.name)
            self.broadcast("chat", {"addr": addr, "items": [asdict(i) for i in items], "reset": True})

    def _watch_chats(self):
        # une exception imprévue (re-résolution, diffusion…) ne doit pas arrêter en silence le
        # seul fil qui alimente tous les fils en direct
        while not self.stopping.wait(CHAT_POLL):
            try:
                self._poll_chats()
            except Exception:
                log.exception("passe de suivi des fils")

    def _poll_chats(self):
        self._reresoudre()
        with self.readers_lock:
            items = list(self.readers.items())
        for addr, cur in items:
            rd = cur[1]
            try:
                with self.readers_lock:
                    new = rd.read_new()  # remplacé ou tronqué : déjà les derniers éléments (tail)
                    reset = rd.reset
            except Exception:
                n = self.read_fails.get(addr, 0) + 1
                log.warning("lecture du journal de %s impossible (%d/%d)", addr, n, READ_FAILS,
                            exc_info=True)
                if n < READ_FAILS:
                    self.read_fails[addr] = n
                    continue
                self.read_fails.pop(addr, None)
                with self.readers_lock:
                    if self.readers.get(addr) is cur:  # pas un lecteur remplacé entre-temps
                        del self.readers[addr]
                continue
            self.read_fails.pop(addr, None)
            if new or reset:
                payload = {"addr": addr, "items": [asdict(i) for i in new]}
                if reset:
                    payload["reset"] = True
                self.broadcast("chat", payload)

    def process_request(self, request, client_address):
        """Un fil par connexion, mais pas plus de MAX_CONNEXIONS : une rafale de connexions
        (ouvertes et laissées muettes, chacune tient un fil jusqu'à PEEK_TIMEOUT) ne crée pas de
        fils sans borne. Au-delà, la connexion est fermée sans être lue."""
        if not self.places.acquire(blocking=False):
            log.warning("trop de connexions simultanées (%d) : %s refusée", MAX_CONNEXIONS, client_address[0])
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.places.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.places.release()

    def finish_request(self, request, client_address):
        """Connexion TLS : poignée de main explicite (un échec ferme sans traceback). Requête HTTP
        en clair sur ce port : 301 vers le nom configuré, jamais vers le Host reçu."""
        if self.ctx is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(PEEK_TIMEOUT)
            head = request.recv(8, socket.MSG_PEEK)
        except OSError:
            return
        if not head:
            return
        if head[0] != 0x16:  # pas un ClientHello
            if HTTP_METHOD.match(head):
                self._redirect_plain(request)
            return
        tls = None
        try:
            tls = self.ctx.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            tls.do_handshake()
        except (ssl.SSLError, OSError) as e:
            log.info("poignée TLS refusée depuis %s : %s", client_address[0], type(e).__name__)
            if tls is not None:
                tls.close()
            return
        try:
            super().finish_request(tls, client_address)
        finally:
            self.shutdown_request(tls)

    def _redirect_plain(self, request):
        try:
            data = request.recv(4096)
        except OSError:
            return
        ligne = data.split(b"\r\n", 1)[0].decode("latin-1", "replace").split(" ")
        chemin = ligne[1] if len(ligne) >= 2 else "/"
        if not SAFE_PATH.fullmatch(chemin):
            chemin = "/"
        loc = f"https://{self.hosts[0]}{chemin}"
        try:
            request.sendall((f"HTTP/1.1 301 Moved Permanently\r\nLocation: {loc}\r\n"
                             "Content-Length: 0\r\nConnection: close\r\n\r\n").encode("latin-1"))
        except OSError:
            pass

    def handle_error(self, request, client_address):
        # une connexion coupée ou un client TLS fautif n'est pas une panne du serveur
        import sys
        e = sys.exc_info()[1]
        if isinstance(e, (OSError, ssl.SSLError)):
            log.info("connexion de %s close : %s", client_address[0], type(e).__name__)
            return
        log.exception("requête de %s", client_address[0])

    def shutdown(self):
        self.stopping.set()
        super().shutdown()


def _cookie(tok: str) -> tuple[str, str]:
    """Le cookie de session, le même pour /login et pour un lien d'appairage."""
    return ("Set-Cookie", f"hw={tok}; HttpOnly; Secure; SameSite=Strict; Max-Age={COOKIE_MAX_AGE}; Path=/")


def _page_appairage(langue: str, cle: str) -> bytes:
    """Petite page autonome (aucune ressource externe, aucun script) pour un lien refusé, dans la langue
    choisie dans les réglages (`cle` : « essais » ou « expire », herdr_web/textes.py)."""
    def e(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    langue = langue if langue in textes.LANGUES else textes.DEFAUT
    titre, texte = textes.t(langue, cle + ".titre"), textes.t(langue, cle + ".texte")
    return (f'<!doctype html><html lang="{langue}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>herdr — {e(titre)}</title>'
            '<style>body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#181825;color:#cdd6f4;'
            'display:flex;min-height:100vh;align-items:center;justify-content:center}'
            'main{max-width:28rem;padding:1.5rem}h1{font-size:1.3rem;margin:0 0 .5rem}a{color:#89b4fa}</style>'
            f'</head><body><main><h1>{e(titre)}</h1><p>{e(texte)}</p><p><a href="/">{e(textes.t(langue, "ouvrir"))}</a></p>'
            '</main></body></html>').encode()


class Handler(BaseHTTPRequestHandler):
    server: Server
    timeout = 30  # une connexion muette ne garde pas un fil (levé pour le flux SSE)

    def log_message(self, *a):  # ni en-têtes ni cookie dans un journal
        pass

    # -- réponses --
    def _send(self, status, body=b"", ctype="application/json", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200, headers=()):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), headers=headers)

    def _fail(self, e: HttpError):
        corps = {"erreur": e.code, "detail": e.detail}
        if e.motif:
            corps.update(motif=e.motif, params=e.params)
        self._json(corps, e.status)

    # -- contrôles --
    def _guard(self, method, api):
        host = self.headers.get("Host", "")
        if host not in self.server.hosts:
            raise HttpError(403, "hote", "hôte inconnu")
        if method != "GET" and self.headers.get("Origin") != f"https://{host}":
            raise HttpError(403, "origine", "origine refusée")
        if api:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            got = jar["hw"].value if "hw" in jar else ""
            if not hmac.compare_digest(got.encode(), self.server.token.encode()):
                raise HttpError(401, "jeton", "jeton absent ou faux")

    def _body(self, limite: int = MAX_BODY) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "invalide", "Content-Length")
        if n < 0:
            raise HttpError(400, "invalide", "Content-Length")
        if n > limite:
            raise HttpError(413, "trop_gros", f"corps > {limite // 1024} Ko")
        return self.rfile.read(n)

    def _json_body(self, limite: int = MAX_BODY) -> dict:
        try:
            d = json.loads(self._body(limite) or b"{}")
        except ValueError:
            raise HttpError(400, "invalide", "JSON illisible")
        if not isinstance(d, dict):
            raise HttpError(400, "invalide", "objet JSON attendu")
        return d

    def _handle(self, method):
        try:
            url = urlsplit(self.path)
            path = url.path
            if path.startswith("/appairer/"):
                self._guard(method, False)  # Host connu ; pas de cookie : c'est lui qu'on vient chercher
                return self._appairer(path)
            api = path.startswith("/api/") or path == "/login"
            if api:
                self._guard(method, path != "/login")
                if path == "/login":
                    return self._login()
                return self._api(method, path, parse_qs(url.query))
            if method != "GET":
                raise HttpError(405, "methode", "")
            return self._static(path)
        except Exception as e:
            try:
                self._fail(_map_error(e))
            except OSError:
                pass

    do_GET = lambda self: self._handle("GET")
    do_POST = lambda self: self._handle("POST")

    # -- routes --
    def _login(self):
        if self.command != "POST":
            raise HttpError(405, "methode", "")
        tok = str(self._json_body(LOGIN_MAX).get("token", ""))  # avant authentification : 4 Ko
        if not hmac.compare_digest(tok.encode(), self.server.token.encode()):
            raise HttpError(401, "jeton", "jeton faux")
        self._json({"ok": True}, headers=[_cookie(tok)])

    def _appairage_neuf(self):
        """Code d'appairage pour un nouvel appareil (route derrière le cookie et l'Origin). Le lien
        porte l'hôte de cette page, que _guard a déjà contrôlé ; jamais le jeton."""
        code, duree = self.server.appairage.creer()
        self._json({"lien": f"https://{self.headers.get('Host', '')}/appairer/{code}", "expire_s": duree})

    def _appairer(self, path):
        """Lien ouvert par le nouvel appareil : code bon ⇒ cookie hw (mêmes attributs que /login)
        puis retour à la page ; périmé, déjà servi ou faux ⇒ page « lien expiré ». Ni le code ni
        l'adresse ne vont dans un journal."""
        if self.command != "GET":
            raise HttpError(405, "methode", "")
        html, entetes = "text/html; charset=utf-8", [("Referrer-Policy", "no-referrer")]
        langue = self.server.reglages.langue()
        if not self.server.appairage.essai_permis(self.client_address[0]):
            return self._send(429, _page_appairage(langue, "essais"), html, entetes)
        m = APPAIRER.fullmatch(path)
        if not m or not self.server.appairage.consommer(unquote(m.group(1))):
            return self._send(410, _page_appairage(langue, "expire"), html, entetes)
        self._send(303, b"", "text/plain", entetes + [_cookie(self.server.token), ("Location", "/")])

    def _logout(self):
        """Le cookie est HttpOnly : seule une réponse du serveur peut l'effacer (Max-Age=0)."""
        self._json({"ok": True}, headers=[(
            "Set-Cookie", "hw=; HttpOnly; Secure; SameSite=Strict; Max-Age=0; Path=/")])

    def _infos_reglages(self):
        s = self.server
        push = s.push
        lu = s.reglages.lire()
        return {"handy": {**reglages_mod.handy_info(), "active": lu["handy"], "depot": reglages_mod.HANDY_DEPOT,
                          "dossier_perso": str(s.home)},
                "voix": lu["voix"], "nom_affiche": lu["nom_affiche"], "langue": lu["langue"],
                "version": {"commit": s.commit, "cache": reglages_mod.cache_sw(s.static_dir)},
                "session": {"cookie_max_age_s": COOKIE_MAX_AGE, "appareils_push": push.appareils() if push else None,
                            "modele": "un jeton partagé ; aucune liste de sessions côté serveur"}}

    def _maj_reglages(self):
        d = self._json_body()
        if not d or set(d) - {"handy", "nom_affiche", "voix", "langue"}:
            raise HttpError(400, "invalide", "handy (booléen), nom_affiche (texte), voix ou langue, rien d'autre")
        if "langue" in d and d["langue"] not in textes.LANGUES:
            raise HttpError(400, "invalide", "langue : " + " ou ".join(textes.LANGUES))
        if "voix" in d:  # modifiés depuis la page : Handy, lui, n'est jamais touché
            try:
                d["voix"] = {**reglages_mod.verifier_voix(d["voix"]), "source": "page"}
            except ValueError as e:
                raise HttpError(400, "invalide", str(e), getattr(e, "motif", None), **(getattr(e, "params", None) or {}))
        if "handy" in d and not isinstance(d["handy"], bool):
            raise HttpError(400, "invalide", "handy : booléen attendu")
        if "nom_affiche" in d and (not isinstance(d["nom_affiche"], str) or len(d["nom_affiche"]) > reglages_mod.NOM_MAX):
            raise HttpError(400, "invalide", f"nom_affiche : texte de {reglages_mod.NOM_MAX} caractères au plus",
                            "nom_affiche", max=reglages_mod.NOM_MAX)
        vals = self.server.reglages.ecrire(**d)
        out = {"handy": vals["handy"], "nom_affiche": vals["nom_affiche"], "voix": vals["voix"], "langue": vals["langue"]}
        self.server.broadcast("reglages", out)  # les autres appareils suivent
        self._json({"ok": True, **out})

    def _importer_handy(self):
        """Handy => herdr-web : modèle, langue et vocabulaire copiés du store de Handy dans reglages.json
        (écriture atomique). Rien d'autre n'est lu du store ; aucune clé d'API n'est copiée."""
        self._json_body()
        try:
            voix = reglages_mod.handy_import()
        except (OSError, ValueError, AttributeError):
            raise HttpError(409, "handy_config", "configuration de Handy absente ou illisible")
        voix = self.server.reglages.ecrire(voix=voix)["voix"]
        self.server.broadcast("reglages", {"voix": voix})  # les autres appareils suivent
        self._json({"ok": True, "voix": voix})

    def _static(self, path):
        base = self.server.static_dir.resolve()
        if path == "/":  # la page porte la langue choisie : i18n.js la lit dans <html lang>
            html = (base / _PAGES[path]).read_bytes()
            lang = f'<html lang="{self.server.reglages.langue()}">'.encode()
            return self._send(200, html.replace(b'<html lang="en">', lang, 1), "text/html; charset=utf-8")
        if path in _PAGES:
            f = base / _PAGES[path]
        elif path.startswith("/static/"):
            f = (base / unquote(path[len("/static/"):])).resolve()
            if not f.is_relative_to(base):
                raise HttpError(404, "introuvable", "")
        else:
            raise HttpError(404, "introuvable", "")
        if not f.is_file():
            raise HttpError(404, "introuvable", "")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype)

    def _api(self, method, path, qs):
        s = self.server
        if (method, path) == ("GET", "/api/agents"):
            return self._json([asdict(a) for a in s.board.snapshot()])
        if (method, path) == ("GET", "/api/stream"):
            return self._stream()
        if (method, path) == ("GET", "/api/image"):
            return self._image(qs.get("p", [""])[0])
        m = re.fullmatch(r"/api/collee/([0-9a-f]{64})", path)
        if m and method == "GET":
            return self._collee(m.group(1))
        if path in ("/api/push/key", "/api/push/subscribe"):
            return self._push(method, path)
        if (method, path) == ("GET", "/api/reglages"):
            return self._json(self._infos_reglages())
        if (method, path) == ("POST", "/api/reglages"):
            return self._maj_reglages()
        if (method, path) == ("POST", "/api/reglages/voix/import"):
            return self._importer_handy()
        if (method, path) == ("POST", "/api/logout"):
            return self._logout()
        if (method, path) == ("POST", "/api/appairage"):
            return self._appairage_neuf()
        if (method, path) == ("GET", "/api/statusbar"):
            return self._json(s.barre())
        if (method, path) == ("GET", "/api/spaces"):
            return self._json(s.spaces())
        if (method, path) == ("GET", "/api/known-agents"):
            return self._json(s.known.public())
        if (method, path) == ("POST", "/api/spaces"):
            return self._space_new()
        m = re.fullmatch(r"/api/spaces/([A-Za-z0-9_-]{1,80})/open", path)
        if m and method == "POST":
            return self._space_open(m.group(1))
        m = re.fullmatch(r"/api/tabs/([A-Za-z0-9_-]{1,80})/rename", path)
        if m and method == "POST":
            return self._tab_rename(m.group(1))
        m = re.fullmatch(rf"/api/agents/{_ADDR}/(\w+)", path)
        if not m:
            raise HttpError(404, "introuvable", "")
        addr, what = unquote(m.group(1)), m.group(2)
        routes = {("GET", "chat"): lambda: self._chat(addr, qs),
                  ("GET", "ecran"): lambda: self._ecran(addr),
                  ("GET", "subagent"): lambda: self._subagent(addr, qs),
                  ("GET", "commands"): lambda: self._commands(addr),
                  ("GET", "choices"): lambda: self._choices(addr),
                  ("GET", "suggestion"): lambda: self._suggestion(addr),
                  ("POST", "send"): lambda: self._send_text(addr),
                  ("POST", "answer"): lambda: self._answer(addr),
                  ("POST", "keys"): lambda: self._keys(addr),
                  ("POST", "photo"): lambda: self._upload(addr, "image/", True),
                  ("POST", "put"): lambda: self._upload(addr, "image/", True, depot=True),
                  ("POST", "voice"): lambda: self._upload(addr, "audio/", False),
                  ("POST", "close"): lambda: self._close(addr),
                  ("POST", "pin"): lambda: self._pin(addr),
                  ("POST", "seen"): lambda: self._seen(addr)}
        fn = routes.get((method, what))
        if not fn:
            raise HttpError(404, "introuvable", "")
        s.agent(addr)  # 404 si adresse inconnue, avant de lire le corps
        if what == "voice" and not s.reglages.voix_active():
            raise VoixDesactivee("voix désactivée dans les réglages")
        return fn()

    def _close(self, addr):
        """Point 33 : fermer un terminal. Geste destructeur, donc l'adresse est répétée dans le corps."""
        d = self._json_body()
        if not isinstance(d.get("confirm"), str) or d.get("confirm") != addr:
            raise HttpError(400, "confirmation", "confirm doit répéter l'adresse du terminal")
        for verbe in ("kill", "rm"):  # liste d'arguments, jamais un shell
            try:
                r = self.server.run(["hc", verbe, addr], capture_output=True, text=True,
                                    timeout=HERDR_APP_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as e:
                raise HttpError(502, "hc " + verbe, type(e).__name__)
            if r.returncode != 0:
                raise HttpError(502, "hc " + verbe, ((r.stderr or "") + (r.stdout or "")).strip()[:300])
        self._json({"ok": True})

    def _pin(self, addr):
        """Épingler (section Pinned) : `terminal.pin` de herdr-app, qui pose le label `herdr.pinned`
        du terminal dans le core — chaque client de herdr le voit, la page comme la fenêtre."""
        d = self._json_body()
        if not isinstance(d.get("pinned"), bool):
            raise HttpError(400, "invalide", "pinned : booléen attendu")
        self._herdr_app(["terminal.pin", "--terminal", addr, "--pinned", "true" if d["pinned"] else "false"])
        self._relire_layout()  # le label arrive au tableau sans attendre un événement hc
        self._json({"ok": True})

    def _seen(self, addr):
        """Vu depuis la page : le même label que herdr-app pose quand on regarde un terminal,
        `herdr.seen` = le tour vu. Un `done` dont le tour est vu se lit `idle`, des deux côtés."""
        a = self.server.agent(addr)
        if a.seen is None or a.seen < a.turn:
            self.server.hc.request("terminal.label", {"terminal": addr, "labels": {"herdr.seen": str(a.turn)}})
            self._relire_layout()  # le label arrive au tableau sans attendre un événement hc
        self._json({"ok": True, "turn": a.turn})

    def _herdr_app(self, args):
        """`herdr-app do …` en liste d'arguments, jamais par un shell."""
        try:
            r = self.server.run(["herdr-app", "do", *args], capture_output=True, text=True,
                                timeout=HERDR_APP_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise HttpError(502, "herdr-app", type(e).__name__)
        if r.returncode != 0:
            raise HttpError(502, "herdr-app", (r.stderr or "").strip()[:200])
        try:
            return json.loads(r.stdout or "null")
        except ValueError:
            return None

    def _space_new(self):
        d = self._json_body()
        name = str(d.get("name", "")).strip()
        if (not name or len(name) > SPACE_NAME_MAX or name.startswith("-")
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise HttpError(400, "invalide", "nom de space vide, trop long ou invalide", "nom_space")
        folder = str(d.get("folder", ""))
        home = self.server.home.resolve()
        f = Path(folder).resolve() if folder.startswith("/") else None
        if f is None or not f.is_relative_to(home) or not f.is_dir():
            raise HttpError(400, "invalide", f"dossier absent ou hors de {home}", "dossier_hors", home=str(home))
        res = self._herdr_app(["space.new", "--name", name, "--folder", str(f)])
        self._relire_layout()
        self._json({"ok": True, "result": res if isinstance(res, (dict, list)) else None})

    def _tab_rename(self, tid):
        d = self._json_body()
        name = d.get("name", "")
        if not isinstance(name, str):
            raise HttpError(400, "invalide", "nom : texte attendu")
        name = name.strip()
        # le vide est permis : l'onglet reprend son numéro (tab.rename sans nom de herdr-app)
        if (len(name) > SPACE_NAME_MAX or name.startswith("-")
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise HttpError(400, "invalide", "nom d'onglet trop long ou invalide", "nom_onglet")
        if not any(t.get("id") == tid for sp in self.server.spaces() for t in sp.get("tabs") or []):
            raise HttpError(404, "introuvable", "onglet inconnu", "onglet_inconnu")
        self._herdr_app(["tab.rename", "--id", tid, "--name", name])
        self._relire_layout()
        self._json({"ok": True})

    def _relire_layout(self):
        """un renommage ou un space neuf n'arrive au tableau qu'au prochain événement hc : sans
        relecture, /api/spaces rendait l'ancien layout pendant quelques secondes."""
        refresh = getattr(self.server.board, "refresh", None)
        if refresh is None:
            return
        try:
            refresh()
        except Exception:
            log.exception("relecture du layout après herdr-app do")

    def _space_open(self, sid):
        d = self._json_body()
        kind, model, tache = d.get("kind"), d.get("model"), d.get("tache")
        if not isinstance(kind, str) or not (model is None or isinstance(model, str)):
            raise HttpError(400, "invalide", "kind ou model : texte attendu")  # une liste n'est pas hachable
        # tâche : une clé de liste blanche, jamais un texte libre de la page ; Claude seulement,
        # dans le dossier personnel
        if tache is not None and (kind != "claude" or not isinstance(tache, str) or tache not in TACHES
                                  or "folder" in d):
            raise HttpError(400, "invalide", "tache hors liste")
        # liste blanche : « terminal » et les agents détectés installés (point 34), jamais un nom libre
        agents = self.server.known.installed() if kind != "terminal" else {}
        if (kind != "terminal" and kind not in agents) or model not in OPEN_MODELS or (
                kind != "claude" and model not in (None, "default")):
            raise HttpError(400, "invalide", "kind ou model hors liste")
        sp = next((x for x in self.server.spaces() if x.get("id") == sid), None)
        if sp is None:
            raise HttpError(404, "introuvable", "space inconnu", "space_inconnu")
        args = ["tab.new", "--space", sid]
        # dossier : celui de l'agent d'où part le clic (la page l'envoie), sinon celui du space —
        # un space dont le dossier est le dossier personnel : Claude y démarrait (2026-11-15)
        folder = str(self.server.home) if tache else d.get("folder")
        if folder is not None:
            home = self.server.home.resolve()
            f = Path(folder).resolve() if isinstance(folder, str) and folder.startswith("/") else None
            if f is None or not f.is_relative_to(home) or not f.is_dir():
                raise HttpError(400, "invalide", f"dossier absent ou hors de {home}", "dossier_hors", home=str(home))
            args += ["--folder", str(f)]
        elif sp.get("folder"):
            args += ["--folder", str(sp["folder"])]
        if kind == "terminal":
            args += ["--name", "terminal"]
        else:
            argv = list(agents[kind]["argv"])  # chemin absolu résolu : le core n'a pas le PATH du shell
            if kind == "claude" and OPEN_MODELS[model]:
                argv += ["--model", OPEN_MODELS[model]]
            nom = kind + (f" {OPEN_MODELS[model]}" if OPEN_MODELS[model] else "")
            if tache:
                cle_nom, texte_tache = TACHES[tache]
                nom = textes.t(self.server.reglages.langue(), cle_nom)
                argv.append(texte_tache)  # premier message de la session interactive
            args += ["--name", nom, "--argv", json.dumps(argv)]
        res = self._herdr_app(args)
        self._json({"ok": True, "result": res if isinstance(res, (dict, list)) else None})

    def _push(self, method, path):
        push = self.server.push
        if push is None:
            raise HttpError(404, "introuvable", "")
        if (method, path) == ("GET", "/api/push/key"):
            return self._json({"key": push.public_key()})
        if (method, path) != ("POST", "/api/push/subscribe"):
            raise HttpError(404, "introuvable", "")
        d = self._json_body()
        ep, keys = d.get("endpoint"), d.get("keys")
        if (not isinstance(ep, str) or not ep.startswith("https://") or len(ep) > 2048
                or not isinstance(keys, dict)
                or not all(isinstance(keys.get(k), str) and 0 < len(keys[k]) <= n
                           for k, n in (("p256dh", 256), ("auth", 64)))):
            raise HttpError(400, "invalide", "abonnement invalide")
        push.subscribe({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}})
        self._json({"ok": True})

    def _image(self, p):
        """Sert une image envoyée : seulement sous <racine>/**/files/, jamais ailleurs (404 muet)."""
        try:
            root = self.server.image_root.resolve()
            f = Path(p).resolve() if p.startswith("/") else None
            ok = (f is not None and f.is_relative_to(root) and "files" in f.relative_to(root).parts[:-1]
                  and f.suffix.lower() in IMAGE_EXT and f.is_file() and f.stat().st_size <= MAX_IMAGE)
            data = f.read_bytes() if ok else None
        except (OSError, ValueError, RuntimeError):
            data = None
        if data is None:
            raise HttpError(404, "introuvable", "")
        self._send(200, data, IMAGE_EXT[f.suffix.lower()], [("X-Content-Type-Options", "nosniff")])

    def _collee(self, cle):
        """Sert une image collée dans Claude Code, vue dans un journal relu (404 muet sinon)."""
        v = image_collee(cle)
        if v is None:
            raise HttpError(404, "introuvable", "")
        self._send(200, v[1], v[0], [("X-Content-Type-Options", "nosniff")])

    def _screen(self, addr, lines):
        return self.server.hc.request("terminal.read", {"terminal": addr, "lines": lines})["text"]

    def _chat(self, addr, qs):
        a = self.server.agent(addr)
        try:
            n = int(qs.get("n", ["200"])[0])
        except ValueError:
            raise HttpError(400, "invalide", "n")
        if a.program != "claude-code":
            return self._json({"mode": "brut", "screen": self._screen(addr, 200), "note": None})
        rd = self.server.reader(a)
        if rd is None:
            return self._json({"mode": "brut", "screen": self._screen(addr, 200),
                               # un Claude neuf n'écrit son journal qu'au premier message
                               "note": "pas encore de fil : écran du terminal jusqu'au premier message"})
        # lecteur jetable : tail() avancerait l'offset du lecteur partagé (flux SSE)
        # borné à l'offset du lecteur partagé : le reste arrivera par le flux, une seule fois
        with self.server.readers_lock:
            end = rd.offset
        items = ChatReader(rd.path).tail(max(1, min(n, 1000)), end=end)
        self._json({"mode": "chat", "items": [asdict(i) for i in items], "note": None})

    def _subagent(self, addr, qs):
        """Fil d'un sous-agent, en lecture seule. Sans `depuis` : les n derniers éléments et
        l'offset atteint ; avec `depuis` (offset rendu avant) : la suite seulement."""
        a = self.server.agent(addr)
        aid = qs.get("id", [""])[0]
        try:
            n = max(1, min(int(qs.get("n", ["300"])[0]), 1000))
            depuis = int(qs["depuis"][0]) if "depuis" in qs else None
        except ValueError:
            raise HttpError(400, "invalide", "n ou depuis")
        if not _SUB_ID.fullmatch(aid) or (depuis is not None and depuis < 0):
            raise HttpError(400, "invalide", "id ou depuis")
        path = resolve_subagent(a.pid, aid) if a.pid else None
        if path is None:
            raise HttpError(404, "introuvable", "fil du sous-agent introuvable")
        out = {"mode": "chat"}
        try:
            if depuis is not None and depuis <= path.stat().st_size:
                rd = ChatReader(path, offset=depuis)
                items = rd.read_new()
            else:
                if depuis is not None:
                    out["reset"] = True  # journal remplacé : la liste rendue remplace tout
                rd = ChatReader(path)
                items = rd.tail(n)
        except OSError:
            raise HttpError(404, "introuvable", "fil du sous-agent illisible")
        out.update(items=[asdict(i) for i in strip_contract(items)], offset=rd.offset)
        self._json(out)

    def _commands(self, addr):
        a = self.server.agent(addr)
        if a.program not in ("claude-code", "claude"):
            return self._json([])  # pas de commandes / pour un terminal
        self._json(self.server.commands.get(a.cwd))

    def _lire_choix(self, a, ecran):
        """Choix montrés à la page, et vérifiés par /answer : ceux de l'AskUserQuestion en attente
        dans le journal de la session (libellés, descriptions, aperçus exacts) quand l'écran montre
        bien cette question, sinon ceux de l'écran. Question à aperçu : l'écran mêle les options et
        la boîte de l'aperçu sur les mêmes lignes (capture de l'utilisateur, 2026-11-15)."""
        cs = choices_mod.parse_choices(ecran)
        if not cs or a.program not in ("claude-code", "claude") or not a.pid:
            return cs, None
        try:
            path = resolve_session(a.pid)
            q = choices_mod.question_affichee(question_en_attente(path), ecran) if path else None
            st = choices_mod.choix_structures(q, cs) if q else None
        except Exception:
            log.exception("question en attente illisible dans le journal de %s", a.address)
            q = st = None
        return (st, q) if st else (cs, None)

    def _choices(self, addr):
        a = self.server.agent(addr)
        ecran = self._screen(addr, 0)
        if choices_mod.panneau_btw(ecran):  # sa réponse numérotée n'est pas un choix : la page propose Échap
            return self._json({"msg": a.msg, "kind": a.kind, "choices": [], "contexte": "", "menu": None,
                               "panneau": True})
        cs, _ = self._lire_choix(a, ecran)
        menu = None if cs else choices_mod.parse_menu(ecran)
        self._json({"msg": a.msg, "kind": a.kind, "choices": [asdict(c) for c in cs],
                    "contexte": choices_mod.parse_contexte(ecran) if cs else "",
                    "menu": {"options": menu[0], "courant": menu[1]} if menu else None})

    def _suggestion(self, addr):
        """Suggestion grisée du prompt de Claude Code (herdr_web/suggestion.py), seulement pour un
        Claude au repos qui a déjà fait un tour : au tour 0 le prompt montre un placeholder."""
        s, a = self.server, self.server.agent(addr)
        if a.program not in ("claude-code", "claude") or a.state not in ("idle", "done") or a.turn < 1:
            return self._json({"suggestion": None, "turn": a.turn})
        cle = (a.pid, a.turn, a.state)
        with s.sugg_lock:
            vu = s.sugg_cache.get(addr)
            if vu and vu[0] == cle and time.monotonic() - vu[1] < SUGGESTION_TTL:
                return self._json({"suggestion": vu[2], "turn": a.turn})
            try:
                t = s.hc.request("terminal.get", {"terminal": addr})
            except HcError:
                raise HttpError(404, "introuvable", "terminal inconnu")
            t = t.get("terminal", t) if isinstance(t, dict) else {}
            cols = t.get("cols") if isinstance(t.get("cols"), int) and t.get("cols") > 0 else 120
            rows = t.get("rows") if isinstance(t.get("rows"), int) and t.get("rows") > 0 else 40
            try:
                texte = suggestion_mod.lire(suggestion_mod.capturer(s.popen, addr, cols, rows))
            except OSError:
                log.warning("capture de la suggestion de %s impossible", addr, exc_info=True)
                texte = None
            s.sugg_cache[addr] = (cle, time.monotonic(), texte)
        self._json({"suggestion": texte, "turn": a.turn})

    def _send_text(self, addr):
        text = str(self._json_body().get("text", ""))
        if not text.strip():
            raise HttpError(400, "invalide", "texte vide", "texte_vide")
        actions.send_text(self.server.hc, addr, text)
        self._json({"ok": True})

    def _keys(self, addr):
        """Une touche nommée (liste blanche actions.TOUCHES) ; 409 bloque si l'agent attend un choix."""
        actions.send_key(self.server.hc, addr, self._json_body().get("key"))
        self._json({"ok": True})

    def _answer(self, addr):
        d = self._json_body()
        if "option" in d:  # menu à curseur : l'écran doit porter le menu vu, sinon 409 perime
            vues, option = d.get("options"), d.get("option")
            if not isinstance(option, int) or not isinstance(vues, list) or not all(isinstance(x, str) for x in vues):
                raise HttpError(400, "invalide", "option ou options")
            choices_mod.answer_menu(self.server.hc, addr, option, vues, self._screen(addr, 0))
            return self._json({"ok": True})
        digit, msg = str(d.get("digit", "")), str(d.get("msg", ""))
        if len(digit) != 1 or not ("1" <= digit <= "9"):
            raise ValueError(f"touche de choix invalide : {digit!r}")
        ecran = self._screen(addr, 0)
        cs, question = self._lire_choix(self.server.agent(addr), ecran)
        # AskUserQuestion à plusieurs questions : msg et chiffres ne changent pas d'une question à
        # l'autre ; la page envoie ce qu'elle montre (contexte + libellés), et si l'écran a tourné
        # entre-temps (réponse donnée dans le terminal), 409 perime : la page réaffiche l'écran
        # réel. Sans signature (page d'avant la PR #22, encore en cache) : la garde d'avant seule.
        if "contexte" in d or "libelles" in d:
            ctx, libs = d.get("contexte"), d.get("libelles")
            if not isinstance(ctx, str) or not isinstance(libs, list) or not all(isinstance(x, str) for x in libs):
                raise HttpError(400, "invalide", "contexte ou libelles")
            if ctx != choices_mod.parse_contexte(ecran) or libs != [c.label for c in cs]:
                raise choices_mod.ChoiceStale(addr)
        choisi = next((c for c in cs if c.digit == digit), None)
        if choisi is None:
            raise HttpError(400, "invalide", "ce choix n'est pas à l'écran", "choix_absent")
        if "texte" in d:  # « Type something. » : la réponse est le texte saisi sur la page
            texte = d.get("texte")
            if not isinstance(texte, str) or not texte.strip():
                raise HttpError(400, "invalide", "texte vide", "texte_vide")
            if not choices_mod.est_saisie_libre(choisi):
                raise HttpError(400, "invalide", "ce choix n'attend pas de texte", "choix_sans_texte")
            choices_mod.answer_texte(self.server.hc, addr, digit, msg, texte,
                                     case=choisi.label == choices_mod.SAISIE_LIBRE_CASE,
                                     contexte=choices_mod.parse_contexte(ecran))
            return self._json({"ok": True})
        # le chiffre seul de « Type something. » ne répond rien (le champ prend le curseur) : la page
        # croirait avoir répondu et l'agent resterait bloqué
        if choices_mod.est_saisie_libre(choisi):
            raise HttpError(400, "texte_attendu", "« Type something. » attend un texte")
        # dialogue à aperçu (le chiffre n'y fait que poser le curseur, Entrée répond) : d'après le
        # journal quand la question y est (une option porte `preview`, pas de cases), sinon d'après la
        # ligne d'aide du dialogue lui-même, jamais d'après le transcript (réserve P1)
        if question is not None:
            entree = not question.get("multiSelect") and any(o.get("preview") for o in question["options"])
        else:
            entree = choices_mod.est_apercu(ecran)
        choices_mod.answer(self.server.hc, addr, digit, msg, entree=entree)
        self._json({"ok": True})

    def _upload(self, addr, prefix, photo, depot=False):
        ct = self.headers.get("Content-Type", "")
        if not ct.startswith("multipart/form-data"):
            raise HttpError(400, "invalide", "multipart attendu")
        parts = parse_multipart(ct, self._body())
        if "file" not in parts:
            raise HttpError(400, "invalide", "champ file manquant")
        fname, ptype, data = parts["file"]
        if not ptype.startswith(prefix):
            raise HttpError(415, "type", f"{prefix}* attendu")
        hc = self.server.hc
        if photo:
            name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(fname or "").name)
            if name.strip(".") == "":
                name = "photo" + (mimetypes.guess_extension(ptype) or "")
            if depot:  # plusieurs photos : la page cite les références dans un seul envoi
                return self._json({"ok": True, "ref": actions.put_photo(hc, addr, name, data)})
            caption = parts.get("caption", (None, "", b""))[2].decode("utf-8", "replace")
            actions.send_photo(hc, addr, name, data, caption)
            return self._json({"ok": True})
        modele = self.server.reglages.lire()["voix"]["modele"]  # None : celui choisi dans Handy
        r = actions.send_voice(hc, addr, data, modele=modele)
        # repli sans --model : le texte (français) pour une page d'avant, le motif pour la page qui traduit
        avert = {"avertissement": r["avertissement"], "motif": "modele_refuse", "params": {"modele": modele}} \
            if r.get("avertissement") else {}
        self._json({"ok": True, "text": r["text"], **avert})

    def _ecran(self, addr):
        """Flux SSE des octets bruts du terminal (`hc attach --observe`) pour un émulateur dans la
        page : couleurs, invite, plein écran, comme herdr-app. Le processus meurt avec la page."""
        s = self.server
        try:
            t = s.hc.request("terminal.get", {"terminal": addr})
        except HcError:
            raise HttpError(404, "introuvable", "terminal inconnu")
        t = t.get("terminal", t) if isinstance(t, dict) else {}
        cols = t.get("cols") if isinstance(t.get("cols"), int) and t.get("cols") > 0 else 120
        rows = t.get("rows") if isinstance(t.get("rows"), int) and t.get("rows") > 0 else 40
        if not s.ecrans.acquire(blocking=False):
            raise HttpError(429, "occupe", f"au plus {ECRANS_MAX} vues terminal à la fois")
        proc, maitre = None, None
        try:
            maitre, esclave = os.openpty()
            fcntl.ioctl(esclave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            try:
                proc = s.popen(["hc", "attach", "--observe", "--history", str(ECRAN_HISTORIQUE), addr],
                               stdin=esclave, stdout=esclave, stderr=subprocess.DEVNULL,
                               start_new_session=True, close_fds=True)
            finally:
                os.close(esclave)
            self.connection.settimeout(2 * s.ecran_ping)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"event: taille\ndata: {json.dumps({'cols': cols, 'rows': rows})}\n\n".encode())
            self.wfile.flush()
            while not s.stopping.is_set():
                prets, _, _ = select.select([maitre], [], [], s.ecran_ping)
                if not prets:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                try:
                    bloc = os.read(maitre, ECRAN_LOT)
                except OSError:  # EIO : hc attach est sorti (terminal fermé)
                    bloc = b""
                if not bloc:
                    self.wfile.write(b"event: fin\ndata: {}\n\n")
                    self.wfile.flush()
                    break
                self.wfile.write(b"data: " + base64.b64encode(bloc) + b"\n\n")
                self.wfile.flush()
        except OSError:
            pass  # la page s'est déconnectée
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            if maitre is not None:
                os.close(maitre)
            s.ecrans.release()
            self.close_connection = True

    def _stream(self):
        s = self.server
        q: queue.Queue = queue.Queue(maxsize=SSE_QUEUE)
        q.dropped = False
        with s.clients_lock:  # inscrit AVANT la photo initiale : aucun événement perdu
            s.clients.add(q)
        q.put_nowait(("agents", [asdict(a) for a in s.board.snapshot()]))
        q.put_nowait(("etat", {"connecte": bool(s.hc.connected)}))
        try:
            self.connection.settimeout(2 * PING_EVERY)  # une écriture bloquée lève OSError
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while not s.stopping.is_set() and not q.dropped:
                try:
                    ev, data = q.get(timeout=PING_EVERY)
                except queue.Empty:
                    ev, data = "ping", {}
                self.wfile.write(
                    f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with s.clients_lock:
                s.clients.discard(q)
            self.close_connection = True


def make_server(hc, board, token, bind=("127.0.0.1", 10370), hosts=None, static_dir=STATIC_DIR,
                cert=None, key=None, push=None, image_root=IMAGE_ROOT, statusbar=None,
                run=subprocess.run, home=None, known=None, reglages=None) -> Server:
    if not token or not TOKEN_RE.fullmatch(token):
        raise ValueError("jeton absent, trop court (< 32) ou hors [A-Za-z0-9_-]")
    ctx = None
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(cert, key)
        except (OSError, ssl.SSLError):  # jamais le détail : il peut citer un chemin ou un contenu
            raise ValueError("certificat ou clé TLS illisible") from None
    return Server(bind, hc, board, token, hosts or DEFAULT_HOSTS, static_dir, push, ctx, image_root,
                  statusbar, run, home, known, reglages)
