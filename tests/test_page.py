"""La page sous node (tests/page_banc.js, faux DOM fidèle sur append(null) -> « null »).

Point 28 : jamais « null », « undefined » ni « NaN » à l'écran, quels que soient les champs absents.
Point 27 : filtrage des commandes « / ».
Fix round 1 (I2, m1, m5, m6, m7, sous-agents finis) et points 32 à 35 : chaque scénario échoue sur
la page d'avant le correctif (rejoué contre la version commitée bcdd3d8, voir le rapport)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ICI = Path(__file__).parent
STATIC = ICI.parent / "static"


def banc(scenario, app=None, entree=None, tz=None):
    node = shutil.which("node")
    assert node, "node absent : il est dans le devShell (flake.nix), lancer via nix develop"
    argv = [node, str(ICI / "page_banc.js"), scenario] + ([str(app)] if app else [])
    env = dict(os.environ, TZ=tz) if tz else None
    r = subprocess.run(argv, input=entree, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def page_d_avant(sha, tmp_path):
    """static/app.js au commit `sha`, pour un témoin. Absent du clone (--depth, archive) : ÉCHEC,
    jamais un skip — un témoin sauté se lit comme un témoin vert (réserve de la revue UI v2)."""
    figee = ICI / "fixtures" / "pages-avant" / f"{sha}.js"
    if figee.is_file():  # dépôt public à un commit : page d'avant figée à l'export
        f = tmp_path / "app.js"
        f.write_text(figee.read_text())
        return f
    git = shutil.which("git")
    r = subprocess.run([git, "-C", str(ICI.parent), "show", f"{sha}:static/app.js"],
                       capture_output=True, text=True) if git else None
    if r is None or r.returncode != 0:
        pytest.fail(f"page d'avant {sha} absente de ce clone : le témoin ne peut pas mordre "
                    "(il faut l'historique complet, pas un clone --depth)")
    f = tmp_path / "app.js"
    f.write_text(r.stdout)
    return f


def test_jamais_null_undefined_nan_a_l_ecran():
    d = banc("nuls")
    assert d["etapes"] == 10 and d["texte"] > 100  # le banc a bien rendu quelque chose
    assert d["fautes"] == []


@pytest.mark.parametrize("avant,apres,attendu", [
    # le défaut vu par l'utilisateur : « 4 outilsnull · résultat »
    ("remplacer(run.tete, ", "run.tete.replaceChildren(", "run-tete"),
    # l'en-tête : « nullnullnull » à côté du badge d'état
    ("remplacer(v.meta, badgeEtat(a.state)", "v.meta.replaceChildren(badgeEtat(a.state)", "meta"),
    # une concaténation qui imprime un champ absent
    ('el("span", { class: "nom" }, nomAgent(a))', 'el("span", { class: "nom" }, "" + a.name)', "nom"),
])
def test_temoins_le_banc_mord(tmp_path, avant, apres, attendu):
    src = (STATIC / "app.js").read_text()
    assert src.count(avant) >= 1, avant
    mute = tmp_path / "app.js"
    mute.write_text(src.replace(avant, apres))
    d = banc("nuls", mute)
    assert any(attendu in f for f in d["fautes"]), d["fautes"]


LISTE = [{"name": n} for n in ("model", "model opus", "model sonnet", "resume", "review",
                                 "superpowers:brainstorming", "add-dir", "fin-de-session", "clear")]


@pytest.mark.parametrize("saisie,attendu", [
    ("bonjour", []),
    ("", []),
    ("/re", ["resume", "review"]),  # préfixe d'abord, puis le plus court
    ("/mod", ["model", "model opus", "model sonnet"]),
    ("/model ", ["model opus", "model sonnet"]),
    ("/model s", ["model sonnet"]),
    ("/brain", ["superpowers:brainstorming"]),  # début d'un mot du nom
    ("/dir", ["add-dir"]),
    ("/sion", ["fin-de-session"]),  # sous-chaîne, en dernier recours
    ("/clear maintenant", []),  # après un espace : des arguments, plus de liste
    ("/zzz", []),
])
def test_filtrage_des_commandes(saisie, attendu):
    rendu = banc("filtre", entree=json.dumps({"liste": LISTE, "saisies": [saisie]}))[0]
    assert rendu == attendu


# -- fix round 1 --

def test_i2_un_refresh_pendant_la_saisie_du_space_garde_valeur_et_focus():
    d = banc("saisie-cote")
    assert d["avant"] is True  # le banc suit bien le focus
    assert d["meme"] and d["attache"], "le formulaire a été reconstruit"
    assert (d["nomVisible"], d["dossierVisible"]) == ("mon space", "/home/user/zz")
    assert d["focus"] is True
    assert d["agents"] == 2  # la liste des agents, elle, a bien été refaite (t1, t3)


def test_i2_et_points_32_34_menu_plus():
    d = banc("menu-plus")
    # point 34 : le menu de herdr-app, agents détectés, non installé grisé et inerte
    assert d["lignes"] == [["Terminal", "", False], ["Claude Code", "1", False], ["OpenCode", "2", False],
                           ["Codex", "not installed", True], ["New space", "", False]]
    assert d["titres"] == ["Dans sp", "Start an agent"] and d["choixSpaceEntete"] is False
    # I2 sur le menu : un refresh ne le referme pas, ne réinitialise pas le modèle, garde le focus
    assert d["entete"] == {"ouvert": True, "attache": True, "meme": True, "modele": "opus", "focus": True}
    assert d["lanceEntete"] == ["POST", "/api/spaces/sp1/open", {"kind": "claude", "model": "opus", "folder": "/home/user/t1"}]  # dossier de l'agent d'origine
    assert d["fermeApresLancement"] is True
    # sans agent ouvert : le space se choisit d'abord, et le choix survit aux refresh
    assert d["optionsSpace"] == ["sp", "autre"]
    assert d["accueil"] == {"ouvert": True, "attache": True, "valeur": "sp2", "meme": True, "focus": True}
    assert d["lanceAccueil"] == ["POST", "/api/spaces/sp2/open", {"kind": "opencode"}]
    assert d["codexNEnvoieRien"] is True
    assert d["liste"] == {"meme": True, "focus": True}
    assert d["plusParSpace"] == 0 and d["plusSpaces"] == 1  # point 32


def test_m6_ouvre_le_terminal_rendu_par_herdr_app():
    d = banc("nouvel-agent")
    assert d["post"] == ["POST", "/api/spaces/sp1/open", {"kind": "claude", "model": "opus"}]
    assert d["hash"] == "#/a/t9"  # t8 paraît aussi, avant : l'heuristique s'y trompait


def test_m1_reconnexion_fusionne_sans_saut_ni_repli():
    d = banc("fusion")
    assert d["memeDetails"] and d["ouvert"] and d["runOuvert"]
    assert d["scrollTop"] == 1000 and d["suivi"] is False and d["nouveaux"] == 2
    assert (d["agents"], d["moi"], d["outils"], d["texteC"]) == (2, 1, 2, True)  # rien en double


def test_point_33_confirmation_de_fermeture():
    d = banc("fermer")
    assert "maker-1" in d["texte"] and "herdr-web" in d["texte"] and "working" in d["texte"]
    assert d["rouge"] == "Il travaille encore : son tour en cours sera perdu."
    assert d["focusAnnuler"] is True
    assert d["apresAnnuler"] == {"voile": False, "posts": 0}  # l'annulation n'envoie rien
    assert d["clicDroitBloque"] is True
    assert d["erreur"] == "hc kill a échoué : terminal occupé" and d["toujoursLa"] is True
    assert d["post"] == ["POST", "/api/agents/t1/close", {"confirm": "t1"}]
    assert d["apresFermer"] == {"voile": False, "hash": "#/"}
    assert d["rougeRepos"] is False
    assert d["fermerSousAgent"] == 0 and d["fermerLigneSous"] == 0 and d["fermerCartes"] == 2


def test_renommer_un_onglet_depuis_spaces():
    d = banc("renommer")
    # crayon sur l'onglet à id connu, pas sur l'onglet sans id
    assert d["crayons"] == [["› build1", 1], ["› sans-id1", 0]]
    assert d["prerempli"] == "build" and d["focusChamp"] is True
    assert d["survitAuRefresh"] is True  # le refresh de 15 s ne détruit pas la modale
    assert d["echap"] is True and d["postsApresAnnuler"] == 0
    assert d["post"] == ["POST", "/api/tabs/tb1/rename", {"name": "neuf"}]
    assert d["fermee"] is True and d["releu"] is True
    assert d["postVide"] == ["POST", "/api/tabs/tb1/rename", {"name": ""}]  # vide : numéro
    assert "fenêtre fermée" in d["erreur"] and d["toujoursLa"] is True


def test_m5_m7_sous_agents_finis_masques_et_role_deduit():
    d = banc("divers")
    # point 39 : reviewer, feuille à trois lignes et coche, animée seulement en working, le >_ prime
    assert d["revueTravaille"] == "role revue anime"
    assert d["revueBloquee"] == "role revue" and d["revueRepos"] == "role revue"
    assert d["revueShell"] == "role shell anime"
    assert d["revueFormes"] == ["feuille", "ligne l1", "ligne l2", "ligne l3", "coche"]
    assert d["shellBloque"] == "role claude"  # m5 : >_ seulement pendant working
    assert d["shellTravaille"] == "role shell anime"
    assert d["shellRepos"] == "role terminal"  # spec 2026-11-15, point 8 : icône terminal de la table
    assert d["rel"] == "noopener noreferrer"  # m7
    assert d["sousVisibles"] == ["Implement herdr-web", "Check the diff", "Explore more",
                                 "Review the diff"]  # ni done ni idle
    # 35 et 39 ; sans rôle, la règle « Claude sans rôle » (spec 2026-11-15) : étoile qui tourne en working
    assert d["iconesSous"] == ["role marteau anime", "role loupe", "role claude anime", "role revue anime"]
    assert d["pastilles"] == ["role marteau anime", "role loupe", "role claude anime", "role revue anime"]


def test_tache_shell_de_fond_montree_sans_lien():
    """Un clic sur une tâche Bash de fond ouvrait un fil de sous-agent inexistant (404) : elle
    reste visible, avec l'icône >_, mais n'est plus un lien, ni dans le menu ni en pastille."""
    d = banc("tacheShell")
    assert d["menu"] == [["a", "#/a/t1/s/a1", "role revue anime"], ["span", None, "role shell anime"]]
    assert d["pastilles"] == [["a", "#/a/t1/s/a1", "role revue anime"], ["span", None, "role shell anime"]]


@pytest.mark.parametrize("scenario", ["saisie-cote", "nouvel-agent", "fusion", "divers"])
def test_temoin_la_page_d_avant_echoue(tmp_path, scenario):
    """Témoin : chaque scénario du fix round 1 rend un autre résultat sur la page d'avant (bcdd3d8)."""
    avant = page_d_avant("bcdd3d8", tmp_path)
    attendu = {"saisie-cote": lambda d: not d["meme"] and not d["focus"] and d.get("nomVisible") != "mon space",
               "nouvel-agent": lambda d: d["hash"] == "#/a/t8",
               "fusion": lambda d: d["scrollTop"] == 5000 and d["suivi"] is True and not d["memeDetails"],
               "divers": lambda d: d["shellBloque"] == "role shell" and d["rel"] == "noopener"
               and len(d["sousVisibles"]) == 6}
    assert attendu[scenario](banc(scenario, avant))


# -- point 36 : façon Discord --

@pytest.mark.parametrize("tz", ["Pacific/Auckland", "America/Los_Angeles", "UTC"])
def test_point_36_groupes_heure_de_paris_et_pilule(tz):
    d = banc("discord", tz=tz)
    assert d["fuseau"] == tz  # l'appareil n'est pas à Paris : l'heure doit l'être quand même
    g = d["groupes"]
    # deux messages consécutifs du même auteur = un seul en-tête ; les outils restent dans le groupe de l'agent
    assert [(x["classe"], x["messages"], x["outils"]) for x in g] == [
        ("groupe-msg de-user", ["q1", "q2"], 0),
        ("groupe-msg de-agent", ["r1", "r2"], 1),
        ("groupe-msg de-user", ["q3"], 0),
        ("groupe-msg de-agent", ["r3"], 0)]
    assert all(x["avatar"] == 0 for x in g)  # point 37 : plus d'avatar à l'initiale
    # « nom · HH:MM » en Europe/Paris : 13:05Z en juillet = 15:05 (CEST), 23:30Z en janvier = 00:30 (CET)
    assert [x["tete"] for x in g] == ["Vous· 15:05", "maker-1· 15:07", "Vous· 00:30", "maker-1"]
    assert g[1]["auteur"] == "auteur ia opus"  # couleur du modèle de l'agent
    # [envoyer caché, micro caché] : la flèche n'existe qu'avec du texte
    assert d["vide"] == [True, False]
    assert d["texte"] == [False, True]
    assert d["blancs"] == [True, False]
    assert d["apresEnvoi"] == [True, False]
    # point 37 : plus de bouton ↓ flottant, le « Live ↓ » suffit ; plus d'horloge dans la barre
    assert d["boutonBas"] == 0 and d["boutonBasRemonte"] == 0 and d["liveRemonte"].startswith("Live ↓")
    # la ligne des modèles reste même vide : sinon la barre perd une ligne et la liste remonte
    assert d["barre"] == ["seg cpu", "seg mem", "seg quota", "seg modeles"]
    # aucun script de quota sur le serveur (quota null) : pas de segment quota, pas de tiret orphelin
    assert d["barreSansQuota"] == ["seg cpu", "seg mem", "seg modeles"]


def test_temoin_point_36_la_page_d_avant_echoue(tmp_path):
    """Témoin : sur la page d'avant le point 36 (52323be), le scénario échoue ou rend autre chose."""
    avant = page_d_avant("52323be", tmp_path)
    d = banc("discord", avant, tz="UTC")
    assert not any(x["classe"].startswith("groupe-msg") for x in d["groupes"])  # pas de groupes
    assert d["vide"] is None  # pas de flèche gérée


# -- vague finale, point 38 : pincer pour la taille du texte du fil --

def test_point_38_pincement_bornes_memoire_et_defaut():
    d = banc("pincement")
    assert d["depart"] == "15px" and d["stockeAvant"] is None
    assert d["ecarte"][:2] == ["26px", "26"]  # borne haute, mémorisée
    assert d["serre"] == ["11px", "11"]  # borne basse
    assert d["demi"] == "14.5px"  # pas continu, arrondi au demi-pixel
    assert d["tremble"] == "14.5px" and d["souris"] == "14.5px"
    assert d["rouvert"] == "14.5px"  # mémorisée par appareil
    assert d["doubleTape"] == ["15px", False]  # double tape à deux doigts : défaut, mémoire effacée
    assert d["fantome"] == "15px"  # rattrapage : un pointerup perdu ne fait pas d'un doigt un pincement
    assert d["annule"] == "15px"  # pointercancel purge : le doigt restant ne redimensionne pas
    assert d["molette"] == ["15px", False]  # molette sans Ctrl : le défilement normal
    assert d["ctrlMolette"][1] is True and d["ctrlMolette"][0] != "15px"  # petits pas cumulés
    assert d["ctrlMolette"][2] == d["ctrlMolette"][0][:-2]
    assert d["suiviGarde"] == [5000, True]  # Live : reste en bas
    assert d["termDepart"] and d["termPince"][0] != d["termDepart"] and d["termPince"][1] is True
    assert d["termGarde"] == d["termPince"][0]  # l'ajustement automatique ne l'écrase pas
    assert d["termRouvert"] == d["termDepart"]  # jusqu'à réouverture


def test_temoin_point_38_la_page_d_avant_ne_pince_pas(tmp_path):
    avant = page_d_avant("dc81bcf", tmp_path)
    d = banc("pincement", avant)
    assert d["depart"] == "" and d["ecarte"][0] == ""  # aucune taille posée, aucun pincement


def test_message_en_attente_grise_puis_retire():
    """L'utilisateur : « c'est grisé lorsque tu travailles car pas encore envoyé, il faut reproduire ce
    comportement ». Mise en file : grisé sous le fil, sans doublon ; livré ou retiré : il part ;
    agent au repos : la file se vide."""
    d = banc("attente")
    assert d["misEnFile"] == {"textes": ["un", "deux"], "cache": False, "grises": 2, "fil": 0}
    assert d["livre"] == {"textes": ["deux"], "cache": False, "grises": 1, "fil": 1}
    assert d["retire"] == {"textes": [], "cache": True, "grises": 0, "fil": 1}
    # un `/cmd` en file arrive en item `commande` (pas `moi`) : il doit quitter l'attente lui aussi
    assert d["cmdEnFile"]["textes"] == ["/model opus"]
    assert d["cmdPris"]["textes"] == [] and d["cmdPris"]["cache"] is True
    assert d["repos"]["textes"] == [] and d["repos"]["cache"] is True


# -- agents hc enfants rangés sous leur parent --

def _noms(blocs):
    return [(b["nom"], _noms(b["enfants"])) for b in blocs]


def test_enfants_hc_sous_leur_parent_et_famille_dans_le_groupe_le_plus_urgent():
    d = banc("enfants")
    g = {x["titre"]: _noms(x["blocs"]) for x in d["groupes"]}
    # enfant bloqué : sa famille (q, r) remonte en « Needs you » alors que q est idle
    assert g["Needs you1"] == [("q", [("r", [])])]
    # p travaille : ses enfants (done, idle) n'ont PAS de ligne dans « Done » / « Idle »
    assert g["Working1"] == [("p", [("c1", [("g", [])]), ("c2", [])])]
    # parent absent (fermé) : l'enfant reste dans son groupe d'état, comme avant
    assert g["Done2"] == [("o", []), ("z", [])]
    assert d["total"] == 10 and d["compte"] == "10 terminaux"  # chaque agent une fois, compte inchangé


def test_enfants_hc_cycle_a_b_a_ne_perd_ni_ne_boucle():
    d = banc("enfants")
    g = {x["titre"]: _noms(x["blocs"]) for x in d["groupes"]}
    assert g["Idle2"] == [("ca", []), ("cb", [])]  # les deux restent à la racine, chacun une fois


def test_enfants_hc_ligne_trait_lien_fermeture_et_actif():
    d = banc("enfants")
    assert d["dernierC2"] is True and d["dernierC1"] is False  # └ pour le dernier, ├ sinon
    assert d["hrefG"] == "#/a/g"  # un vrai terminal : il s'ouvre comme un agent
    assert d["msgR"] == "ok ?"
    assert d["croix"] == 1 and d["fermeture"] == "g"  # même fermeture que les cartes
    assert d["actif"] == ["g"]


def test_profondeur_illimitee_toute_la_chaine_sous_sa_racine():
    d = banc("profondeur")
    # spec 2026-11-15, point 15 : arbre à profondeur illimitée (la garde anti-cycle reste : test
    # du cycle a -> b -> a ci-dessus) ; chaîne de 7 : une racine et 6 niveaux, aucun perdu
    assert d["total"] == 7
    assert d["racines"] == ["n0"]
    assert d["niveaux"] == 6


AVANT_ENFANTS = "a77ad9d4544837cc85ee9c352508302477114637"


@pytest.mark.parametrize("scenario", ["enfants", "profondeur"])
def test_temoin_la_page_d_avant_range_tout_a_plat(tmp_path, scenario):
    # page d'AVANT les enfants hc, figée sur le dernier main qui ne les avait pas (PR #11) : écrit
    # contre « main », le témoin passait en worktree et rougissait une fois la PR #12 mergée
    avant = page_d_avant(AVANT_ENFANTS, tmp_path)
    d = banc(scenario, avant)
    if scenario == "enfants":
        assert all(not b["enfants"] for x in d["groupes"] for b in x["blocs"])
    else:
        assert len(d["racines"]) == 7


def test_image_collee_dans_claude_code_montree():
    """Réserve de la revue : une image collée dans Claude Code (bloc base64 du journal) ne se
    voyait pas dans le fil ; seule une empreinte hexadécimale fait une image, jamais une URL."""
    d = banc("image-collee")
    assert d["srcs"] == ["/api/collee/" + "ab" * 32]
    assert "[Image #1] vois" in d["textes"]


def test_plusieurs_photos_toutes_montrees():
    """Plusieurs photos envoyées du téléphone : seule la 1re apparaissait dans le fil (2026-11-17)."""
    q = "/api/image?p="
    assert banc("photos-multiples")["srcs"] == [q + "%2Ff%2Fa.png", q + "%2Ff%2Fb.png", q + "%2Ff%2Fe.png"]


def test_panneau_btw_se_ferme_par_un_bouton_ou_echap():
    """/btw dans herdr-web (2026-11-17) : la carte proposait la liste de la réponse comme choix,
    et Échap ne passait pas (agent `blocked`)."""
    d = banc("btw-panneau")
    assert d["boutons"] == ["Fermer le panneau (Échap)"] and d["cachee"] is False
    assert d["clic"] == ["esc"] and d["cacheeApresClic"] is True
    assert d["echapPrevenu"] is True and d["echap"] == ["esc"]
    assert d["dialogueEchap"] == [False, []]


def test_appui_long_ni_tap_court_rafraichi_ni_tremblement():
    """Réserve de la revue UI v2 : un tap court sur une carte rafraîchie pendant l'appui ouvrait
    la confirmation ; le moindre tremblement annulait l'appui long."""
    d = banc("appui-long")
    assert d["remplacee"] is True  # le scénario rafraîchit bien la carte
    assert d["tapCourt"] is False
    assert d["tremblement"] is True
    assert d["defilement"] is False


def test_boite_de_fermeture_suit_l_etat():
    """Réserve de la revue UI v2 : l'état de la boîte était figé à son ouverture."""
    d = banc("boite-etat")
    assert d["repos"] is None
    assert d["travaille"] == "Il travaille encore : son tour en cours sera perdu."
    assert d["ouverte"] is True
    assert d["partie"] is False  # fermé ailleurs : la boîte se ferme


def test_un_temoin_sans_sa_page_d_avant_echoue_au_lieu_de_sauter(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="absente de ce clone"):
        page_d_avant("0" * 40, tmp_path)


def test_askuser_plusieurs_questions_chaque_question_puis_submit():
    """Capture de l'utilisateur du 2026-11-15 : AskUserQuestion à 4 questions, la page ne proposait que la
    1re. L'agent reste « blocked » d'une question à l'autre (aucun événement) : la page relit
    l'écran après sa réponse, sans remontrer la question répondue tant que l'écran n'a pas tourné."""
    d = banc("askuser-multi")
    assert d["q1"] == ["1  Rouge", "2  Vert"] and d["contexte1"]
    assert d["pendant"] == []  # l'écran d'avant la touche : rien de cliquable
    assert d["q2"] == ["1  Petit", "2  Grand"] and d["contexte2"]
    assert d["submit"] == ["1  Submit answers", "2  Cancel"]
    assert d["fin"] == [] and not d["finNote"]
    assert d["posts"] == ["1", "1", "1"]


def test_relecture_bornee_meme_quand_choices_echoue():
    """Réserve D1 de la PR #22 : /choices en 502 après la réponse ; la relecture s'arrête au
    plafond (10) au lieu d'un GET toutes les 300 ms tant que le core est indisponible."""
    d = banc("suite-erreur")
    assert d["lectures"] <= 10, d
    assert not d["suiteVivante"] and d["minuteurs"] == 0, d


def test_clic_sur_une_question_repondue_dans_le_terminal_reaffiche_l_ecran_reel():
    """Réserve D2 de la PR #22 : terminal et page côte à côte, Q1 répondue dans le terminal ; la
    page envoie ce qu'elle montre, le serveur refuse (409 perime), la carte de Q2 s'affiche."""
    d = banc("suite-perime")
    assert d["q1"] == ["1  Rouge", "2  Vert"]
    assert d["corps"] == [{"digit": "1", "msg": "m", "contexte": "←  ☐ Couleur  ☐ Taille  →\nQuelle couleur ?",
                           "libelles": ["Rouge", "Vert"]}]
    assert d["acceptes"] == []  # rien n'est parti vers Q2
    assert d["apres"] == ["1  Petit", "2  Grand"] and d["contexteApres"]


def test_meme_permission_redemandee_apres_un_tour_s_affiche_tout_de_suite():
    """M5 de la revue de la PR #22 : sortir de « blocked » oublie l'attente ; sinon la même
    permission redemandée reste cachée jusqu'au plafond de relecture (3 s)."""
    d = banc("suite-etat")
    assert d["visible"] and d["boutons"] == ["1  Yes", "2  No"], d


def test_relecture_arretee_quand_l_agent_n_est_plus_bloque_sans_rafraichir_la_vue():
    """M6 de la revue de la PR #22 : l'état connu passe « working » sans majVueAgent ; la relecture
    s'arrête sans relire /choices ni remontrer une carte pendant que l'agent travaille."""
    d = banc("suite-garde")
    assert d == {"lectures": 0, "carteVisible": False, "suiteVivante": False}, d


def test_apres_un_menu_a_curseur_la_question_suivante_s_affiche():
    """M7 de la revue de la PR #22 : écran Submit rendu en menu à curseur ; après la réponse au
    menu, la page relit l'écran comme après un chiffre."""
    d = banc("suite-menu")
    assert d["menu"] == ["❯ Submit answers", "Cancel"]
    assert d["pendant"] == []
    assert d["suivante"] == ["1  Oui", "2  Non"], d


def test_logo_os_rendu_de_la_page():
    """logoOs exécuté (faux DOM) : le fichier suit la clé `os`, glfos garde son PNG, rien de lisible ou
    clé hostile → ordinateur générique, WSL → distribution + badge Windows."""
    d = banc("logosOs")
    img = lambda cle, cls="os-logo": ["img", cls, f"/static/os/{cle}.svg"]
    assert d["a1"] == [img("ubuntu")], d
    assert d["a2"] == [["img", "os-logo os-glfos", "/static/os-logo.png"]], d
    assert d["a3"] == [img("generic")] and d["a4"] == [img("generic")], d
    assert d["a6"] == [img("generic")], d  # une clé qui n'est pas [a-z]* ne devient jamais un chemin
    assert d["a7"] == [img("quantum")] and d["a8"] == [img("linux")], d
    assert d["a5"] == [["span", "os-wsl", None, [img("debian"), ["img", "os-badge", "/static/os/windows.svg"]]]], d


def test_type_something_ouvre_un_champ_et_le_texte_part_avec_le_chiffre():
    """Brief appairage (2026-11-15) : cliquer « Type something. » envoyait le chiffre seul — le TUI
    pose alors le curseur sur son champ, rien n'est répondu et rien ne permettait d'écrire. La page
    ouvre un champ ; la réponse saisie part avec le chiffre ; la question suivante vient ensuite."""
    d = banc("saisie-libre")
    assert d["q1"] == ["1  Rouge", "2  Vert", "3  Type something."]
    assert d["postsApresClic"] == 0  # rien ne part avant le texte
    assert d["champ"] and d["focus"]
    assert d["texteGarde"] == "Bleu canard, mat"  # carte reconstruite pendant la saisie
    assert d["corps"] == [{"digit": "3", "msg": "Claude needs your input",
                           "contexte": "←  ☐ Couleur  ☐ Taille  ✔ Submit  →\nQuelle couleur ?",
                           "libelles": ["Rouge", "Vert", "Type something."], "texte": "Bleu canard, mat"}]
    assert d["cacheeApres"]
    assert d["q2"] == ["1  Petit", "2  Grand", "3  Type something."] and d["contexte2"]
    assert not d["champQ2"]
    assert [c["digit"] for c in d["corpsQ2"]] == ["1"] and "texte" not in d["corpsQ2"][0]


def test_temoin_type_something_la_page_d_avant_repondait_le_chiffre_seul(tmp_path):
    """Le scénario mord : sur la page de main (80c4d6d), le clic poste le chiffre sans texte."""
    d = banc("saisie-libre", app=page_d_avant("80c4d6d", tmp_path))
    assert d["postsApresClic"] == 1 and not d["champ"]
    assert d["corps"][0] == {"digit": "3", "msg": "Claude needs your input",
                             "contexte": "←  ☐ Couleur  ☐ Taille  ✔ Submit  →\nQuelle couleur ?",
                             "libelles": ["Rouge", "Vert", "Type something."]}


def test_type_something_d_une_question_a_cases():
    """Complément du brief (mesure sur un vrai TUI, 2026-11-15) : en multiSelect la ligne s'appelle
    « [ ] Type something » (sans point) ; elle ouvre le champ, et la case cochée avant reste cochée."""
    d = banc("saisie-libre-cases")
    assert d["avant"] == ["1  [ ] Python", "2  [ ] Rust", "3  [ ] Type something"]
    assert d["python"] == ["1  [✔] Python", "2  [ ] Rust", "3  [ ] Type something"]
    assert d["champ"]
    assert d["corps"] == [{"digit": "1"}, {"digit": "3", "texte": "Zig"}]
    assert d["apres"] == ["1  [✔] Python", "2  [ ] Rust", "3  [✔] Zig"]


def test_temoin_cases_la_page_d_avant_postait_le_chiffre_seul(tmp_path):
    """Le scénario mord : sur la page de d7de31e (choix unique seulement), la ligne à case partait
    en chiffre seul, sans champ."""
    d = banc("saisie-libre-cases", app=page_d_avant("d7de31e", tmp_path))
    assert not d["champ"]
    assert d["corps"] == [{"digit": "1"}, {"digit": "3"}]


def test_question_a_apercu_boutons_propres_et_apercus_montres():
    """Capture de l'utilisateur (2026-11-15) : l'aperçu n'était montré nulle part. Il l'est sous chaque option
    (téléphone) et dans un panneau qui suit le survol ou le focus (pointeur), en bloc préformaté."""
    d = banc("apercu")
    assert d["boutons"] == ["1  OngletsRubriques à gauche", "2  CartesUne colonne"]
    assert d["apercus"] == ["+-----+\n| > V |\n+-----+", "+---+\n| C |\n+---+"]
    assert d["panneau"] == d["apercus"][0] and d["survol"] == d["apercus"][1] and d["focus"] == d["apercus"][0]
    assert d["ordre"] == ["div.question", "div.contexte", "button", "pre.apercu", "button", "pre.apercu", "pre.apercu-panneau"]
    assert d["fautes"] == []
    assert d["corps"] == [{"digit": "2", "msg": "m", "contexte": "Quelle mise en page ?", "libelles": ["Onglets", "Cartes"]}]


def test_temoin_apercu_la_page_d_avant_ne_montrait_pas_l_apercu(tmp_path):
    d = banc("apercu", app=page_d_avant("4937470", tmp_path))
    assert d["apercus"] == [] and d["panneau"] is None


def test_css_apercu_preformate_defilant():
    css = (STATIC / "style.css").read_text()
    regle = css.split("#choix pre.apercu, #choix pre.apercu-panneau {")[1].split("}")[0]
    assert "white-space: pre" in regle and "overflow-x: auto" in regle and "var(--mono)" in regle
    survol = css.split("@media (hover: hover) and (pointer: fine) {\n  #choix pre.apercu { display: none; }")
    assert len(survol) == 2 and "#choix pre.apercu-panneau { display: block; }" in survol[1].split("}\n}")[0] + "}"


def test_reglages_connecter_un_appareil_qr_et_lien():
    """Brief appairage (2026-11-15) : la roue crantée crée un lien à usage unique (POST) et l'affiche
    en QR code (encodeur embarqué) et en texte ; 5 min plus tard, le lien affiché est retiré."""
    d = banc("appairage")
    assert d["bouton"] and d["avant"] == 0  # rien n'est créé à l'ouverture des réglages
    assert d["appels"] == ["POST"]
    assert d["qr"] and d["dessinDuLien"]
    assert d["lien"] == "https://herdr.example.org:10370/appairer/Ab-_9xYz0123456789abcdefghijKLMN"
    assert d["note"] and d["fautes"] == []
    assert d["expire"] == "Lien expiré : en créer un nouveau." and not d["qrApres"]
    assert d["apres401"] == ""


# -- suggestion grisée de Claude Code (2026-11-15) --

def test_puce_et_live_une_ligne_css():
    """Puce à gauche (tronquée en ellipsis avant de pousser Live), Live collé à droite, même hauteur :
    sans suggestion la ligne garde Live seul à droite, donc aucun saut quand la puce (dés)apparaît."""
    css = (STATIC / "style.css").read_text()
    barre = css.split(".barre-live {", 1)[1].split("}", 1)[0]
    puce = css.split(".puce-suggestion {", 1)[1].split("}", 1)[0]
    assert "display: flex" in barre and "align-items: center" in barre
    assert ".barre-live .live { margin-left: auto; flex: none; }" in css  # Live à droite, jamais écrasé
    assert "min-width: 0" in puce and "text-overflow: ellipsis" in puce and "flex: 0 1 auto" in puce
    assert "margin: 0;" in puce and "align-self" not in puce  # pas de marge propre : même hauteur que Live
    assert "font: .78rem var(--mono)" in puce  # même police que .live => même hauteur de ligne

def test_suggestion_grisee_placeholder_puce_tab_fleche():
    d = banc("suggestion")
    assert d["lecturesAvant"] == 0 and not d["avantLecture"]["puce"]  # lue après la fin du tour, pas avant
    assert d["lue"] == {"placeholder": "fais le test", "grise": True, "puce": True}
    assert d["tab"] is True and d["apresTab"]["valeur"] == "fais le test" and not d["apresTab"]["puce"]
    assert d["videDeNouveau"]["placeholder"] == "fais le test" and d["videDeNouveau"]["puce"]
    assert d["fleche"] is True and d["apresFleche"] == "fais le test"
    assert d["tabSurBrouillon"] is False and d["maj"] is False  # brouillon, Maj+Tab : rien d'intercepté
    assert d["apresPuce"]["valeur"] == "fais le test" and d["envois"] == 0  # recopiée, jamais envoyée
    # une seule ligne : la puce est dans la barre de « Live », à sa gauche (plus de ligne à part)
    assert d["ligne"] == {"memeConteneur": True, "classe": "barre-live", "ordre": True}
    assert d["relectures"] == 2
    assert d["disparue"]["placeholder"] == "Message…" and not d["disparue"]["puce"]
    assert d["changee"] == "autre"
    assert d["repart"] == {"valeur": "", "placeholder": "Message…", "grise": False, "puce": False}
    assert d["lecturesPendantTravail"] == 0
    assert d["reponsePerimee"]["placeholder"] == "Message…"  # réponse d'un autre tour ignorée
    assert d["nouveauTour"] == "neuve"
    # début de la suggestion tapé (ajout de l'utilisateur) : → et Tab complètent si le curseur est en fin
    assert d["prefixe"] == {"valeur": "lance", "placeholder": "Message…", "grise": False, "puce": True,
                            "puceTexte": "↹ lance le checker"}
    assert d["prefixeFleche"] is True and d["prefixeFlecheValeur"] == "lance le checker"
    assert d["prefixeMilieu"] == [False, "lance"]  # curseur au milieu : → garde son rôle
    assert d["prefixeTab"] == [True, "lance le checker"]
    assert d["prefixePuce"] == "lance le checker"
    assert d["horsPrefixe"] == [None, False, "lancer"]
    assert d["puceVide"] == "↹ suggestion"
    # vignettes : le champ est leur commentaire, ni puce ni Tab volé
    assert d["vignettes"] == {"valeur": "", "placeholder": "Ajouter un commentaire…", "grise": False,
                              "puce": False, "tab": False}
    assert d["lecturesCache"] == 0 and d["relanceCache"] >= 1  # onglet caché : rien lu, relecture plus tard
    assert d["apresEnvoi"] == {"valeur": "", "placeholder": "Message…", "grise": False, "puce": False}
    assert d["avantTactile"] is True and d["tabTactile"] is False
    assert d["tour0"]["lectures"] == 0 and d["tour0"]["placeholder"] == "Message…"
    assert d["fautes"] == []


@pytest.mark.parametrize("avant,apres,cle,mord", [
    # sans la garde du tour 0, le placeholder « Try … » d'un Claude neuf serait lu
    ('["idle", "done"].includes(a.state) && a.turn > 0', '["idle", "done"].includes(a.state)',
     "tour0", lambda x: x["lectures"] > 0),
    # sans la garde de tour sur la réponse, une suggestion périmée s'afficherait
    ('&& r && cle.endsWith(":" + r.turn)) poserSuggestion', '&& r) poserSuggestion',
     "reponsePerimee", lambda x: x["placeholder"] == "vieille"),
    # sans la garde tactile, Tab serait volé au clavier du téléphone
    ('|| ev.metaKey || TACTILE()) return;', '|| ev.metaKey) return;', "tabTactile", lambda x: x is True),
    # sans la garde du champ vide, Tab écraserait un brouillon
    ('texte(v.champ.value) === "" && ', '', "tabSurBrouillon", lambda x: x is True),
    # onglet caché : sans la garde, la page lirait l'écran pour rien
    ("if (!document.hidden) {\n    try {\n      const r = await api(\"GET\", urlAgent(v.addr, \"suggestion\"));",
     "if (true) {\n    try {\n      const r = await api(\"GET\", urlAgent(v.addr, \"suggestion\"));",
     "lecturesCache", lambda x: x > 0),
    # envoi : sans l'effacement, l'ancienne suggestion revient en placeholder
    ("v.sugg = null; // l'agent repart", "// l'agent repart", "apresEnvoi", lambda x: x["puce"]),
    # vignettes : sans la garde, la puce s'afficherait sur leur commentaire
    (" && !(v.pieces && v.pieces.length)", "", "vignettes", lambda x: x["puce"] or x["tab"]),
    # curseur au milieu d'un début de suggestion : sans la garde, → serait volé
    ("(suggestionPrefixe(v) && curseurEnFin(v.champ))", "suggestionPrefixe(v)", "prefixeMilieu",
     lambda x: x[0] is True),
    # sans le cas préfixe, → ne complète pas « lance »
    ('return !!(v.sugg && t !== "" && t !== v.sugg', 'return !!(false && v.sugg && t !== "" && t !== v.sugg',
     "prefixeFleche", lambda x: x is False),
])
def test_temoins_suggestion_le_banc_mord(tmp_path, avant, apres, cle, mord):
    src = (STATIC / "app.js").read_text()
    assert src.count(avant) >= 1, avant
    mute = tmp_path / "app.js"
    mute.write_text(src.replace(avant, apres))
    assert mord(banc("suggestion", mute)[cle])


def test_hc_jamais_attribue_a_l_utilisateur_et_nom_affiche():
    d = banc("hc-attribution")
    for k in ("defaut", "nomme"):
        assert len(d[k]["hc"]) == 3 and all(h["gauche"] for h in d[k]["hc"])
        assert d[k]["bullesMoi"] == ["bonjour"]  # seule la vraie bulle reste à l'utilisateur
        assert d[k]["hc"][0]["lien"].endswith("#/a/t4s") and d[k]["hc"][1]["lien"] is None
        assert d[k]["auteurs"][1:] == ["design-reglages", "parti", "herdr"]
    assert d["defaut"]["auteurs"][0] == "Vous" and d["nomme"]["auteurs"][0] == "Marie Curie"
    assert d["vide"] == "Marie Curie"  # un nom vide ne remplace pas le dernier nom connu
