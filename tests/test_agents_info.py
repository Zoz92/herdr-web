import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "agents_info", Path(__file__).resolve().parent.parent / "tools" / "agents_info.py")
ai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ai)


@pytest.fixture(autouse=True)
def _aucun_vrai_git(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError(f"processus réel lancé par un test : {a[:1]}")
    monkeypatch.setattr("herdr_web.agents.subprocess.run", interdit)


class _SansGit:
    def get(self, cwd):
        return "main"


def _board(hc, layout):
    from herdr_web.agents import AgentBoard
    b = AgentBoard(hc, layout=layout, home=Path("/nonexistent"), branches=_SansGit())
    b._proc = Path("/nonexistent")
    return b


def _t(addr, cwd="/x/proj"):
    return {"address": addr, "name": None, "title": "✳ Revue", "cwd": cwd, "state": "live", "pid": 10,
            "turn": 0, "status": {"app": "claude-code", "state": "working", "msg": "", "kind": None},
            "foreground": {"pid": 10, "name": "claude", "argv": ["claude", "--model", "opus"], "cwd": cwd}}


def test_infos_par_adresse():
    vus = []

    def get(a):
        vus.append(a)
        return {"t1": _t("t1"), "t2": None}.get(a)
    out = ai.infos(["t1", "t2", "vps/t7"], get=get, board_cls=_board, os_lu=lambda: ("glfos", False))
    assert vus == ["t1", "t2"]  # une adresse distante n'est pas demandée au hc local
    assert set(out) == {"t1"}
    d = out["t1"]
    assert d["model"] == "opus" and d["branch"] == "main" and d["os"] == "glfos" and d["os_wsl"] is False
    assert d["dir"] == "proj" and d["label"] == "proj · Revue" and d["role"] == "claude"


def test_hc_injoignable_rend_vide():
    assert ai.infos(["t1"], get=lambda a: None, board_cls=_board, os_lu=lambda: ("", False)) == {}


def test_os_id(tmp_path):
    f = tmp_path / "os-release"
    f.write_text('NAME="GLF-OS"\nID=glfos\n')
    assert ai.os_id(f) == "glfos"
    f.write_text('ID="debian"\n')
    assert ai.os_id(f) == "debian"
    assert ai.os_id(tmp_path / "absent") == ""


def test_hc_get_en_echec_rend_none():
    class R:
        returncode, stdout = 1, ""
    assert ai.hc_get("t1", run=lambda *a, **k: R()) is None

    def boom(*a, **k):
        raise OSError("hc absent")
    assert ai.hc_get("t1", run=boom) is None


# -- contrat élargi (spec du 2026-11-15) : since_ms, msg, subagents, mêmes valeurs que la page --

def test_contrat_since_msg_et_sous_agents():
    t = _t("t1")
    t["status"] = {"app": "claude-code", "state": "blocked", "msg": "Allow Bash?", "kind": None}
    t["status_ms"] = 1794732763556
    t["records"] = {"ab12": {"title": "Review the diff", "state": "working"}}
    d = ai.infos(["t1"], get=lambda a: t, board_cls=_board, os_lu=lambda: ("", False))["t1"]
    assert d["since_ms"] == 1794732763556 and d["msg"] == "Allow Bash?"
    assert d["subagents"] == [{"id": "ab12", "title": "Review the diff", "state": "working", "model": None,
                               "context": None, "context_max": None, "role": "reviewer"}]


def test_contrat_msg_vide_et_since_absent_rendent_null():
    d = ai.infos(["t1"], get=lambda a: _t("t1"), board_cls=_board, os_lu=lambda: ("", False))["t1"]
    assert d["since_ms"] is None and d["msg"] is None and d["subagents"] == []
    # champs existants inchangés
    assert {"model", "context", "context_max", "dir", "branch", "machine", "role", "activity", "label",
            "os"} <= set(d)
