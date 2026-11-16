"""Roue crantée, cadre vert des terminaux, « vu » partagé (2026-11-15), sous le banc node."""
import re
from pathlib import Path

import pytest

from test_page import banc, page_d_avant

STATIC = Path(__file__).parent.parent / "static"
AVANT_RESERVES = "9572e2a"  # PR #25 avant les réserves du checker : témoin des trois tests ci-dessous


@pytest.fixture(scope="module")
def rs():
    return banc("reserves")


@pytest.fixture(scope="module")
def rs_avant(tmp_path_factory):
    return banc("reserves", page_d_avant(AVANT_RESERVES, tmp_path_factory.mktemp("avant")))


def test_reserve_1_agent_ouvert_pas_reannonce(rs, rs_avant):
    assert rs["annonce"] is True and rs["ouvertReannonce"] is False
    assert rs_avant["ouvertReannonce"] is True  # témoin : l'ancienne page le ré-annonçait


def test_reserve_2_seen_en_echec_reessaye_au_retour_ou_au_tour_suivant(rs, rs_avant):
    assert (rs["premierEssai"], rs["apresRafraichis"], rs["retourVisible"], rs["tourSuivant"]) == (1, 1, 2, 3)
    assert rs_avant["apresRafraichis"] == 4  # témoin : un essai par rafraîchissement


def test_reserve_3_une_seule_vibration_avec_la_notification(rs, rs_avant):
    assert rs["vibrations"] == 1 and rs["bandeau"] is True
    assert rs_avant["vibrations"] == 2  # témoin : bandeau puis notification


@pytest.fixture(scope="module")
def r():
    return banc("reglages")


RUBRIQUES = ["Voix", "Notifications", "Affichage", "Appareils", "À propos"]


def test_roue_crantee_dans_la_tete_du_panneau(r):
    assert r["roue"] is True
    # refonte (brief design-reglages) : une rubrique par onglet latéral, au lieu de cinq sections empilées
    assert r["titres"] == RUBRIQUES and r["onglets"] == RUBRIQUES


def test_a_handy_parametres_reels(r):
    # résumé lisible dans la carte « Moteur », chemins complets dans « Détails »
    lg, ch = r["lignes"], r["chemins"]
    assert (lg["Handy 0.9.8"], lg["Modèle"], lg["Langue"], lg["Délai maximal"], lg["ffmpeg"]) == (
        "● prêt", "Qwen3", "fr", "90 s", "✓ trouvé")
    assert ch == {"binaire": "/nix/store/x-handy-0.9.8/bin/h", "modèle": "Qwen3", "ffmpeg": "/bin/ffmpeg"}


def test_a_handy_desactive_ecrit_au_serveur_et_retire_le_micro(r):
    assert r["voixRole"] == ["switch", "true", 0]  # interrupteur accessible, atteignable au clavier
    assert r["appelVoix"] == [{"handy": False}] and r["voix"] is False
    assert r["voixAria"] == "false" and r["pastille"] == "○ désactivée"
    assert r["microCoupe"] is True      # vue d'un agent : pas de micro
    assert r["microRevenu"] is False    # réactivé depuis un autre appareil (événement « reglages ») : il revient


def test_b_version_deployee(r):
    assert r["lignes"]["Commit"] == "abc123def456" and r["lignes"]["Cache"] == "herdr-coquille-v28"
    assert r["lignes"]["Version"] == r["versionNav"] == "v28 · abc123d"  # aussi en bas de la colonne


def test_c_notifications_permission_au_clic_seulement_puis_notification(r):
    assert r["demandesAvantClic"] == 0 and r["demandesApresClic"] == 1
    assert r["notifStockee"] == "1"
    assert r["notifs"] == [["Needs you : agent-t1", "t1"]] and r["vibre"] is True


def test_d_sections_masquees_par_appareil_au_clavier(r):
    assert r["sectionsAvant"] == ["sec-idle", "sec-terminal"]
    # Espace sur « Terminaux » la masque, une autre touche ne bascule rien, Entrée la rend
    assert r["clavier"] == ["false", True]
    assert r["sectionsMasquees"] == ["sec-idle"]
    assert r["stockage"] == ["1"]
    assert r["entree"] == ["true", ["sec-idle", "sec-terminal"]]


def test_d_tri_par_defaut_controle_segmente(r):
    assert r["lignes"]["Tri par défaut"] == "segmente"
    assert r["segments"] == [["Priority", "true"], ["Grouped", "false"]]
    assert r["triApres"] == ["grouped", ["false", "true"]]
    assert r["triClavier"] == "priority"  # flèche gauche : retour à Priority


def test_e_session_et_deconnexion_confirmee(r):
    # le modèle de session est un texte de la page (dictionnaire), plus celui du serveur
    assert r["lignes"]["Durée du cookie"] == ["365 jours", "un jeton partagé ; aucune liste de sessions côté serveur"]
    assert r["lignes"]["Abonnés au push"] == ["2", "fcm.googleapis.com"]  # nombre, et les services sans doublon
    # le bouton seul ne déconnecte pas : il demande ; « Annuler » revient sans appel
    assert r["avantConfirmation"] == [] and r["question"] == "Se déconnecter de cet appareil ?"
    assert r["focusAnnuler"] == "Annuler"
    assert r["apresAnnuler"] == [0, True, False, True]
    assert r["logout"] == ["POST /api/logout"] and r["ferme"] is True


def test_champs_absents_ou_serveur_ancien_sans_null_a_l_ecran(r):
    assert r["fautesVide"] == [] and r["fautesAncien"] == []
    assert "inconnu" in r["valsVide"] and r["versionVide"] == "version inconnue"
    assert r["ancien"].startswith("réglages illisibles")


@pytest.fixture(scope="module")
def rr():
    return banc("reglages-rubriques")


def test_rubriques_bascule_et_memoire(rr):
    assert rr["ouverture"] == {"selection": ["voix"], "visibles": ["Voix"], "surPage": False}
    assert rr["tabindex"] == [0, -1, -1, -1, -1]       # un seul arrêt de tabulation dans la colonne
    assert rr["relies"] == ["tab", True, True]          # onglet -> page (aria-controls / aria-labelledby)
    assert rr["apresClic"] == {"selection": ["affichage"], "visibles": ["Affichage"], "surPage": True}
    assert rr["stockee"] == "affichage"
    assert rr["reouverture"]["selection"] == ["notif"]  # la dernière rubrique ouverte revient
    assert rr["inconnue"] == ["voix"]                   # valeur stockée inconnue : Voix
    assert rr["version"] == "v27 · abc123d" and rr["fautes"] == []


def test_rubriques_au_clavier(rr):
    bas = rr["flecheBas"]
    assert bas[0]["selection"] == ["appareils"] and bas[1] == "appareils" and bas[2] is True
    assert rr["flecheHaut"] == ["notif"]


def test_telephone_liste_puis_page_puis_retour(rr):
    # « sur-page » : sur un écran ≤ 600 px, la CSS cache la colonne et montre la page (et l'inverse sans)
    assert rr["ouverture"]["surPage"] is False and rr["apresClic"]["surPage"] is True
    assert rr["apresRetour"]["surPage"] is False and rr["focusRetour"] == "affichage"
    assert rr["reouverture"]["surPage"] is False        # rouvert : la liste d'abord
    css = (STATIC / "style.css").read_text()
    tel = css.split("@media (max-width: 600px) {")[-1]
    assert ".reglages:not(.sur-page) .reglages-pages { display: none; }" in tel
    assert ".reglages.sur-page .reglages-nav { display: none; }" in tel
    assert ".retour-reglages { display: inline-block; }" in tel


def test_textes_du_panneau_en_un_seul_endroit():
    js = (STATIC / "app.js").read_text()
    debut = js.index("const TXT_REGLAGES = (() => {")
    fin = js.index("// QR code du lien d'appairage")
    corps = js[js.index("\n})();\n", debut):fin]
    # hors de l'objet de libellés, aucun texte français en dur dans le code du panneau
    assert not re.search(r'"[^"\n]*[éèàùêç][^"\n]*"', corps), re.findall(r'"[^"\n]*[éèàùêç][^"\n]*"', corps)


def test_3_vu_partage_ouvrir_un_agent_pose_le_label():
    v = banc("vu")
    assert v["ouvert"] == ["/api/agents/t1/seen"]        # done jamais vu : marqué à l'ouverture
    assert v["rafraichi"] == v["ouvert"]                 # pas de doublon tant que le label n'est pas revenu
    assert v["dejaVu"] == v["ouvert"]                    # seen >= turn : rien
    assert v["sous"] == v["ouvert"]                      # sous-agent : rien
    assert v["nouveauTour"] == ["/api/agents/t1/seen"] * 3  # rouvert, puis le tour suivant fini sous les yeux


def test_2_icone_terminal_dans_un_cadre_arrondi_vert():
    s = (STATIC / "prog-terminal.svg").read_text()
    # herdr-app : lucide « square-terminal », un >_ dans un carré à coins arrondis
    assert re.search(r'<rect[^>]*width="18"[^>]*rx="2"', s)
    assert 'd="m7 11 2-2-2-2"' in s and 'd="M11 13h4"' in s
    css = (STATIC / "style.css").read_text()
    assert re.search(r"\.role\.terminal \{[^}]*background: var\(--green\)", css)
    assert "--green: #a6e3a1" in css


def test_cache_du_service_worker_incremente():
    assert 'const CACHE = "herdr-coquille-v39";' in (STATIC / "sw.js").read_text()


def test_4_bandeau_needs_you_suit_l_etat():
    b = banc("bandeau")
    assert b["annonce"] == ["t1", "Needs you : agent-t1 — answer: oui"]
    assert b["deux"] == ["t2", "Needs you : agent-t2 — Bash: ls"]
    assert b["msgChange"] == ["t2", "Needs you : agent-t2 — Bash: rm x"]
    assert b["repondu"] == ["t1", "Needs you : agent-t1 — answer: oui"]  # t2 répondu : t1 attend encore
    assert b["personne"] is None and b["disparu"] is None               # avant : restait jusqu'au clic
    assert b["vibrations"] == 3                                          # une par blocage nouveau


def test_c_permission_demandee_seulement_sur_un_geste():
    # deux appels, chacun dans un rappel de geste : le bouton « Activer les notifications » (Web Push)
    # et la coche des réglages ; aucun au chargement (le banc ne lance pas demarrer(), d'où cette garde)
    js = (STATIC / "app.js").read_text()
    assert js.count("requestPermission(") == 2
    corps = js.split("function sectionsReglages(")[1].split("\nfunction ")[0]
    assert corps.count("requestPermission(") == 1
    push = js.split("async function initPush(")[1].split("\n}\n")[0]
    assert 'bouton.addEventListener("click", async () => {' in push and push.count("requestPermission(") == 1


TABLE = {  # brief point 5 : table du brief (2026-11-15), texte exact, [repos, travail]
    "maker": ["Maker : écrit le code d'après un brief", "Maker : écrit le code"],
    "concepteur": ["Concepteur : conçoit et écrit, sans modèle existant", "Concepteur : conçoit et écrit"],
    "checker": ["Checker : vérifie le travail d'un maker, sans rien modifier", "Checker : vérifie"],
    "checker-conception": ["Checker de conception : vérifie le travail d'un concepteur", "Checker de conception : vérifie"],
    "reviewer": ["Reviewer : relit les changements de code", "Reviewer : relit"],
    "claude": ["Claude Code : en attente", "Claude Code : au travail"],
}


@pytest.fixture(scope="module")
def ib():
    return banc("infobulles")


@pytest.mark.parametrize("role", list(TABLE))
def test_5_infobulle_repos_et_travail(ib, role):
    assert ib[role] == TABLE[role]


def test_5_infobulles_shell_terminal_autre_programme(ib):
    assert ib["shell"] == "Exécute une commande shell"             # activité, working seulement
    assert ib["claudeSansRole"] == "Claude Code : en attente"
    assert ib["terminal"] == ["Terminal sans agent", "attr", "Terminal sans agent"]  # title et aria-label
    assert ib["autre"] == ["codex", "opencode"]                     # autre programme : son nom
    assert ib["sous"] == "Checker : vérifie"                        # sous-agent : même table
