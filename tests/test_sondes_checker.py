"""Sondes du checker (PR #27) : chaque test affirme le comportement ATTENDU ; rouge = défaut."""
import json

from test_server import FakeHc, cert, req, srv  # noqa: F401
from herdr_web import choices as ch

PERMISSION_AVEC_TRANSCRIPT = """● J'ai relu choices.py : la ligne d'aide du dialogue à aperçu dit « n to add notes ».

╭──────────────────────────────────────────────────────────╮
 Bash command

   touch fichier-oui.txt
   Crée le fichier fichier-oui.txt

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and always allow access to fichier-oui.txt from this project
   3. No

 Esc to cancel · Tab to amend
"""


def test_P1_permission_pas_d_entree_meme_si_le_transcript_cite_n_to_add_notes(srv):
    srv.hc_fake.screen = PERMISSION_AVEC_TRANSCRIPT
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m"})
    assert r.status == 200, b
    keys = [c[1]["keys"] for c in srv.hc_fake.calls if c[0] == "terminal.keys"]
    assert keys == [["1"]], keys


class HcQuiTourne(FakeHc):
    """Après le chiffre, l'écran a tourné (réponse donnée au terminal, dialogue suivant)."""
    def request(self, method, params, timeout=100.0):
        r = super().request(method, params, timeout)
        if method == "terminal.keys":
            self.status = {"state": "blocked", "msg": "Do you want to proceed?"}
            self.screen = PERMISSION_AVEC_TRANSCRIPT
        return r


def test_P2_envoi_force_reverifie_l_ecran_apres_le_chiffre():
    hc = HcQuiTourne()
    hc.status = {"state": "blocked", "msg": "m"}
    try:
        ch.answer_texte(hc, "t1", "3", "m", "Zig")
    except ch.ChoiceStale:
        pass
    envoyes = [c for c in hc.calls if c[0] == "terminal.send"]
    assert envoyes == [], "texte collé + Entrée forcés dans un AUTRE dialogue"


ECRAN_Q2_MEME_TEXTE = """────────────────────────────────────────────────────────────────
←  ☒ Front  ☐ Back  ✔ Submit  →

Quel langage ?

❯ 1. Go
  2. Rust
  3. Type something.
────────────────────────────────────────────────────────────────
  4. Chat about this
"""


def test_P3_deux_questions_au_meme_texte_libelles_de_la_bonne():
    qs = [{"question": "Quel langage ?", "multiSelect": False,
           "options": [{"label": "TypeScript"}, {"label": "Elm"}]},
          {"question": "Quel langage ?", "multiSelect": False,
           "options": [{"label": "Go"}, {"label": "Rust"}]}]
    cs = ch.parse_choices(ECRAN_Q2_MEME_TEXTE)
    q = ch.question_affichee(qs, ECRAN_Q2_MEME_TEXTE)
    st = ch.choix_structures(q, cs) if q else None
    libs = [c.label for c in (st or cs)]
    assert libs[:2] == ["Go", "Rust"], libs


def test_P4_question_a_une_option():
    ecran = """────────────────────────────────────────────────
Seule ?

❯ 1. Oui
  2. Type something.
────────────────────────────────────────────────
  3. Chat about this
"""
    qs = [{"question": "Seule ?", "multiSelect": False, "options": [{"label": "Oui", "description": "d"}]}]
    st = ch.choix_structures(ch.question_affichee(qs, ecran), ch.parse_choices(ecran))
    assert [(c.digit, c.label) for c in st] == [("1", "Oui"), ("2", "Type something.")]
