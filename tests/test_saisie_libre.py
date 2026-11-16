"""« Type something. » d'un AskUserQuestion (brief appairage, 2026-11-15) : la page ouvre un champ,
et la réponse saisie arrive dans le TUI comme si on l'avait tapée.

Mesuré dans Claude Code 2.1.325 : le chiffre de cette option, champ vide, ne fait que poser le
curseur sur le champ (`focusOption`) ; Entrée sur le champ non vide répond. `terminal.keys` ne prend
que des noms de touches (« é », « ! », « ab » refusés : invalid_params, mesuré sur un terminal
jetable) : le texte passe par `terminal.send` forcé (collé, puis Entrée).
Fichier à part : test_server.py porte un jeton de test que le garde-secret refuse de laisser relire."""
import json
import re
from pathlib import Path

import pytest

from test_server import FakeHc, cert, req, srv  # noqa: F401
from herdr_web.choices import ChoiceStale, answer_texte, parse_choices, parse_contexte

ECRAN = """←  ☐ Couleur  ☐ Taille  ✔ Submit  →

Quelle couleur ?

❯ 1. Rouge
  2. Vert
  3. Type something.
────────────────────────────────────────────────────────────────────────────────
  4. Chat about this
"""
VUE = {"contexte": "←  ☐ Couleur  ☐ Taille  ✔ Submit  →\nQuelle couleur ?",
       "libelles": ["Rouge", "Vert", "Type something."]}


def appels(hc, *methodes):
    return [c for c in hc.calls if c[0] in methodes]


_LIGNE = re.compile(r"^(\s*)(❯\s*)?(\d)\.(\s+)(.*)$")


def tui_chiffre(ecran: str, d: str) -> str:
    """Ce que fait un chiffre sur la ligne libre, mesuré sur un vrai TUI 2.1.325 (fixtures
    ecran_garde_*) : ligne à case ⇒ case basculée, curseur resté ; sinon ⇒ curseur sur la ligne."""
    lignes = ecran.split("\n")
    cible = next((i for i, l in enumerate(lignes) if (m := _LIGNE.match(l)) and m.group(3) == d), None)
    if cible is None:
        return ecran
    m = _LIGNE.match(lignes[cible])
    if m.group(5).startswith(("[ ] ", "[✔] ")):
        bascule = ("[✔] " if m.group(5).startswith("[ ] ") else "[ ] ") + m.group(5)[4:]
        lignes[cible] = f"{m.group(1)}{m.group(2) or ''}{d}.{m.group(4)}{bascule}"
        return "\n".join(lignes)
    for i, l in enumerate(lignes):
        if (n := _LIGNE.match(l)) and n.group(2):
            lignes[i] = l.replace("❯ ", "  ", 1)
    lignes[cible] = "❯ " + lignes[cible][2:]
    return "\n".join(lignes)


class HcTui(FakeHc):
    """Faux core dont l'écran réagit au chiffre comme le TUI."""

    def __init__(self, ecran, msg="m"):
        super().__init__()
        self.screen, self.status = ecran, {"state": "blocked", "msg": msg}

    def request(self, method, params, timeout=100.0):
        r = super().request(method, params, timeout)
        if method == "terminal.keys" and params["keys"] != ["enter"]:
            self.screen = tui_chiffre(self.screen, params["keys"][0])
        return r


def test_ecran_de_reference():
    assert [c.label for c in parse_choices(ECRAN)] == VUE["libelles"]


def test_answer_texte_chiffre_puis_texte_colle_force():
    hc = HcTui(ECRAN)
    answer_texte(hc, "t1", "3", "m", "Bleu canard, l'été !")
    assert appels(hc, "terminal.keys", "terminal.send") == [
        ("terminal.keys", {"terminal": "t1", "keys": ["3"]}),
        ("terminal.send", {"terminal": "t1", "text": "Bleu canard, l'été !", "force": True})]


def test_answer_texte_une_seule_ligne():
    """Un saut de ligne collé hors du mode « bracketed paste » validerait le champ trop tôt."""
    hc = HcTui(ECRAN)
    answer_texte(hc, "t1", "3", "m", "  ligne 1\nligne 2\r\n\tfin  ")
    assert appels(hc, "terminal.send")[0][1]["text"] == "ligne 1 ligne 2 fin"


@pytest.mark.parametrize("status", [{"state": "blocked", "msg": "autre"}, {"state": "working", "msg": "m"}, None])
def test_answer_texte_garde_comme_un_chiffre(status):
    hc = FakeHc()
    hc.status = status
    with pytest.raises(ChoiceStale):
        answer_texte(hc, "t1", "3", "m", "x")
    assert not appels(hc, "terminal.keys", "terminal.send")


@pytest.mark.parametrize("texte", ["", "   \n ", "x" * 4001])
def test_answer_texte_vide_ou_trop_long(texte):
    hc = FakeHc()
    hc.status = {"state": "blocked", "msg": "m"}
    with pytest.raises(ValueError):
        answer_texte(hc, "t1", "3", "m", texte)
    assert not appels(hc, "terminal.keys", "terminal.send")


# ---------- la route ----------

def bloque(s):
    s.hc = s.hc_fake = HcTui(ECRAN)


def test_route_texte_libre_arrive_au_tui(srv):
    bloque(srv)
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "Bleu canard", **VUE})
    assert r.status == 200, b
    assert appels(srv.hc_fake, "terminal.keys", "terminal.send") == [
        ("terminal.keys", {"terminal": "t1", "keys": ["3"]}),
        ("terminal.send", {"terminal": "t1", "text": "Bleu canard", "force": True})]


def test_route_chiffre_seul_de_type_something_refuse(srv):
    """L'ancien comportement : le chiffre seul part, le TUI pose le curseur sur le champ, la page
    croit avoir répondu et l'agent reste bloqué. Désormais 400 texte_attendu, aucune touche."""
    bloque(srv)
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", **VUE})
    assert r.status == 400 and json.loads(b)["erreur"] == "texte_attendu"
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


def test_route_texte_sur_une_option_ordinaire_refuse(srv):
    bloque(srv)
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "1", "msg": "m", "texte": "x", **VUE})
    assert r.status == 400
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


@pytest.mark.parametrize("texte", ["", "   ", None, 3, ["x"]])
def test_route_texte_invalide_400(srv, texte):
    bloque(srv)
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": texte, **VUE})
    assert r.status == 400
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


def test_route_texte_ecran_tourne_409(srv):
    """Q1 répondue dans le terminal entre-temps : le texte ne part pas vers Q2."""
    bloque(srv)
    srv.hc_fake.screen = ECRAN.replace("Quelle couleur ?", "Quelle taille ?").replace("☐ Couleur", "☒ Couleur")
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "x", **VUE})
    assert r.status == 409 and json.loads(b)["erreur"] == "perime"
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


def test_route_texte_msg_change_409(srv):
    bloque(srv)
    srv.hc_fake.status = {"state": "blocked", "msg": "autre"}
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "x", **VUE})
    assert r.status == 409
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


def test_route_option_ordinaire_inchangee(srv):
    bloque(srv)
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "2", "msg": "m", **VUE})
    assert r.status == 200
    assert appels(srv.hc_fake, "terminal.keys", "terminal.send") == [("terminal.keys", {"terminal": "t1", "keys": ["2"]})]


# ---------- question à cases (multiSelect), séquence mesurée sur un vrai TUI 2.1.325 ----------
# chiffre ⇒ `3. [✔] Type something` cochée, curseur resté ; collage + Entrée ⇒ `❯ 3. [ ] Zig` (case
# DÉCOCHÉE) ; Entrée ⇒ `❯ 3. [✔] Zig` (fixtures ecran_garde_q2_*)

CASES = """←  ☐ Langages  ✔ Submit  →

Quels langages ?

❯ 1. [✔] Python
  2. [ ] Rust
  3. [ ] Type something
"""
VUE_CASES = {"contexte": "←  ☐ Langages  ✔ Submit  →\nQuels langages ?",
             "libelles": ["[✔] Python", "[ ] Rust", "[ ] Type something"]}


class HcCases(HcTui):
    """L'écran change au chiffre (case cochée) puis au collage (texte, case décochée), comme le TUI."""

    def __init__(self, apres_collage):
        super().__init__(CASES)
        self.apres = apres_collage

    def request(self, method, params, timeout=100.0):
        r = super().request(method, params, timeout)
        if method == "terminal.send":
            self.screen = self.apres
        return r


def test_ecran_cases_de_reference():
    assert [c.label for c in parse_choices(CASES)] == VUE_CASES["libelles"]


def test_cases_texte_puis_entree_qui_coche():
    hc = HcCases(CASES.replace("[ ] Type something", "[ ] Zig"))
    answer_texte(hc, "t1", "3", "m", "Zig", case=True)
    assert appels(hc, "terminal.keys", "terminal.send") == [
        ("terminal.keys", {"terminal": "t1", "keys": ["3"]}),
        ("terminal.send", {"terminal": "t1", "text": "Zig", "force": True}),
        ("terminal.keys", {"terminal": "t1", "keys": ["enter"]})]


def test_cases_deja_cochee_pas_d_entree_qui_decocherait():
    hc = HcCases(CASES.replace("[ ] Type something", "[✔] Zig"))
    answer_texte(hc, "t1", "3", "m", "Zig", case=True)
    assert [c[1]["keys"] for c in appels(hc, "terminal.keys")] == [["3"]]


@pytest.mark.parametrize("apres", ["écran parti\n", CASES.replace("3. [ ] Type something", "3. Chat about this")])
def test_cases_ecran_illisible_apres_collage_pas_d_entree_a_l_aveugle(apres):
    hc = HcCases(apres)
    with pytest.raises(ChoiceStale):
        answer_texte(hc, "t1", "3", "m", "Zig", case=True)
    assert [c[1]["keys"] for c in appels(hc, "terminal.keys")] == [["3"]]


def test_choix_unique_pas_d_entree_supplementaire():
    hc = HcTui(ECRAN)
    answer_texte(hc, "t1", "3", "m", "Bleu")
    assert [c[1]["keys"] for c in appels(hc, "terminal.keys")] == [["3"]]
    assert len(appels(hc, "terminal.read")) == 1  # la garde d'après chiffre, rien après le collage


# ---------- P2 du checker : garde ENTRE le chiffre et le collage forcé ----------

FIX = Path(__file__).parent / "fixtures"


def fixture(nom):
    return (FIX / f"ecran_garde_{nom}.txt").read_text()


class HcRejoue(FakeHc):
    """Rejoue les écrans réels relevés sur le TUI : un écran par touche envoyée, puis par collage."""

    def __init__(self, avant, apres_chiffre, apres_collage=None, apres_entree=None):
        super().__init__()
        self.screen, self.status = avant, {"state": "blocked", "msg": "m"}
        self.suite = {"chiffre": apres_chiffre, "collage": apres_collage, "entree": apres_entree}

    def request(self, method, params, timeout=100.0):
        r = super().request(method, params, timeout)
        cle = {"terminal.send": "collage"}.get(method) or (
            ("entree" if params["keys"] == ["enter"] else "chiffre") if method == "terminal.keys" else None)
        if cle and self.suite[cle] is not None:
            self.screen = self.suite[cle]
        return r


def test_ecrans_reels_choix_unique_texte_colle():
    avant = fixture("q1_avant")
    hc = HcRejoue(avant, fixture("q1_apres3"))
    answer_texte(hc, "t1", "3", "m", "Bleu", contexte=parse_contexte(avant))
    assert [c[0] for c in appels(hc, "terminal.keys", "terminal.send")] == ["terminal.keys", "terminal.send"]


def test_ecrans_reels_cases_texte_colle_puis_entree():
    avant = fixture("q2_avant")
    hc = HcRejoue(avant, fixture("q2_a_chiffre"), fixture("q2_b_texte"), fixture("q2_c_enter"))
    answer_texte(hc, "t1", "3", "m", "Zig", case=True, contexte=parse_contexte(avant))
    assert appels(hc, "terminal.keys", "terminal.send") == [
        ("terminal.keys", {"terminal": "t1", "keys": ["3"]}),
        ("terminal.send", {"terminal": "t1", "text": "Zig", "force": True}),
        ("terminal.keys", {"terminal": "t1", "keys": ["enter"]})]


@pytest.mark.parametrize("cas,avant,apres", [
    # le chiffre n'a rien changé (curseur resté sur 1) : choix unique
    ("unique-immobile", "q1_avant", "q1_avant"),
    # le chiffre a DÉCOCHÉ la ligne (déjà cochée au terminal) : à cases
    ("cases-decochee", "q2_a_chiffre", "q2_apres3bis"),
    # l'écran est passé au dialogue suivant (permission, « 1. Yes » focalisé)
    ("autre-dialogue", "q1_avant", None),
    # même chiffre, autre question (Q2 répondue au terminal entre-temps) : contexte différent
    ("autre-question", "q1_avant", "q2_a_chiffre"),
    # autre question À CHOIX UNIQUE, elle aussi « ❯ 3. Type something. » : seul le contexte la trahit
    ("autre-question-meme-ligne", "q1_avant", "autre"),
])
def test_rien_n_est_colle_si_l_ecran_d_apres_chiffre_n_est_pas_l_attendu(cas, avant, apres):
    from test_sondes_checker import PERMISSION_AVEC_TRANSCRIPT
    av = fixture(avant)
    autre = fixture("q1_apres3").replace("Couleur ?", "Taille ?").replace("☐ Couleur", "☒ Couleur")
    hc = HcRejoue(av, PERMISSION_AVEC_TRANSCRIPT if apres is None else autre if apres == "autre" else fixture(apres))
    with pytest.raises(ChoiceStale):
        answer_texte(hc, "t1", "3", "m", "Zig", case=avant.startswith("q2"), contexte=parse_contexte(av))
    assert not appels(hc, "terminal.send")
    assert [c[1]["keys"] for c in appels(hc, "terminal.keys")] == [["3"]]


def test_route_garde_d_apres_chiffre_compare_le_contexte(srv):
    """La route passe le contexte qu'elle a vérifié : une autre question à la même ligne libre
    sous le curseur, apparue entre-temps, ne reçoit pas le texte."""
    av = fixture("q1_avant")
    autre = fixture("q1_apres3").replace("Couleur ?", "Taille ?").replace("☐ Couleur", "☒ Couleur")
    srv.hc = srv.hc_fake = HcRejoue(av, autre)
    vue = {"contexte": parse_contexte(av), "libelles": [c.label for c in parse_choices(av)]}
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "Zig", **vue})
    assert r.status == 409, b
    assert not appels(srv.hc_fake, "terminal.send")


def test_rien_n_est_colle_si_l_agent_n_est_plus_bloque_apres_le_chiffre():
    class Repart(HcTui):
        def request(self, method, params, timeout=100.0):
            r = super().request(method, params, timeout)
            if method == "terminal.keys":
                self.status = {"state": "working", "msg": ""}
            return r
    hc = Repart(ECRAN)
    with pytest.raises(ChoiceStale):
        answer_texte(hc, "t1", "3", "m", "Zig")
    assert not appels(hc, "terminal.send")


def test_route_cases_texte_libre_coche_la_ligne_et_garde_les_autres(srv):
    srv.hc = srv.hc_fake = HcCases(CASES.replace("[ ] Type something", "[ ] Zig"))
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "Zig", **VUE_CASES})
    assert r.status == 200, b
    # la case 1 cochée par la page n'est pas touchée : seules la ligne 3 et un Entrée partent
    assert appels(srv.hc_fake, "terminal.keys", "terminal.send") == [
        ("terminal.keys", {"terminal": "t1", "keys": ["3"]}),
        ("terminal.send", {"terminal": "t1", "text": "Zig", "force": True}),
        ("terminal.keys", {"terminal": "t1", "keys": ["enter"]})]


def test_route_cases_chiffre_seul_refuse(srv):
    srv.hc = srv.hc_fake = HcCases(CASES)
    r, b = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", **VUE_CASES})
    assert r.status == 400 and json.loads(b)["erreur"] == "texte_attendu"
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")


def test_route_cases_ecran_tourne_409(srv):
    srv.hc = srv.hc_fake = HcCases(CASES)
    srv.hc_fake.screen = CASES.replace("[✔] Python", "[ ] Python")  # décochée dans le terminal entre-temps
    r, _ = req(srv, "POST", "/api/agents/t1/answer", {"digit": "3", "msg": "m", "texte": "Zig", **VUE_CASES})
    assert r.status == 409
    assert not appels(srv.hc_fake, "terminal.keys", "terminal.send")
