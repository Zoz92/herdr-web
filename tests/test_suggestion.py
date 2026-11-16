"""Suggestion grisée de Claude Code (herdr_web/suggestion.py) et sa route /api/agents/<t>/suggestion.

Fixtures : premiers dessins RÉELS de `hc attach --observe` relevés le 2026-11-15 sur un vrai
Claude Code (fin du dessin seulement, à partir de deux lignes au-dessus du prompt) :
suggestion.bin (t4w, suggestion vivante), brouillon.bin (t4w, « brouillon de test » tapé),
placeholder.bin (t4x, Claude neuf, `Try "…"`),
collage.bin (t52, « [Pasted text #1 +4 lines] » collé sans Entrée).
Aucun cas n'atteint un vrai `hc` : chaque popen est remplacé, celui qui ne doit pas servir lève."""
import dataclasses
import json
import sys
import threading
from pathlib import Path

import pytest

import test_server
from test_server import FakeBoard, FakeHc, HOST, agent, cert, req  # noqa: F401
from herdr_web import suggestion
from herdr_web.server import make_server

FIX = Path(__file__).parent / "fixtures" / "suggestion"
SUGG = "Montre-moi les 20 premières lignes de server.py"
BORD = b"\x1b[0m\x1b[38;2;136;136;136m" + "─".encode() * 20 + b"\x1b[0m\r\n"
FIN = b"\x1b[0m\x1b]7501;state=done:app=claude-code\x1b\\"


def dessin(fichier):
    return (FIX / fichier).read_bytes()


def prompt(ligne: bytes, curseur=b"\x1b[40;3H", suite=BORD) -> bytes:
    return BORD + "❯ ".encode() + ligne + b"\r\n" + suite + b"status\r\n" + curseur + FIN


# -- lecture du dessin --

def test_suggestion_reelle_lue():
    assert suggestion.lire(dessin("suggestion.bin")) == SUGG


def test_brouillon_reel_jamais_pris_pour_une_suggestion():
    """Texte sans attribut, curseur en fin : c'est l'utilisateur qui a tapé."""
    assert suggestion.lire(dessin("brouillon.bin")) is None


def test_placeholder_reel_d_un_claude_neuf_refuse():
    """Encodé comme une suggestion (dim, colonne 3) : seul le motif le distingue (et le tour 0)."""
    assert suggestion.lire(dessin("placeholder.bin")) is None


def test_collage_reel_refuse():
    """t52 (2026-11-15) : collage de 5 lignes sans Entrée, « [Pasted text #1 +4 lines] » sans attribut,
    curseur en fin (colonne 28) : deux gardes l'écartent chacune."""
    d = dessin("collage.bin")
    assert b"[Pasted text #1 +4 lines]" in d and b"\x1b[115;28H" in d
    assert suggestion.lire(d) is None
    # même rendu dim : la garde du curseur suffit encore
    assert suggestion.lire(d.replace("❯\u00a0[".encode(), "❯\u00a0\x1b[2m[".encode())) is None


def test_suggestion_avec_le_curseur_en_fin_refusee():
    assert suggestion.lire(dessin("suggestion.bin").replace(b"\x1b[115;3H", b"\x1b[115;50H")) is None


def test_dessin_incomplet_refuse():
    """Sans le fait d'état de fin du dessin, la ligne du prompt peut être coupée."""
    d = dessin("suggestion.bin")
    assert suggestion.lire(d[:d.find(b"\x1b]7501;")]) is None


@pytest.mark.parametrize("ligne", [
    b"brouillon",  # tapé
    b"bro\x1b[2muillon",  # début tapé, fin grisée (complétion d'une commande /…)
    b"\x1b[2mfais \x1b[22mle test",  # dim éteint par 22
    b"\x1b[2mfais \x1b[0mle test",  # dim éteint par 0
    b"\x1b[38;5;2mvert",  # 2 = couleur, pas dim
    b"\x1b[38;2;2;2;2mgris fonce",  # composantes RVB à 2, pas dim
    b"",  # prompt vide
    b"\x1b[2m   ",  # que des espaces
])
def test_cas_qui_ne_sont_pas_une_suggestion(ligne):
    assert suggestion.lire(prompt(ligne)) is None


def test_suggestion_synthetique_et_combinaisons_sgr():
    assert suggestion.lire(prompt(b"\x1b[0m\x1b[2;38;2;153;153;153mfais le test\x1b[0m")) == "fais le test"


def test_texte_sur_deux_lignes_refuse():
    """Pas mesuré : un texte qui déborde sur la ligne suivante n'est pas recopié à moitié."""
    assert suggestion.lire(prompt(b"\x1b[2mdebut", suite=b"\x1b[2m  suite\x1b[0m\r\n" + BORD)) is None


# -- capture : un faux hc attach, jamais le vrai --

def faux_popen(contenu: bytes, vus: list):
    import subprocess as sp

    def popen(argv, **kw):
        vus.append(argv)
        code = f"import sys,time; sys.stdout.buffer.write({contenu!r}); sys.stdout.flush(); time.sleep(30)"
        p = sp.Popen([sys.executable, "-c", code], **kw)
        vus.append(p)
        return p
    return popen


def test_capture_rend_le_dessin_puis_tue_l_observateur():
    import time
    vus = []
    d = dessin("suggestion.bin")
    t0 = time.monotonic()
    recu = suggestion.capturer(faux_popen(d, vus), "t9", 200, 120, delai=10)
    # rendu dès la fin du dessin, observateur tué par terminate : ni le délai (10 s) ni le kill de repli (3 s)
    assert time.monotonic() - t0 < 2
    assert vus[0] == ["hc", "attach", "--observe", "t9"]  # observe : jamais une touche
    assert suggestion.lire(recu) == SUGG
    assert vus[1].poll() is not None, "l'observateur doit mourir dès le dessin reçu"


def test_capture_bornee_par_le_delai():
    vus = []
    recu = suggestion.capturer(faux_popen(b"partiel", vus), "t9", 80, 24, delai=0.5)
    assert recu == b"partiel" and suggestion.lire(recu) is None
    assert vus[1].poll() is not None


# -- route --

def interdit(argv, **kw):
    """Un cas qui ne doit pas capturer : atteindre hc est un échec, enregistré puis levé."""
    interdit.appels.append(argv)
    raise AssertionError(f"capture non attendue : {argv}")


interdit.appels = []


@pytest.fixture
def srv(cert, tmp_path):
    hc, board = FakeHc(), FakeBoard([dataclasses.replace(agent(state="done"), turn=3)])
    (tmp_path / "index.html").write_text("<html>")
    s = make_server(hc, board, test_server.TOKEN, ("127.0.0.1", 0), [HOST], tmp_path, cert[0], cert[1])
    s.board_fake = board
    s.popen = interdit
    interdit.appels = []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def test_route_rend_la_suggestion_et_garde_le_cache(srv):
    vus = []
    srv.popen = faux_popen(dessin("suggestion.bin"), vus)
    r, b = req(srv, "GET", "/api/agents/t1/suggestion")
    b = json.loads(b)
    assert r.status == 200 and b == {"suggestion": SUGG, "turn": 3}
    r, b = req(srv, "GET", "/api/agents/t1/suggestion")
    b = json.loads(b)
    assert b["suggestion"] == SUGG
    assert len([x for x in vus if isinstance(x, list)]) == 1, "deux pages en 2 s : une seule capture"


def test_route_brouillon_rend_rien(srv):
    srv.popen = faux_popen(dessin("brouillon.bin"), [])
    r, b = req(srv, "GET", "/api/agents/t1/suggestion")
    b = json.loads(b)
    assert r.status == 200 and b["suggestion"] is None


@pytest.mark.parametrize("modif", [
    {"state": "working"}, {"state": "blocked"}, {"turn": 0}, {"program": "zsh"},
])
def test_route_ne_capture_pas_hors_repos(srv, modif):
    srv.board_fake.agents = [dataclasses.replace(srv.board_fake.agents[0], **modif)]
    r, b = req(srv, "GET", "/api/agents/t1/suggestion")
    b = json.loads(b)
    assert r.status == 200 and b["suggestion"] is None
    assert interdit.appels == []


def test_route_exige_le_jeton(srv):
    r, _ = req(srv, "GET", "/api/agents/t1/suggestion", cookie=False)
    assert r.status == 401 and interdit.appels == []
