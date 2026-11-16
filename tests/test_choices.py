from pathlib import Path

import pytest

from herdr_web.choices import Choice, ChoiceStale, answer, parse_choices

FIX = Path(__file__).parent / "fixtures"


def screen(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_permission():
    choices = parse_choices(screen("ecran_permission.txt"))
    assert [c.digit for c in choices] == ["1", "2", "3", "4"]
    assert choices[0] == Choice("1", "Yes", "")
    assert choices[3] == Choice("4", "No", "")


def test_parse_question_avec_details():
    choices = parse_choices(screen("ecran_question.txt"))
    assert len(choices) == 5
    assert choices[1].label == "Vert"
    assert choices[1].detail == "Vert"
    assert choices[4].detail == ""


def test_parse_sans_choix_rend_vide():
    assert parse_choices("rien ici\nni là\n") == []
    assert parse_choices("") == []


class FakeHc:
    def __init__(self, state="blocked", msg="Do you want to proceed?", status=True):
        self.status = {"app": "claude-code", "state": state, "kind": None, "msg": msg}
        if not status:
            self.status = None
        self.calls = []

    def request(self, method, params=None, timeout=100.0):
        self.calls.append((method, params))
        if method == "terminal.get":
            return {"address": "t1", "state": "live", "status": self.status}
        return {}

    def keys_calls(self):
        return [c for c in self.calls if c[0] == "terminal.keys"]


def test_answer_envoie_une_touche():
    hc = FakeHc()
    answer(hc, "t1", "4", "Do you want to proceed?")
    assert hc.keys_calls() == [("terminal.keys", {"terminal": "t1", "keys": ["4"]})]


def test_answer_refuse_si_msg_change():
    hc = FakeHc(msg="Autre question")
    with pytest.raises(ChoiceStale):
        answer(hc, "t1", "1", "Do you want to proceed?")
    assert hc.keys_calls() == []


def test_answer_refuse_si_plus_bloque():
    for hc in (FakeHc(state="working"), FakeHc(status=False)):
        with pytest.raises(ChoiceStale):
            answer(hc, "t1", "1", "Do you want to proceed?")
        assert hc.keys_calls() == []


@pytest.mark.parametrize("digit", ["10", "a", "enter", "0", "", "१"])
def test_answer_refuse_digit_invalide(digit):
    hc = FakeHc()
    with pytest.raises(ValueError):
        answer(hc, "t1", digit, "Do you want to proceed?")
    assert hc.calls == []


# -- menus à curseur (dialogue de confiance de Claude Code, 2026-11-15) --

CONFIANCE = """ Accessing workspace:

 /home/user

 Quick safety check: Is this a project you created or one you trust?

 Security guide

 ❯ No, exit
   Yes, I trust this folder

 Enter to confirm · Esc to cancel
"""


def test_menu_a_curseur_lu():
    from herdr_web.choices import parse_menu
    assert parse_menu(CONFIANCE) == (["No, exit", "Yes, I trust this folder"], 0)
    assert parse_menu(CONFIANCE.replace(" ❯ No, exit\n   Yes,", "   No, exit\n ❯ Yes,")) == (
        ["No, exit", "Yes, I trust this folder"], 1)


@pytest.mark.parametrize("ecran", [
    "rien\n", "❯ seul\n", "  a\n  b\n",  # une option, ou aucun curseur
    "❯ 1. Yes\n  2. No\n",  # numéroté : relève de parse_choices
    "❯ a\n❯ b\n",  # deux curseurs : pas un menu
])
def test_pas_de_menu(ecran):
    from herdr_web.choices import parse_menu
    assert parse_menu(ecran) is None


class _Hc:
    def __init__(self):
        self.calls = []

    def request(self, m, p):
        self.calls.append((m, p))
        return {}


def test_reponse_au_menu_deplace_le_curseur_puis_entree():
    from herdr_web.choices import answer_menu
    hc = _Hc()
    answer_menu(hc, "t2t", 1, ["No, exit", "Yes, I trust this folder"], CONFIANCE)
    assert hc.calls == [("terminal.keys", {"terminal": "t2t", "keys": ["down", "enter"]})]
    hc = _Hc()
    answer_menu(hc, "t2t", 0, ["No, exit", "Yes, I trust this folder"], CONFIANCE)
    assert hc.calls == [("terminal.keys", {"terminal": "t2t", "keys": ["enter"]})]


def test_menu_change_rien_n_est_envoye():
    from herdr_web.choices import ChoiceStale, answer_menu
    hc = _Hc()
    with pytest.raises(ChoiceStale):
        answer_menu(hc, "t2t", 1, ["No, exit", "Autre chose"], CONFIANCE)
    with pytest.raises(ChoiceStale):
        answer_menu(hc, "t2t", 1, ["No, exit", "Yes, I trust this folder"], "écran parti\n")
    assert hc.calls == []


# -- AskUserQuestion à plusieurs questions (capture de l'utilisateur, 2026-11-15, écran transcrit) --

def test_multi_questions_choix_et_contexte():
    from herdr_web.choices import parse_contexte
    ecran = screen("ecran_askuser_multi.txt")
    cs = parse_choices(ecran)
    assert [(c.digit, c.label) for c in cs] == [
        ("1", "Garder la section (Recommandé)"), ("2", "Fondre dans les en-têtes"), ("3", "Type something.")]
    assert cs[0].detail.startswith("Section Spaces au-dessus")
    ctx = parse_contexte(ecran)
    assert ctx.splitlines() == [
        "←  ☒ Badge rôle  ☐ Spaces web  ☐ Fermer  ☐ Population  ✔ Submit  →",
        "Le web reçoit les modes de tri de herdr-app (Priority / Grouped by space). Que devient sa "
        "section Spaces (onglets, crayon de renommage, « + Nouveau space ») ?"]


def test_contexte_question_simple_et_permission():
    from herdr_web.choices import parse_contexte
    assert parse_contexte(screen("ecran_question.txt")).splitlines() == [
        "☐ Couleur", "Quelle est ta couleur préférée ?"]
    assert parse_contexte(screen("ecran_permission.txt")).splitlines() == [
        "Bash command", "touch fichier-oui.txt", "Crée le fichier fichier-oui.txt", "Do you want to proceed?"]
    assert parse_contexte("rien\n") == ""
