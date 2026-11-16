"""Zone Agents alignée sur la spec commune herdr-app / herdr-web (2026-11-15), sous le banc node.
Chaque assertion échoue sur la page d'avant (témoin en bas, figé sur main 6498b31)."""
from pathlib import Path

import pytest

from test_page import banc, page_d_avant

AVANT = "6498b31"


@pytest.fixture(scope="module")
def d():
    return banc("harmonie")


def test_points_1_3_4_5_sections_et_ordre_en_mode_priority(d):
    # Pinned d'abord (par urgence), Needs you, Working (récent d'abord), Done, Idle, Error, puis
    # Terminaux ; la famille de w1 (enfants k1, k2) reste sous w1 en Working
    assert d["priorite"] == [
        ["pinned", ["pinb", "pin"]], ["sec-blocked", ["b1"]], ["sec-working", ["w2", "w1", "ip"]],
        ["sec-done", ["d1"]], ["sec-idle", ["i1"]], ["sec-error", ["er"]], ["sec-terminal", ["term"]]]
    assert d["titres"][0] == "Pinned2" and d["titres"][-1] == "Terminaux1"


def test_point_7_statut_a_gauche(d):
    s = d["statuts"]
    assert s["w2"] == ["statut working", ""]           # spinner
    assert s["b1"] == ["statut blocked compact", "!"]  # sous l'en-tête « Needs you » en Priority
    assert s["pinb"] == ["statut blocked", "Needs you"]  # pilule qui pulse ailleurs
    assert s["i1"] == ["statut idle", ""] and s["d1"] == ["statut done", ""]  # anneau, point plein
    assert s["er"] == ["statut error", ""] and s["term"] == ["statut terminal", ""]


def test_point_7_enfant_bloque_sous_needs_you_porte_le_point_d_exclamation():
    # comme le mod : en Priority, toute la famille rangée sous « Needs you » est compacte
    assert banc("enfants")["statutR"] == "!"


def test_point_8_icones(d):
    i = d["icones"]
    assert i["w2"] == ["svg", "role claude anime", None]                      # étoile qui tourne
    assert i["i1"] == ["span", "role claude dort", None]                      # personnage au repos : il dort (idle)
    assert i["term"] == ["span", "role terminal", None]                       # icône de la table
    assert i["k1"] == ["svg", "role claude anime", None]


def test_point_9_badge_fable(d):
    assert d["badgeFable"] == ["modele fable", "fable 5.2"]


def test_points_11_15_enfant_hc_avec_duree_et_dossier_sans_machine(d):
    assert d["enfant"] == {"depuis": "depuis 2 min", "l2": "proj › wt⎇ main", "l3": False, "petit": "k2"}


def test_point_14_sous_agent_statut_icone_titre_badge_contexte(d):
    assert d["sous"] == [["arbre", "statut", "role", "nom", "modele", "ctx"]]


def test_point_6_plier_un_groupe_et_un_agent(d):
    assert d["groupePlie"]["plan"] == ["sec-working", []] and d["groupePlie"]["chevron"] is True
    assert "sec-working" in d["groupePlie"]["memo"]
    assert d["chevronOuvert"] == ""
    assert d["agentPlie"]["k1"] is False and d["agentPlie"]["compte"] == "2"
    assert "ag-w1" in d["agentPlie"]["memo"]


def test_point_6_epingler(d):
    assert [c for _, _, c in d["epingles"]] == [{"pinned": True}, {"pinned": False}]
    assert [p for _, p, _ in d["epingles"]] == ["/api/agents/i1/pin", "/api/agents/pin/pin"]


@pytest.fixture(scope="module")
def racine():
    return banc("epingle-racine")


def test_m1_epingle_seulement_sur_les_racines(racine):
    # bouton au survol : ni c (enfant) ni g (petit-enfant) ; boîte de l'appui long : idem
    assert racine["avecEpingle"] == ["agent-p", "agent-r"]
    assert racine["boite"]["c"] == ["Annuler", "Fermer"] and racine["boite"]["g"] == ["Annuler", "Fermer"]
    assert racine["boite"]["r"] == ["Épingler", "Annuler", "Fermer"]
    assert racine["boite"]["p"] == ["Désépingler", "Annuler", "Fermer"]


def test_m2_un_epingle_est_toujours_une_racine(racine):
    # p porte parent=r mais est épinglé : racine listée en Pinned, jamais enfant de r
    assert "agent-p" not in racine["enfants"] and racine["enfants"] == ["agent-c", "agent-g"]
    assert "agent-p" in racine["racinePinned"]


def test_urgence_compte_les_sous_agents_des_enfants_hc():
    # racine idle, enfant idle dont un sous-agent tourne : la famille est en Working
    assert banc("urgence-sous-enfant")["plan"] == [["sec-working", ["agent-r"]]]


def test_point_5_menu_de_tri_et_mode_grouped_by_space(d):
    assert d["menu"] == {"lignes": ["Priority", "Grouped by space"], "coche": 0}
    assert d["apresChoix"] == {"menu": False, "memo": "grouped"}
    assert d["groupe"] == [
        # urgence POIDS : blocked > working > error > done > idle
        ["pinned", ["pinb", "pin"]], ["sp-sp", ["b1", "w2", "w1", "ip", "er", "d1"]], ["sp-autre", ["i1"]],
        ["sec-terminal", ["term"]]]
    assert d["statutGroupeB1"] == ["statut blocked", "Needs you"]  # le « ! » est propre à Priority


def test_points_8_9_etoile_en_6_s_et_fable_jaune():
    from pathlib import Path
    css = (Path(__file__).parent.parent / "static" / "style.css").read_text()
    assert ".role.claude.anime { animation: tourne 6s linear infinite; }" in css
    assert "--yellow: #f9e2af" in css and ".modele.fable { color: var(--yellow);" in css


def test_temoin_la_page_d_avant_n_a_ni_sections_ni_statut(tmp_path):
    avant = page_d_avant(AVANT, tmp_path)
    with pytest.raises(AssertionError):  # pas de sections : le scénario lève
        banc("harmonie", avant)


# --- Claude qui dort en idle -------------------------------------------------------------------------

def test_sommeil_seulement_en_idle_sans_role():
    d = banc("sommeil")
    dort = {"tag": "span", "classe": "role claude dort", "zs": 3, "perso": 1, "titre": "Claude Code : en attente"}
    assert d["idle"] == dort and d["roleClaudeExplicite"] == dort
    assert d["sous"] == "role claude dort"
    for etat in ("done", "blocked"):  # le personnage seul, sans z
        assert d[etat]["tag"] == "img" and d[etat]["classe"] == "role claude" and d[etat]["zs"] == 0
    assert d["working"]["tag"] == "svg" and d["working"]["classe"] == "role claude anime"  # l'étoile qui tourne
    assert d["maker"]["classe"] == "role marteau" and d["terminal"]["classe"] == "role terminal"
    assert "dort" not in d["error"]["classe"] and "dort" not in d["null"]["classe"]


def test_sommeil_pas_tant_que_la_famille_travaille():
    d = banc("sommeilFamille")
    etoile = {"tag": "svg", "classe": "role claude anime", "zs": 0, "titre": "Claude Code : au travail"}
    for cas in ("enfantWorking", "enfantBlocked", "sousAgentDEnfant", "sousAgentPropre", "petitEnfant"):
        assert d[cas] == etoile, cas
    for cas in ("familleIdle", "familleDoneEtIdle", "sansFamille"):  # toute la famille au repos : il dort
        assert d[cas] == {"tag": "span", "classe": "role claude dort", "zs": 3, "titre": "Claude Code : en attente"}, cas


def test_sommeil_css_anime_et_statique_si_mouvement_reduit():
    css = (Path(__file__).parent.parent / "static" / "style.css").read_text()
    assert "animation: monte-z 3s" in css and "animation: respire 4s" in css
    reduit = css[css.index("@media (prefers-reduced-motion: reduce) { /* statique"):]
    reduit = reduit[:reduit.index("\n}\n") + 3]
    assert ".role.dort .perso, .role.dort .z { animation: none !important; }" in reduit
    assert ".z2, .role.dort .z3 { display: none; }" in reduit  # un seul z fixe
    assert ".role.dort .z1 { opacity: .85" in reduit
