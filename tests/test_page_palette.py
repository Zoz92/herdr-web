"""Palette de commandes et Ctrl+L sous le banc node (brief palette, 2026-11-15, et ses deux ajouts) :
ouverture, clavier, exécution d'actions, Ctrl+L ⇒ POST keys, refus en blocked, raccourcis réglables.
Chaque garde a son témoin : la page mutée doit faire rougir le scénario (test_temoins_*)."""
import pytest

from test_page import STATIC, banc


@pytest.fixture(scope="module")
def p():
    return banc("palette")


def keys(touche, addr="t1"):
    return [[f"/api/agents/{addr}/keys", {"key": touche}]]


def test_ouverture_ctrl_shift_p_dialogue_accessible(p):
    o = p["ouverture"]
    assert o["prevenu"] and o["boite"] and o["role"] == "dialog" and o["modal"] == "true" and o["focus"]
    assert o["combobox"] == "combobox" and o["actif"] == "palette-0"
    assert o["sections"] == ["Aller à", "Agent ouvert", "Créer", "Page"]
    assert o["slashCache"] and o["pied"] == "Ctrl+Shift+P"
    assert p["ctrlK"] == [False, False]  # ajout 1 : Ctrl+Shift+P seul par défaut


def test_clavier_fleches_et_aria_activedescendant(p):
    assert p["clavier"] == {"actif": "palette-1", "selId": "palette-1", "ariaSel": "true"}
    assert p["echap"] is False


def test_filtre_puis_entree_va_au_fil(p):
    assert p["filtre"] == "relecteur" and p["aller"] == ["#/a/t3", False]


def test_commandes_slash_remplissent_le_champ(p):
    assert p["slash"] == ["/model"] and p["commande"] == "/model "


def test_touche_par_la_palette_post_keys(p):
    assert p["toucheTitre"] == "Touche Ctrl+C" and p["touchePalette"] == keys("ctrl+c")


def test_ctrl_l_post_keys_et_retenu(p):
    assert p["ctrlL"] == {"prevenu": True, "keys": keys("ctrl+l")}
    # champ de message vide : envoyé ; rempli : laissé au navigateur
    assert p["ctrlLChamp"] == {"vide": True, "rempli": False, "keys": keys("ctrl+l")}
    assert p["ctrlLEcran"] == keys("ctrl+l")  # focus sur l'écran xterm (lecture seule)


def test_bloque_rien_ne_part(p):
    assert p["bloqueCtrlL"]["keys"] == [] and "attend un choix" in p["bloqueCtrlL"]["statut"]
    assert p["bloqueEntree"] == {"titre": "Touche Entrée", "desactive": "true"}
    assert p["bloquePalette"]["keys"] == [] and p["bloquePalette"]["ouverte"] and "attend un choix" in p["bloquePalette"]["note"]
    assert "attend un choix" in p["serveur409"]


def test_fermer_passe_par_la_confirmation(p):
    assert p["fermer"] == {"confirmation": "Fermer « agent-t1 » ?", "close": 0}


def test_creer_par_la_route_du_menu_plus(p):
    assert p["creer"] == [["/api/spaces/sp1/open", {"kind": "terminal", "folder": "/home/user/t1"}]]


def test_page_tri_et_rubrique(p):
    assert p["tri"] == "grouped" and p["reglages"] == [True, "affichage"]


def test_raccourci_palette_reglable(p):
    assert p["kbdAvant"] == "Ctrl+Shift+P"
    assert p["refusW"] == ["Ctrl+W : pris par le navigateur", None]
    assert p["refusL"] == ["Ctrl+L : déjà pris pour nettoyer le terminal", None]
    assert p["pendantCapture"] == [False, False]  # la capture ne rouvre pas la palette
    assert p["accepte"] == ["raccourci : Ctrl+Alt+K", "ctrl+alt+k", "Ctrl+Alt+K"]
    assert p["ancien"] == [False, False] and p["nouveau"] == [True, True]


def test_nettoyer_reglable_et_desactivable(p):
    assert p["nettoyerRefus"][0].endswith("Ctrl+Alt+K : déjà pris pour la palette") and p["nettoyerRefus"][1] is None
    assert p["interrupteurAvant"] == "true" and p["interrupteurStocke"] == "0"
    assert p["desactive"] == {"prevenu": False, "keys": []}


def test_bouton_en_tete_et_rien_de_nul(p):
    assert p["bouton"] is True and p["boutonFermer"] is True and p["fautes"] == []


# ---------- témoins : chaque garde retirée fait rougir le scénario ----------
@pytest.mark.parametrize("avant,apres,cle,attendu_faux", [
    # refus en blocked côté page
    ('if (a && a.state === "blocked") { statut(t("erreur.attendChoixCarte"), true); chargerChoix(); return; }',
     "", "bloqueCtrlL", lambda v: v["keys"] == []),
    # entrée inactive exécutée quand même
    ("if (e.inactif) { p.note.textContent = e.detail; return; }", "", "bloquePalette", lambda v: v["keys"] == []),
    # champ rempli : Ctrl+L ne doit pas partir
    ("champRempli: !!(S.vue && S.vue.champ && S.vue.champ.value.trim())", "champRempli: false",
     "ctrlLChamp", lambda v: v["rempli"] is False),
    # aria-activedescendant
    ('p.champ.setAttribute("aria-activedescendant", "palette-" + p.sel)', "0", "clavier", lambda v: v["actif"] == "palette-1"),
    # interrupteur ignoré
    ("nettoyer: nettoyerActif() ? comboStocke(\"nettoyer\") : null", "nettoyer: comboStocke(\"nettoyer\")",
     "desactive", lambda v: v["keys"] == []),
    # refus de la combinaison de l'autre action
    ("venue\n    const v = PALETTE.raccourciValide(c, autre());", "venue\n    const v = PALETTE.raccourciValide(c);",
     "refusL", lambda v: v[1] is None),
    # la capture ne doit pas laisser passer le raccourci
    ("  if (S.captureRaccourci) return;\n  const r = PALETTE.raccourci", "  const r = PALETTE.raccourci",
     "pendantCapture", lambda v: v == [False, False]),
])
def test_temoins_le_banc_mord(tmp_path, avant, apres, cle, attendu_faux):
    src = (STATIC / "app.js").read_text()
    assert src.count(avant) == 1, avant
    mute = tmp_path / "app.js"
    mute.write_text(src.replace(avant, apres))
    try:
        d = banc("palette", mute)
    except AssertionError:
        return  # le scénario plante : rouge aussi
    assert not attendu_faux(d[cle]), d[cle]


# ---------- réserves du checker (brief palette-2) ----------
def test_tab_reste_dans_la_palette(p):
    # champ → × → champ, puis Maj+Tab : × ; jamais « dehors », Tab toujours retenu
    assert p["tab"] == ["champ", True, "fermer", True, "champ", True, "fermer"]


def test_ctrl_l_dans_un_autre_champ_reste_au_navigateur(p):
    assert p["ctrlLInput"] == {"input": True, "prevenu": False, "keys": []}


def test_occupe_dit_a_l_ecran(p):
    assert "envoi vers ce terminal est en cours" in p["occupe"]


def test_firefox_averti_dans_les_reglages(p):
    assert len(p["firefox"]) == 1 and "Sous Firefox, Ctrl+Shift+P ouvre une fenêtre privée" in p["firefox"][0]
    assert p["firefoxApres"] == [""]
    assert "Sans effet visible sur un agent Claude Code" in p["aideNettoyer"]


@pytest.mark.parametrize("avant,apres,cle,attendu_faux", [
    # piège à focus retiré
    ('    if (ev.key !== "Tab") return;\n    ev.preventDefault();\n', '    return;\n',
     "tab", lambda v: v == ["champ", True, "fermer", True, "champ", True, "fermer"]),
    # M3 du checker : un autre input compté « aucun »
    ('return /^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName) || f.isContentEditable ? "autre" : "aucun";', 'return "aucun";',
     "ctrlLInput", lambda v: v["keys"] == [] and v["prevenu"] is False),
    # occupe non dit
    ('    else if (e.code === "occupe") statut(t("erreur.terminalOccupe"), true);\n', "",
     "occupe", lambda v: "envoi vers ce terminal est en cours" in v),
    # avertissement Firefox non affiché
    ("    alerte.textContent = PALETTE.avertissement(comboStocke(action), navigator.userAgent);\n", "",
     "firefox", lambda v: len(v) == 1 and "Firefox" in v[0]),
])
def test_temoins_des_reserves(tmp_path, avant, apres, cle, attendu_faux):
    src = (STATIC / "app.js").read_text()
    assert src.count(avant) == 1, avant
    mute = tmp_path / "app.js"
    mute.write_text(src.replace(avant, apres))
    try:
        d = banc("palette", mute)
    except AssertionError:
        return  # le scénario plante : rouge aussi
    assert not attendu_faux(d[cle]), d[cle]
