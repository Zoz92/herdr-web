import pytest


@pytest.fixture(autouse=True)
def proc_vide(tmp_path_factory, monkeypatch):
    """Les pid inventés des tests existent sur la machine : sans ce dossier vide, AgentBoard lirait
    le cwd de vrais processus (/proc/<pid>/cwd) au lieu du cwd des terminaux de test."""
    monkeypatch.setattr("herdr_web.agents.PROC", tmp_path_factory.mktemp("proc"))


@pytest.fixture(autouse=True)
def reglages_jetables(tmp_path_factory, monkeypatch):
    """Jamais le vrai ~/.config/herdr-web/reglages.json : une voix désactivée chez l'utilisateur ferait rougir
    les tests vocaux, et un test qui écrit les réglages ne doit pas toucher la machine."""
    monkeypatch.setattr("herdr_web.server.REGLAGES", tmp_path_factory.mktemp("reglages") / "reglages.json")
