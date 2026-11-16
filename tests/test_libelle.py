"""Libellé d'un agent, calculé comme le mod agents de herdr-app : « space · détail ».

Le space suit le worktree parce que herdr-app nomme un space jamais renommé d'après le dépôt du
dossier VIVANT de son 1er terminal (un worktree lié : son propre dossier). Le détail est l'onglet
s'il a été nommé, sinon le titre de l'agent quand il dit quelque chose.
"""
from pathlib import Path

import pytest

from herdr_web.agents import AgentBoard, libelle, lieu_du_dossier


@pytest.fixture(autouse=True)
def _aucun_vrai_git(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError(f"processus réel lancé par un test : {a[:1]}")
    monkeypatch.setattr("herdr_web.agents.subprocess.run", interdit)


# --- libelle() : la règle du mod agents-panel (title() puis « space · détail ») ---

def test_onglet_nomme_l_emporte_sur_le_titre():
    assert libelle(None, "my-project", "build .html", "✳ Rename space", "claude-code") == \
        "my-project · build .html"


def test_onglet_automatique_cede_au_titre_parlant():
    assert libelle(None, "fin-session-herdr-web", "1", "✳ Rename space", "claude-code") == \
        "fin-session-herdr-web · Rename space"


def test_onglet_automatique_et_titre_muet_donnent_le_space_seul():
    # titre de shell (« user@hôte:chemin ») : il ne dit rien, l'onglet « 1 » non plus
    assert libelle(None, "Work", "1", "user@my-laptop:~/Claude/my-project/work", "zsh") == "Work"
    assert libelle(None, "Work", "1", "~/Claude/my-project/work", "zsh") == "Work"
    assert libelle(None, "Work", "1", "/home/user", "zsh") == "Work"
    assert libelle(None, "Work", "1", "", "claude-code") == "Work"


def test_titre_egal_au_programme_ne_dit_rien():
    assert libelle(None, "sp", "2", "claude", "claude-code") == "sp"
    assert libelle(None, "sp", "2", "Claude Code", "claude-code") == "sp"
    assert libelle(None, "sp", "2", "zsh", "zsh") == "sp"


def test_onglet_nomme_comme_le_programme_cede_au_titre():
    assert libelle(None, "sp", "claude-code", "✳ Revue", "claude-code") == "sp · Revue"


def test_onglet_partage_prend_le_titre_de_l_agent():
    # plusieurs terminaux dans l'onglet : son nom ne distingue personne
    assert libelle(None, "sp", "build", "✳ Revue", "claude-code", partage=True) == "sp · Revue"
    assert libelle(None, "sp", "build", "user@h:~", "zsh", partage=True) == "sp · build"


def test_nom_hc_explicite_l_emporte():
    assert libelle("fix-login", "sp", "build", "✳ Revue", "claude-code") == "fix-login"


def test_sans_space_le_titre_puis_rien():
    assert libelle(None, None, None, "✳ Revue", "claude-code") == "Revue"
    assert libelle(None, None, None, "", "zsh") == ""


# --- lieu_du_dossier() : le nom que herdr-app donne à un space jamais renommé ---

def test_lieu_depot_worktree_dossier_maison(tmp_path):
    repo = tmp_path / "my-project"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir()
    wt = repo / ".claude" / "worktrees" / "fin-session-herdr-web"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {repo}/.git/worktrees/fin-session-herdr-web\n")
    (wt / "src").mkdir()
    sans = tmp_path / "ici"
    sans.mkdir()
    assert lieu_du_dossier(str(repo / "scripts"), tmp_path / "maison") == "my-project"
    assert lieu_du_dossier(str(wt / "src"), tmp_path / "maison") == "fin-session-herdr-web"
    assert lieu_du_dossier(str(sans), tmp_path / "maison") == "ici"
    assert lieu_du_dossier(str(sans), sans) == "~"
    assert lieu_du_dossier("", tmp_path) == ""


# --- le tableau : label sur chaque agent ---

class _Hc:
    def __init__(self, terminals):
        self.terminals = terminals

    def on_event(self, cb):
        pass

    def on_state(self, cb):
        pass

    def request(self, method, params=None, timeout=100.0):
        return {"terminals": self.terminals, "hidden_finished": 0, "machines": []}


class _SansGit:
    def get(self, cwd):
        return None


def _term(addr, title, cwd, app="claude-code", name=None):
    return {"address": addr, "name": name, "title": title, "cwd": cwd, "state": "live", "pid": 10,
            "turn": 0, "status": {"app": app, "state": "working", "msg": "", "kind": None},
            "foreground": {"pid": 10, "name": "claude", "argv": [], "cwd": cwd}}


def _board(terms, layout, home):
    b = AgentBoard(_Hc(terms), layout=lambda: layout, home=home, branches=_SansGit())
    b._proc = Path("/nonexistent")  # pid inventés : jamais le /proc réel
    b.refresh()
    return {a.address: a for a in b.snapshot()}


LAYOUT = {"spaces": [
    {"id": "sp1", "name": "fin-session-herdr-web", "tabs": [
        {"id": "tb1", "name": "1", "terminals": [{"terminal": "t1"}]},
        {"id": "tb2", "name": "revue", "terminals": [{"terminal": "t2"}, {"terminal": "t3"}]}]}]}


def test_label_depuis_la_disposition(tmp_path):
    by = _board([_term("t1", "✳ Fin de session", "/x"), _term("t2", "✳ Revue A", "/x"),
                 _term("t3", "user@h:~", "/x", app="zsh"), _term("t9", "✳ Seul", "/x", name="maker-1")],
                LAYOUT, tmp_path)
    assert by["t1"].label == "fin-session-herdr-web · Fin de session"
    assert by["t2"].label == "fin-session-herdr-web · Revue A"  # onglet partagé : son titre
    assert by["t3"].label == "fin-session-herdr-web · revue"
    assert by["t9"].label == "maker-1"


def test_label_sans_fenetre_suit_le_worktree_du_dossier(tmp_path):
    repo = tmp_path / "my-project"
    (repo / ".git").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "veille"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {repo}/.git/worktrees/veille\n")
    by = _board([_term("t1", "✳ Veille", str(wt))], None, tmp_path)
    assert by["t1"].label == "veille · Veille"
    assert by["t1"].space is None  # la section Spaces ne s'invente rien
