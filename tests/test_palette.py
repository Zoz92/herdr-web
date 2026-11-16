"""static/palette.js sous node (pur) : filtre flou, entrées, raccourcis (brief palette, 2026-11-15)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parent.parent / "static"


def js(code, module=None, langue="fr"):
    """Le module sous node, dans la langue donnée (fr : celle des assertions écrites avant l'anglais)."""
    node = shutil.which("node")
    assert node, "node absent : il est dans le devShell (flake.nix), lancer via nix develop"
    src = f"const P = require({json.dumps(str(module or STATIC / 'palette.js'))});\n" \
          f"require({json.dumps(str(STATIC / 'i18n' / 'i18n.js'))}).poser({json.dumps(langue)});\n" \
          f"process.stdout.write(JSON.stringify((() => {{ {code} }})()));"
    r = subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


CTX = {
    "agents": [
        {"addr": "t1", "nom": "concepteur-palette", "role": "concepteur", "modele": "opus", "etat": "working", "space": "web"},
        {"addr": "t2", "nom": "zsh", "role": "terminal", "etat": "", "space": "web", "terminal": True},
        {"addr": "t3", "nom": "relecteur", "role": "checker", "modele": "sonnet", "etat": "idle", "space": "hub"},
    ],
    "spaces": [{"nom": "hub", "addr": "t3", "n": 1}, {"nom": "vide"}],
    "ouvert": {"addr": "t1", "nom": "concepteur-palette", "claude": True, "bloque": False, "epinglable": True, "epingle": False},
    "commandes": [{"name": "model", "description": "Changer de modèle"}, {"name": "compact", "description": "Compacter"},
                  {"name": "clear", "description": "Vider la conversation"}],
    "creer": {"spaces": [{"id": "sp1", "nom": "web"}], "connus": [{"id": "claude", "nom": "Claude Code"},
                                                                 {"id": "opencode", "nom": "OpenCode"}]},
    "rubriques": [["voix", "Voix"], ["affichage", "Affichage"]],
    "tri": "priority", "tris": [["priority", "Priority"], ["grouped", "Grouped"]],
    "groupes": [{"cle": "sec-working", "titre": "Working", "plie": False}, {"cle": "pinned", "titre": "Pinned", "plie": True}],
}


def entrees(q, ctx=CTX):
    return js(f"return P.filtrer(P.construire({json.dumps(ctx)}), {json.dumps(q)})"
              ".map((e) => [e.section, e.titre, e.action.type, !!e.inactif]);")


def test_sections_dans_l_ordre_et_requete_vide():
    r = entrees("")
    sections = [s for s, *_ in r]
    assert sections == sorted(sections, key=["Aller à", "Agent ouvert", "Créer", "Page"].index)
    titres = [t for _, t, *_ in r]
    # l'agent ouvert ne se propose pas lui-même ; un space sans agent n'a nulle part où aller
    assert "concepteur-palette" not in titres[:3] and "zsh" in titres and "relecteur" in titres
    assert "hub" in titres and "vide" not in titres
    # les commandes « / » restent cachées sans requête : une entrée y mène
    assert "/model" not in titres and "Commandes « / » de Claude" in titres
    for t in ("Touche Échap", "Touche Ctrl+C", "Touche Ctrl+L", "Touche Entrée", "Touche ↑", "Touche ↓",
              "Épingler", "Fermer…", "Nouveau terminal", "Démarrer Claude Code (opus)", "Démarrer OpenCode",
              "Réglages", "Réglages › Affichage", "Trier : Grouped", "Plier « Working »", "Déplier « Pinned »",
              "Plier toutes les sections", "Recharger (vider le cache)"):
        assert t in titres, t


def test_entrees_et_raccourcis_en_anglais():
    # langue par défaut de la page : sections, titres, aides et refus de raccourci en anglais
    r = js(f"return P.filtrer(P.construire({json.dumps(CTX)}), '').map((e) => [e.section, e.titre, e.detail]);", langue="en")
    sections = [s for s, *_ in r]
    assert sections == sorted(sections, key=["Go to", "Open agent", "Create", "Page"].index) and len(set(sections)) == 4
    titres = [t for _, t, _ in r]
    for t in ("Key Esc", "Key Ctrl+C", "Key Enter", "Key ↑", "Pin", "Close…", "New terminal", "Start Claude Code (opus)",
              "Start OpenCode", "Settings", "Collapse all sections", "Reload (clear the cache)", "Claude “/” commands"):
        assert t in titres, t
    assert ["Open agent", "Key Esc", "cancel, close a menu"] in r
    assert ["Open agent", "Close…", "concepteur-palette · asks for confirmation"] in r
    assert js("return [P.libelle('ctrl+shift+p'), P.libelle('meta+space'), P.raccourciValide('ctrl+w').raison, "
              "P.raccourciValide('ctrl+l', { action: 'nettoyer', combo: 'ctrl+l' }).raison, "
              "P.avertissement('ctrl+shift+p', 'Mozilla/5.0 Firefox/130.0')];", langue="en") == [
        "Ctrl+Shift+P", "Meta+Space", "taken by the browser", "already used for clearing the terminal",
        "In Firefox, Ctrl+Shift+P opens a private window: the page never receives it. Choose another combination (Change)."]


def test_filtre_flou_accents_et_sous_suite():
    assert entrees("relec")[0][1] == "relecteur"
    assert entrees("reglages aff")[0][1] == "Réglages › Affichage"   # sans accent, deux mots
    assert entrees("rlct")[0][1] == "relecteur"                         # sous-suite r…l…c…t
    assert entrees("ctrl l")[0][1] == "Touche Ctrl+L"
    assert entrees("checker")[0][1] == "relecteur"                      # le rôle, dans le détail
    assert entrees("xyzq") == []


def test_slash_delegue_a_commandes_js():
    r = entrees("/c")
    assert [t for _, t, *_ in r] == ["/clear", "/compact"]  # rangées par commandes.js (début du nom, puis longueur)
    assert all(a == "commande" for _, _, a, _ in r)
    assert entrees("/mod")[0][1] == "/model"


def test_bloque_touches_inactives_et_terminal_sans_commandes():
    ctx = dict(CTX, ouvert=dict(CTX["ouvert"], bloque=True, claude=False, epinglable=False))
    r = entrees("touche", ctx)
    assert r and all(inactif for s, t, _, inactif in r if t.startswith("Touche"))
    titres = [t for _, t, *_ in entrees("", ctx)]
    assert "Commandes « / » de Claude" not in titres and "Épingler" not in titres and "Fermer…" in titres
    assert entrees("/c", ctx) == []


def test_sans_agent_ouvert_ni_rien():
    r = js("return P.filtrer(P.construire({}), '').map((e) => e.titre);")
    assert "Touche Ctrl+L" not in r and "Réglages" in r and "Nouveau space…" in r
    assert js("return P.filtrer(null, 'x').length;") == 0


def test_actions_portent_ce_qu_il_faut_executer():
    r = js(f"return P.construire({json.dumps(CTX)}).map((e) => e.action);")
    assert {"type": "touche", "addr": "t1", "key": "ctrl+l", "nom": "Ctrl+L"} in r
    assert {"type": "ouvrir", "space": {"id": "sp1", "nom": "web"}, "kind": "claude", "model": "opus"} in r
    assert {"type": "aller", "addr": "t3"} in r and {"type": "reglages", "rubrique": "voix"} in r
    assert {"type": "pli", "cle": "pinned"} in r and {"type": "tri", "mode": "grouped"} in r


def test_touches_meme_liste_blanche_que_le_serveur():
    from herdr_web.actions import TOUCHES
    assert [k for k, _ in js("return P.TOUCHES;")] == list(TOUCHES)


def ev(key, **mods):
    return dict({"key": key, "ctrlKey": False, "shiftKey": False, "altKey": False, "metaKey": False}, **mods)


@pytest.mark.parametrize("e,combos,attendu", [
    (ev("P", ctrlKey=True, shiftKey=True), None, "palette"),
    (ev("p", ctrlKey=True, shiftKey=True), None, "palette"),
    (ev("k", ctrlKey=True), None, None),                   # Ctrl+K n'est pas un raccourci par défaut (ajout 1 du brief)
    (ev("p", ctrlKey=True), None, None),                   # Ctrl+P : impression, laissée au navigateur
    (ev("l", ctrlKey=True), None, "nettoyer"),
    (ev("L", ctrlKey=True, shiftKey=True), None, None),
    (ev("l", ctrlKey=True, altKey=True), None, None),
    (ev("k", ctrlKey=True), {"palette": "ctrl+k"}, "palette"),         # combinaison choisie dans les réglages
    (ev("P", ctrlKey=True, shiftKey=True), {"palette": "ctrl+k"}, None),  # l'ancienne ne répond plus
    (ev("l", ctrlKey=True), {"nettoyer": None}, None),                 # interception désactivée (ajout 2)
    (ev("l", ctrlKey=True, altKey=True), {"nettoyer": "ctrl+alt+l"}, "nettoyer"),
    (ev("l", ctrlKey=True), {"nettoyer": "ctrl+alt+l"}, None),
    (dict(ev("p", ctrlKey=True, shiftKey=True), isComposing=True), None, None),
    (ev("Control", ctrlKey=True), None, None),
])
def test_raccourci(e, combos, attendu):
    assert js(f"return P.raccourci({json.dumps(e)}, {json.dumps(combos)});") == attendu


def test_combinaison_canonique():
    assert js("return P.combinaison({ key: 'P', ctrlKey: true, shiftKey: true, altKey: true, metaKey: false });") \
        == "ctrl+alt+shift+p"
    assert js("return P.combinaison({ key: 'π', code: 'KeyP', altKey: true });") == "alt+p"
    assert js("return P.combinaison({ key: ' ', ctrlKey: true });") == "ctrl+space"
    assert js("return P.combinaison({ key: 'Shift', shiftKey: true });") is None
    assert js("return P.libelle('ctrl+shift+p');") == "Ctrl+Shift+P"


PALETTE_AUTRE = {"action": "nettoyer", "combo": "ctrl+l"}   # la palette ne reprend pas Ctrl+L (§1)
NETTOYER_AUTRE = {"action": "palette", "combo": "ctrl+shift+p"}


@pytest.mark.parametrize("c,autre,ok", [
    ("ctrl+shift+p", PALETTE_AUTRE, True), ("ctrl+k", PALETTE_AUTRE, True), ("alt+p", PALETTE_AUTRE, True),
    ("meta+k", PALETTE_AUTRE, True), ("ctrl+shift+space", PALETTE_AUTRE, True),
    ("ctrl+w", PALETTE_AUTRE, False), ("ctrl+t", PALETTE_AUTRE, False), ("ctrl+n", PALETTE_AUTRE, False),
    ("ctrl+tab", PALETTE_AUTRE, False), ("ctrl+shift+tab", PALETTE_AUTRE, False),
    ("ctrl+l", PALETTE_AUTRE, False), ("p", PALETTE_AUTRE, False), ("shift+p", PALETTE_AUTRE, False),
    ("", PALETTE_AUTRE, False), (None, PALETTE_AUTRE, False), ("hyper+p", PALETTE_AUTRE, False),
    # nettoyer : Ctrl+L permis, mêmes refus, et jamais la combinaison de la palette (ajout 2)
    ("ctrl+l", NETTOYER_AUTRE, True), ("ctrl+alt+l", NETTOYER_AUTRE, True),
    ("ctrl+shift+p", NETTOYER_AUTRE, False), ("ctrl+w", NETTOYER_AUTRE, False), ("l", NETTOYER_AUTRE, False),
])
def test_raccourci_valide(c, autre, ok):
    r = js(f"return P.raccourciValide({json.dumps(c)}, {json.dumps(autre)});")
    assert r["ok"] is ok and (ok or r["raison"])


def test_raccourci_valide_dit_pourquoi():
    assert js("return P.raccourciValide('ctrl+l', { action: 'nettoyer', combo: 'ctrl+l' }).raison;") \
        == "déjà pris pour nettoyer le terminal"
    assert js("return P.raccourciValide('ctrl+w').raison;") == "pris par le navigateur"
    assert js("return P.DEFAUTS;") == {"palette": "ctrl+shift+p", "nettoyer": "ctrl+l"}


@pytest.mark.parametrize("o,permis", [
    ({"vue": True, "focus": "aucun"}, True),
    ({"vue": True, "focus": "terminal"}, True),
    ({"vue": True, "focus": "message", "champRempli": False}, True),
    ({"vue": True, "focus": "message", "champRempli": True}, False),
    ({"vue": True, "focus": "autre"}, False),
    ({"vue": True, "focus": "aucun", "modal": True}, False),
    ({"vue": True, "focus": "aucun", "sous": True}, False),
    ({"vue": False, "focus": "aucun"}, False),
])
def test_ctrl_l_permis(o, permis):
    assert js(f"return P.ctrlLPermis({json.dumps(o)});") is permis


FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"


@pytest.mark.parametrize("c,ua,averti", [
    ("ctrl+shift+p", FIREFOX, True),    # key_privatebrowsing, reserved="true" : jamais remis à la page
    ("meta+shift+p", FIREFOX, True),
    ("ctrl+shift+p", CHROME, False),
    ("ctrl+alt+k", FIREFOX, False),
    ("ctrl+shift+p", None, False),
])
def test_avertissement_firefox(c, ua, averti):
    t = js(f"return P.avertissement({json.dumps(c)}, {json.dumps(ua)});")
    assert bool(t) is averti
    if averti:
        assert "Firefox" in t and "fenêtre privée" in t and "autre combinaison" in t


def test_defaut_inchange_sous_firefox():
    assert js("return P.DEFAUTS.palette;") == "ctrl+shift+p"
    assert js("return P.raccourciValide('ctrl+shift+p', { action: 'nettoyer', combo: 'ctrl+l' }).ok;") is True
