"""AskUserQuestion à aperçu (`preview`) — bug signalé par l'utilisateur le 2026-11-15 : la page collait des
morceaux de l'aperçu dans les boutons (« 2 Cartes sur une colonne │ ┌─ Réglages ──── ✕ ┐ ») et ne
montrait pas l'aperçu.

Écrans réels (tests/fixtures/ecran_apercu_*.txt) et appel réel (askuser_apercu.json) relevés sur un
agent d'essai Claude Code 2.1.325 : options à gauche, boîte d'aperçu à droite sur les mêmes lignes ;
un chiffre ne fait que déplacer le curseur (ecran_apercu_apres2 : Q1 toujours là), Entrée répond
(ecran_apercu_apresenter : Q2 affichée, « ☒ Mise en page »).
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import json
from pathlib import Path

import pytest

from test_server import cert, req, srv  # noqa: F401
from herdr_web import choices as ch
from herdr_web.chat import question_en_attente

FIX = Path(__file__).parent / "fixtures"
Q1, APRES2, Q2, APRES_ENTREE = (FIX / f"ecran_apercu_{n}.txt" for n in ("q1", "apres2", "q2", "apresenter"))
APPEL = json.loads((FIX / "askuser_apercu.json").read_text())
BOITE = set("│┌┐└┘├┤┬┴┼─╭╮╰╯+|")


def ecran(p):
    return p.read_text()


def journal(tmp_path, *lignes):
    f = tmp_path / "session.jsonl"
    f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lignes))
    return f


def ligne_appel():
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": APPEL["id"], "name": "AskUserQuestion", "input": APPEL["input"]}]}}


def ligne_resultat():
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": APPEL["id"], "content": "réponses"}]}}


# ---------- l'écran seul (repli sans journal) ----------

@pytest.mark.parametrize("f", [Q1, APRES2])
def test_ecran_a_apercu_libelles_propres(f):
    cs = ch.parse_choices(ecran(f))
    assert [(c.digit, c.label, c.detail) for c in cs] == [("1", "Onglets", ""), ("2", "Cartes", "")]
    assert not any(BOITE & set(c.label + c.detail) for c in cs)


def test_ecran_a_cases_descriptions_gardees():
    cs = ch.parse_choices(ecran(Q2))
    assert [(c.label, c.detail) for c in cs][:3] == [("[ ] Voix", "Module voix"), ("[ ] QR", "Module QR"),
                                                     ("[ ] Langue", "Module langue")]


def test_le_contexte_reste_celui_de_la_question():
    assert ch.parse_contexte(ecran(Q1)) == "←  ☐ Mise en page  ☐ Modules  ✔ Submit  →\nQuelle mise en page ?"
    assert ch.parse_contexte(ecran(APRES2)) == ch.parse_contexte(ecran(Q1))  # le curseur bouge, pas la question


@pytest.mark.parametrize("f,attendu", [(Q1, True), (APRES2, True), (Q2, False), (APRES_ENTREE, False),
                                       (FIX / "ecran_question.txt", False), (FIX / "ecran_permission.txt", False)])
def test_dialogue_a_apercu_reconnu(f, attendu):
    assert ch.est_apercu(ecran(f)) is attendu


# ---------- le journal ----------

def test_question_en_attente_lue_dans_le_journal(tmp_path):
    f = journal(tmp_path, {"type": "user", "message": {"content": "pose la question"}}, ligne_appel())
    qs = question_en_attente(f)
    assert [q["question"] for q in qs] == ["Quelle mise en page ?", "Quels modules ?"]
    assert [o["label"] for o in qs[0]["options"]] == ["Onglets", "Cartes"]
    assert qs[0]["options"][0]["preview"] == APPEL["input"]["questions"][0]["options"][0]["preview"]
    assert qs[1]["multiSelect"] is True and qs[1]["options"][0]["preview"] == ""


def test_question_repondue_n_est_plus_en_attente(tmp_path):
    assert question_en_attente(journal(tmp_path, ligne_appel(), ligne_resultat())) is None


def test_journal_sans_question_ou_illisible(tmp_path):
    assert question_en_attente(journal(tmp_path, {"type": "user", "message": {"content": "x"}})) is None
    f = tmp_path / "abime.jsonl"
    f.write_text("{pas du json\n" + json.dumps(ligne_appel()) + "\n")
    assert question_en_attente(f) is not None  # une ligne abîmée n'empêche pas la suivante
    assert question_en_attente(tmp_path / "absent.jsonl") is None


def test_journal_lu_par_la_fin_seulement(tmp_path):
    bourre = {"type": "user", "message": {"content": "x" * 5000}}
    f = journal(tmp_path, bourre, bourre, ligne_appel())
    assert question_en_attente(f, fenetre=len(json.dumps(ligne_appel())) + 100) is not None


def test_question_affichee_et_choix_structures():
    qs = APPEL["input"]["questions"]
    q = ch.question_affichee(qs, ecran(Q1))
    assert q["question"] == "Quelle mise en page ?"
    cs = ch.choix_structures(q, ch.parse_choices(ecran(Q1)))
    assert [(c.digit, c.label, c.detail) for c in cs] == [("1", "Onglets", "Rubriques à gauche"), ("2", "Cartes", "Une colonne")]
    assert cs[1].preview.startswith("+-----")
    q2 = ch.question_affichee(qs, ecran(APRES_ENTREE))
    cs2 = ch.choix_structures(q2, ch.parse_choices(ecran(APRES_ENTREE)))
    assert [c.label for c in cs2] == ["[ ] Voix", "[ ] QR", "[ ] Langue", "[ ] Type something"]


def test_question_du_journal_absente_de_l_ecran():
    autre = [{"question": "Autre chose ?", "options": [{"label": "a"}]}]
    assert ch.question_affichee(autre, ecran(Q1)) is None
    memes_options = [dict(APPEL["input"]["questions"][0], question="Une autre question ?")]
    assert ch.question_affichee(memes_options, ecran(Q1)) is None  # options égales, texte absent
    q = dict(APPEL["input"]["questions"][0], options=APPEL["input"]["questions"][0]["options"] * 2)  # 4 options, 2 à l'écran
    assert ch.choix_structures(q, ch.parse_choices(ecran(Q1))) is None


# ---------- les routes ----------

@pytest.fixture
def bloque(srv, tmp_path, monkeypatch):
    f = journal(tmp_path, ligne_appel())
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: f)
    srv.hc_fake.status = {"state": "blocked", "msg": "m"}
    return srv


def test_choices_libelles_descriptions_et_apercus_du_journal(bloque):
    bloque.hc_fake.screen = ecran(Q1)
    r, b = req(bloque, "GET", "/api/agents/t1/choices")
    d = json.loads(b)
    assert [(c["digit"], c["label"], c["detail"]) for c in d["choices"]] == [
        ("1", "Onglets", "Rubriques à gauche"), ("2", "Cartes", "Une colonne")]
    assert [c["preview"] for c in d["choices"]] == [o["preview"] for o in APPEL["input"]["questions"][0]["options"]]
    assert d["contexte"].endswith("Quelle mise en page ?")


def test_choices_sans_journal_repli_sur_l_ecran_nettoye(bloque, monkeypatch):
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: None)
    bloque.hc_fake.screen = ecran(Q1)
    d = json.loads(req(bloque, "GET", "/api/agents/t1/choices")[1])
    assert [(c["label"], c["preview"]) for c in d["choices"]] == [("Onglets", ""), ("Cartes", "")]


def test_answer_question_a_apercu_chiffre_puis_entree(bloque):
    bloque.hc_fake.screen = ecran(Q1)
    vue = {"contexte": ch.parse_contexte(ecran(Q1)), "libelles": ["Onglets", "Cartes"]}
    r, b = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m", **vue})
    assert r.status == 200, b
    assert [c for c in bloque.hc_fake.calls if c[0] == "terminal.keys"] == [
        ("terminal.keys", {"terminal": "t1", "keys": ["2", "enter"]})]


def test_answer_question_a_apercu_sans_journal_entree_d_apres_le_dialogue(bloque, monkeypatch):
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: None)
    bloque.hc_fake.screen = ecran(Q1)
    vue = {"contexte": ch.parse_contexte(ecran(Q1)), "libelles": ["Onglets", "Cartes"]}
    r, b = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", **vue})
    assert r.status == 200, b
    assert [c[1]["keys"] for c in bloque.hc_fake.calls if c[0] == "terminal.keys"] == [["1", "enter"]]


def test_answer_question_a_cases_chiffre_seul(bloque):
    """Hors aperçu, le chiffre coche ou répond seul : pas d'Entrée en plus."""
    bloque.hc_fake.screen = ecran(Q2)
    vue = {"contexte": ch.parse_contexte(ecran(Q2)), "libelles": ["[ ] Voix", "[ ] QR", "[ ] Langue", "[ ] Type something"]}
    r, b = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", **vue})
    assert r.status == 200, b
    assert [c[1]["keys"] for c in bloque.hc_fake.calls if c[0] == "terminal.keys"] == [["1"]]


def test_answer_libelles_pollues_d_une_page_d_avant_refuses(bloque):
    """La page d'avant envoyait les libellés lus avec l'aperçu : l'écran ne les porte plus, 409."""
    bloque.hc_fake.screen = ecran(Q1)
    vue = {"contexte": ch.parse_contexte(ecran(Q1)), "libelles": ["Onglets                      ┌──┐", "Cartes │ +--+ │"]}
    r, _ = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m", **vue})
    assert r.status == 409
    assert not [c for c in bloque.hc_fake.calls if c[0] == "terminal.keys"]


# ---------- réserves P1 et P3 du checker de la PR #27 ----------

TRANSCRIPT = "● Le dialogue à aperçu affiche « Notes: press n to add notes » sous les options.\n\n"


def test_P1_la_ligne_d_aide_n_est_cherchee_que_dans_le_dialogue():
    assert ch.est_apercu(TRANSCRIPT + ecran(Q2)) is False
    assert ch.est_apercu(TRANSCRIPT + ecran(FIX / "ecran_question.txt")) is False
    assert ch.est_apercu(TRANSCRIPT + ecran(Q1)) is True


def test_P1_entree_tiree_du_journal_quand_la_question_y_est(bloque):
    """Question à cases du journal (sans aperçu) sous un transcript qui cite la ligne d'aide :
    chiffre seul."""
    bloque.hc_fake.screen = TRANSCRIPT + ecran(Q2)
    vue = {"contexte": ch.parse_contexte(ecran(Q2)), "libelles": ["[ ] Voix", "[ ] QR", "[ ] Langue", "[ ] Type something"]}
    r, b = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m", **vue})
    assert r.status == 200, b
    assert [c[1]["keys"] for c in bloque.hc_fake.calls if c[0] == "terminal.keys"] == [["2"]]


def test_P1_question_a_cases_avec_aperçus_au_journal_chiffre_seul(bloque, tmp_path, monkeypatch):
    """Le dialogue à aperçu n'existe qu'en choix unique (`DZ` : `!multiSelect && options.some(preview)`) :
    une question à cases dont les options portent `preview` se répond au chiffre seul."""
    qs = json.loads(json.dumps(APPEL["input"]["questions"]))
    for o in qs[1]["options"]:
        o["preview"] = "+--+\n|" + o["label"] + "|\n+--+"
    f = journal(tmp_path, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "y", "name": "AskUserQuestion", "input": {"questions": qs}}]}})
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: f)
    bloque.hc_fake.screen = ecran(Q2)
    vue = {"contexte": ch.parse_contexte(ecran(Q2)), "libelles": ["[ ] Voix", "[ ] QR", "[ ] Langue", "[ ] Type something"]}
    r, b = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m", **vue})
    assert r.status == 200, b
    assert [c[1]["keys"] for c in bloque.hc_fake.calls if c[0] == "terminal.keys"] == [["2"]]


MEME_TEXTE = [{"question": "Quel langage ?", "header": "Front", "multiSelect": False,
               "options": [{"label": "TypeScript", "description": "", "preview": "TS"},
                           {"label": "Elm", "description": "", "preview": "ELM"}]},
              {"question": "Quel langage ?", "header": "Back", "multiSelect": False,
               "options": [{"label": "Go", "description": "Go d", "preview": ""},
                           {"label": "Rust", "description": "Rust d", "preview": ""}]}]
ECRAN_MEME_TEXTE = """────────────────────────────────────────────
←  ☒ Front  ☐ Back  ✔ Submit  →

Quel langage ?

❯ 1. Go
  2. Rust
  3. Type something.
────────────────────────────────────────────
  4. Chat about this
"""


def test_P3_deux_questions_au_meme_texte_departagees_par_les_options():
    q = ch.question_affichee(MEME_TEXTE, ECRAN_MEME_TEXTE)
    assert q is MEME_TEXTE[1]
    assert [c.detail for c in ch.choix_structures(q, ch.parse_choices(ECRAN_MEME_TEXTE))][:2] == ["Go d", "Rust d"]


def test_P3_meme_texte_autres_options_repli_sur_l_ecran():
    """Seule la question Front (TypeScript, Elm) est au journal, l'écran montre Go, Rust : ce n'est
    pas elle."""
    assert ch.question_affichee(MEME_TEXTE[:1], ECRAN_MEME_TEXTE) is None
    assert ch.choix_structures(MEME_TEXTE[0], ch.parse_choices(ECRAN_MEME_TEXTE)) is None


def test_P3_libelle_replie_a_l_ecran_accepte():
    long = [{"question": "Quel langage ?", "multiSelect": False,
             "options": [{"label": "Go, le langage de Google", "description": "", "preview": ""},
                         {"label": "Rust", "description": "", "preview": ""}]}]
    ecran = ECRAN_MEME_TEXTE.replace("1. Go", "1. Go, le langage")  # replié par le TUI
    assert ch.question_affichee(long, ecran) is long[0]


def test_P3_ambigu_repli_sur_l_ecran():
    deux = [dict(MEME_TEXTE[1], header="A"),
            dict(MEME_TEXTE[1], header="B", options=[dict(o, description="autre") for o in MEME_TEXTE[1]["options"]])]
    assert ch.question_affichee(deux, ECRAN_MEME_TEXTE) is None


def test_P3_route_montre_et_valide_les_options_de_l_ecran(bloque, tmp_path, monkeypatch):
    f = journal(tmp_path, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "x", "name": "AskUserQuestion", "input": {"questions": MEME_TEXTE}}]}})
    monkeypatch.setattr("herdr_web.server.resolve_session", lambda pid: f)
    bloque.hc_fake.screen = ECRAN_MEME_TEXTE
    d = json.loads(req(bloque, "GET", "/api/agents/t1/choices")[1])
    assert [c["label"] for c in d["choices"]] == ["Go", "Rust", "Type something."]
    faux = {"contexte": ch.parse_contexte(ECRAN_MEME_TEXTE), "libelles": ["TypeScript", "Elm", "Type something."]}
    r, _ = req(bloque, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", **faux})
    assert r.status == 409  # la vue fausse n'est plus validée
    assert not [c for c in bloque.hc_fake.calls if c[0] == "terminal.keys"]
