"""Connecter un appareil sans aller chercher le jeton au coffre : un appareil déjà connecté demande
un code d'appairage (POST /api/appairage, derrière le cookie), la page l'affiche en QR code et en
lien ; le nouvel appareil ouvre le lien (GET /appairer/<code>), qui pose le cookie `hw`.

Le code est aléatoire (`secrets`), à usage unique, valable DUREE secondes, gardé en mémoire
seulement : jamais écrit sur disque ni dans un journal. Le jeton ne voyage jamais dans le lien.
La consommation est la seule route joignable sans cookie : essais bornés par adresse, et chaque
code en attente est comparé en temps constant (hmac.compare_digest), sans sortie anticipée."""
import hmac
import re
import secrets
import threading
import time
from collections import deque

DUREE = 300  # secondes : le temps de scanner le QR code
MAX_CODES = 8  # codes en attente à la fois ; au-delà, le plus ancien est oublié
ESSAIS = 10  # essais de consommation par adresse et par FENETRE
FENETRE = 60
MAX_ADRESSES = 1024  # adresses suivies pour la limite d'essais (mémoire bornée)
CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class Appairage:
    def __init__(self, horloge=time.monotonic):
        self.horloge = horloge
        self._lock = threading.Lock()
        self._codes: list[tuple[str, float]] = []  # (code, échéance)
        self._essais: dict[str, deque] = {}  # adresse -> instants des essais récents

    def creer(self) -> tuple[str, int]:
        """Un code neuf (32 caractères URL-sûrs, 192 bits) et sa durée de vie en secondes."""
        code = secrets.token_urlsafe(24)
        with self._lock:
            now = self.horloge()
            self._codes = [(c, e) for c, e in self._codes if e > now][-(MAX_CODES - 1):]
            self._codes.append((code, now + DUREE))
        return code, DUREE

    def essai_permis(self, adresse: str) -> bool:
        """Compte un essai de `adresse` ; False au-delà de ESSAIS dans la FENETRE glissante."""
        with self._lock:
            now = self.horloge()
            q = self._essais.get(adresse)
            if q is None:
                if len(self._essais) >= MAX_ADRESSES:
                    self._purger(now)
                    if len(self._essais) >= MAX_ADRESSES:  # toutes actives : la plus ancienne sort
                        self._essais.pop(next(iter(self._essais)))
                q = self._essais[adresse] = deque()
            while q and q[0] <= now - FENETRE:
                q.popleft()
            if len(q) >= ESSAIS:
                return False
            q.append(now)
            return True

    def _purger(self, now):
        for a in [a for a, q in self._essais.items() if not q or q[-1] <= now - FENETRE]:
            del self._essais[a]

    def consommer(self, code: str) -> bool:
        """True une seule fois pour un code émis et pas encore expiré ; le code est alors retiré."""
        brut = code.encode() if isinstance(code, str) and CODE_RE.fullmatch(code) else b""
        with self._lock:
            now = self.horloge()
            self._codes = [(c, e) for c, e in self._codes if e > now]
            trouve = -1
            for i, (c, _) in enumerate(self._codes):  # tous comparés : pas de sortie anticipée
                if hmac.compare_digest(c.encode(), brut) and brut:
                    trouve = i
            if trouve < 0:
                return False
            del self._codes[trouve]
            return True

    def en_attente(self) -> int:
        with self._lock:
            now = self.horloge()
            return sum(1 for _, e in self._codes if e > now)
