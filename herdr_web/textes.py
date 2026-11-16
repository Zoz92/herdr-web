"""Les rares textes que le serveur affiche lui-même, hors de la page : la page « lien expiré » (aucun script,
elle ne peut pas traduire), le titre d'une notification push et le nom d'onglet d'une tâche (« installer Handy »). Tout le reste est traduit par la page
(static/i18n/) : le serveur lui rend des codes. Mêmes clés dans chaque langue (tests/test_i18n.py)."""

LANGUES = ("en", "fr")
DEFAUT = "en"

TEXTES = {
    "fr": {
        "ouvrir": "Ouvrir herdr",
        "essais.titre": "Trop d'essais",
        "essais.texte": "Trop de liens essayés depuis cet appareil. Réessaie dans une minute.",
        "expire.titre": "Lien expiré",
        "expire.texte": ("Ce lien a déjà servi, a expiré (5 minutes) ou n'existe pas. Sur un appareil déjà "
                         "connecté : Réglages › Connecter un appareil, puis scanne le nouveau code."),
        "push.attend": "{nom} attend",
        "tache.installer-handy": "installer Handy",
    },
    "en": {
        "ouvrir": "Open herdr",
        "essais.titre": "Too many attempts",
        "essais.texte": "Too many links tried from this device. Try again in a minute.",
        "expire.titre": "Link expired",
        "expire.texte": ("This link has already been used, has expired (5 minutes) or does not exist. On a device "
                         "that is already connected: Settings › Connect a device, then scan the new code."),
        "push.attend": "{nom} is waiting",
        "tache.installer-handy": "Install Handy",
    },
}


def t(langue: str, cle: str, **params) -> str:
    s = TEXTES.get(langue if langue in LANGUES else DEFAUT, TEXTES[DEFAUT])[cle]
    return s.format(**params) if params else s
