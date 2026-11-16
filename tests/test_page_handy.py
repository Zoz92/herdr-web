"""Rubrique Voix, sous le banc node : suggestion de Handy quand il manque, lien discret quand il est là,
import de sa configuration, agent d'installation lancé seulement après confirmation (brief handy-reglages)."""
import pytest

from test_page import banc

DEPOT = "https://github.com/cjpais/Handy"


@pytest.fixture(scope="module")
def h():
    return banc("handy-voix")


def test_present_lien_discret_seulement(h):
    p = h["present"]
    assert p["suggestion"] is False and p["installer"] is False
    assert p["liens"] == [["site du projet", DEPOT, "_blank", "noopener noreferrer"]]
    assert h["fautesPresent"] == []


@pytest.mark.parametrize("cas", ["absent", "ancien"])
def test_absent_suggestion_deux_lignes_et_depot(h, cas):
    a = h[cas]
    assert a["suggestion"] is True and a["installer"] is True
    assert a["lignes"][:2] == ["Transcription vocale locale : la voix ne quitte pas cet ordinateur.",
                               "Libre et open source. Le micro de herdr-web en a besoin."]
    assert [x[1] for x in a["liens"]] == [DEPOT]  # le dépôt officiel, ouvert à part, sans référent
    assert a["liens"][0][2:] == ["_blank", "noopener noreferrer"]
    assert h["fautes" + cas.capitalize()] == []


def test_installer_confirmation_avant_tout_lancement(h):
    assert h["avantConfirmation"] == []  # le bouton seul ne lance rien
    assert h["question"] == ["Un agent va installer un logiciel sur cet ordinateur.", "alertdialog"]
    assert h["focus"] == "Annuler"
    assert h["apresAnnuler"] == [0, False, True]  # Annuler : aucun appel, retour au bouton


def test_installer_lance_claude_par_la_route_du_menu_plus_puis_ouvre_l_agent(h):
    # le space dont le dossier est le dossier personnel, une tâche de liste blanche, jamais un texte libre
    assert h["lancement"] == [["POST", "/api/spaces/sp0/open",
                               {"kind": "claude", "model": "default", "tache": "installer-handy"}]]
    assert h["fermeApresLancement"] is True and h["hash"] == "#/a/t9"


def test_import_puis_modification_depuis_la_page(h):
    assert h["importAppel"] == [["POST", "/api/reglages/voix/import", {}]]
    assert h["champsApresImport"] == ["parakeet-tdt-0.6b-v3", "fr", "herdr\nAlex"]
    assert h["msgImport"] == "configuration de Handy importée"
    assert h["maj"] == [{"voix": {"modele": "whisper-small", "langue": "fr", "vocabulaire": ["herdr", "Alex"]}}]


def test_langue_et_vocabulaire_dits_de_reference(h):
    assert h["present"]["reference"] is True and h["absent"]["reference"] is True


# -- réserves du checker (brief handy-reglages-2) --

def test_r3_double_appui_un_seul_agent(h):
    assert len(h["lancement"]) == 1  # les deux appuis du banc : le second tombe sur un bouton désactivé


def test_r4_ecart_avec_le_modele_de_handy(h):
    assert h["ecartAvant"] == ""  # rien de forcé : herdr-web suit Handy
    assert h["ecartApresImport"] == ("Écart : Handy utilise « Qwen3 », herdr-web demande « parakeet-tdt-0.6b-v3 ». "
                                     "Vider le champ pour suivre Handy.")
    assert h["ecartApresDiffusion"] == ""


def test_r4_la_page_dit_le_repli_de_modele(h):
    assert h["statutVocal"] == "modèle « x » refusé par Handy : transcrit avec le modèle choisi dans Handy"


def test_r5_voix_diffusee_par_un_autre_appareil(h):
    assert h["champsApresDiffusion"] == ["Qwen3", "en", "a"]
