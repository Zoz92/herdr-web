from pathlib import Path

from herdr_web.agents import AgentBoard


def term(addr, status=None, state="live", name=None, cwd="/work/proj", turn=0):
    t = {"address": addr, "name": name, "title": "", "cwd": cwd, "state": state,
         "pid": 10, "turn": turn, "status": None,
         "foreground": {"pid": 10, "name": "claude", "argv": [], "cwd": cwd}}
    if status is not None:
        t["status"] = {"app": "claude-code", "state": status, "msg": "", "kind": None}
    return t


import pytest


@pytest.fixture(autouse=True)
def _aucun_vrai_git(monkeypatch):
    """Aucun test de ce module ne lance un vrai processus (git compris)."""
    def interdit(*a, **k):
        raise AssertionError(f"processus réel lancé par un test : {a[:1]}")
    monkeypatch.setattr("herdr_web.agents.subprocess.run", interdit)


class _SansGit:
    def get(self, cwd):
        return None


class FakeHc:
    def __init__(self, terminals):
        self.terminals = terminals
        self.cbs = []
        self.state_cbs = []
        self.calls = 0

    def on_event(self, cb):
        self.cbs.append(cb)

    def on_state(self, cb):
        self.state_cbs.append(cb)

    def request(self, method, params=None, timeout=100.0):
        assert method == "terminal.list"
        self.calls += 1
        return {"terminals": self.terminals, "hidden_finished": 0, "machines": []}

    def fire(self, method="event"):
        for cb in self.cbs:
            cb({"method": method, "params": {}})


def board(terminals, layout=lambda: None):
    hc = FakeHc(terminals)
    b = AgentBoard(hc, layout=layout, home=Path("/nonexistent"), branches=_SansGit())
    b.refresh()
    return hc, b


def test_tri_attend_en_tete():
    _, b = board([term("t1", "idle", name="a"), term("t2", "working", name="b"),
                  term("t3", "done", name="c"), term("t4", "blocked", name="d"),
                  term("t5", "error", name="e")])
    assert [a.address for a in b.snapshot()] == ["t4", "t3", "t2", "t1", "t5"]


def test_tri_par_nom_a_etat_egal():
    _, b = board([term("t1", "idle", name="zeta"), term("t2", "idle", name="alpha")])
    assert [a.name for a in b.snapshot()] == ["alpha", "zeta"]


def test_status_null_donne_unknown():
    _, b = board([term("t9", None)])
    a = b.snapshot()[0]
    assert a.state == "unknown" and a.kind is None and a.msg == ""


def test_msg_et_kind_repris():
    t = term("t1", "blocked")
    t["status"].update(kind="permission", msg="approve Bash: touch fichier-test.txt")
    _, b = board([t])
    a = b.snapshot()[0]
    assert a.kind == "permission" and a.msg.startswith("approve Bash")
    assert a.program == "claude-code"


LAYOUT = {"spaces": [{"id": "sp1", "name": "projet", "tabs": [
    {"id": "tb1", "name": "build", "terminals": [
        {"terminal": "t1", "pane": "p1", "status": "working", "app": "claude-code"}]},
    {"id": "tb2", "name": "shell", "terminals": [
        {"terminal": "t9", "pane": "p2", "status": None, "app": None}]}]}]}


def test_space_et_tab_depuis_herdr_app_ls():
    _, b = board([term("t1", "working"), term("t7", "idle")], layout=lambda: LAYOUT)
    by = {a.address: a for a in b.snapshot()}
    assert (by["t1"].space, by["t1"].tab) == ("projet", "build")
    assert (by["t7"].space, by["t7"].tab) == (None, None)


def test_layout_absent_liste_plate():
    _, b = board([term("t1", "working")], layout=lambda: None)
    a = b.snapshot()[0]
    assert a.space is None and a.tab is None


def test_layout_qui_leve_liste_plate():
    def boom():
        raise OSError("fenêtre fermée")
    _, b = board([term("t1", "working")], layout=boom)
    assert b.snapshot()[0].space is None


def test_on_blocked_une_fois():
    hc, b = board([term("t1", "idle")])
    got = []
    b.on_blocked(got.append)
    for s in ["blocked", "blocked", "idle", "blocked"]:
        hc.terminals = [term("t1", s)]
        hc.fire()
    assert len(got) == 2 and got[0].address == "t1"


def test_on_unblocked_quand_l_attente_finit():
    # notification « X attend » restée sur le téléphone après la réponse (2026-11-16) : le serveur doit
    # signaler la fin de l'attente (répondu, ou terminal fermé) pour qu'elle soit effacée
    hc, b = board([term("t1", "idle"), term("t2", "idle")])
    fini = []
    b.on_unblocked(fini.append)
    hc.terminals = [term("t1", "blocked"), term("t2", "blocked")]
    hc.fire()
    assert fini == []
    hc.terminals = [term("t1", "working"), term("t2", "blocked")]
    hc.fire()
    assert fini == ["t1"]
    hc.terminals = [term("t1", "working")]  # t2 fermé pendant qu'il attendait
    hc.fire()
    assert fini == ["t1", "t2"]
    hc.fire()
    assert fini == ["t1", "t2"]  # une fois par fin d'attente


def test_on_unblocked_rien_au_premier_instantane():
    hc, b = board([term("t1", "idle")])
    fini = []
    b.on_unblocked(fini.append)
    hc.fire("missed")
    assert fini == []


def test_reconnexion_ne_renotifie_pas():
    hc, b = board([term("t1", "blocked")])
    got = []
    b.on_blocked(got.append)
    hc.fire("missed")
    for cb in hc.state_cbs:
        cb(False)
        cb(True)
    assert got == []


def test_terminal_fini_retire():
    hc, b = board([term("t1", "idle"), term("t2", "idle")])
    hc.terminals = [term("t1", "idle"), term("t2", "idle", state="finished")]
    hc.fire()
    assert [a.address for a in b.snapshot()] == ["t1"]


def test_terminal_distant_ignore():
    _, b = board([term("t1", "idle"), term("autre/t1", "idle")])
    assert [a.address for a in b.snapshot()] == ["t1"]


def test_subscribe_recoit_la_liste():
    hc, b = board([term("t1", "idle")])
    seen = []
    b.subscribe(seen.append)
    hc.terminals = [term("t1", "working")]
    hc.fire()
    assert seen and seen[-1][0].state == "working"


def test_heartbeat_ignore():
    hc, b = board([term("t1", "idle")])
    seen = []
    b.subscribe(seen.append)
    avant = hc.calls
    hc.fire("heartbeat")
    assert seen == [] and hc.calls == avant


def test_refresh_concurrents_etat_final_recent_sans_doublon():
    import threading
    hc, b = board([term("t1", "idle")])
    got = []
    b.on_blocked(got.append)
    entree = threading.Event()
    liberer = threading.Event()
    reponses = [[term("t1", "idle")], [term("t1", "blocked")]]
    n = {"i": 0}

    def request(method, params=None, timeout=100.0):
        i = n["i"]
        n["i"] += 1
        if i == 0:
            entree.set()
            liberer.wait(5)  # la 1re réponse arrive en retard
        return {"terminals": reponses[i]}

    hc.request = request
    a = threading.Thread(target=b.refresh)
    a.start()
    assert entree.wait(5)
    c = threading.Thread(target=b.refresh)
    c.start()
    liberer.set()
    a.join(5)
    c.join(5)
    assert b.snapshot()[0].state == "blocked"
    assert len(got) == 1


def test_rafale_evenements_coalescee():
    import threading
    hc, b = board([term("t1", "idle")])
    entree = threading.Event()
    liberer = threading.Event()
    avant = hc.calls
    orig = hc.request

    def lent(method, params=None, timeout=100.0):
        entree.set()
        liberer.wait(5)
        return orig(method, params, timeout)

    hc.request = lent
    t = threading.Thread(target=hc.fire)
    t.start()
    assert entree.wait(5)
    for _ in range(20):
        hc.fire()  # pendant le refresh lent
    liberer.set()
    t.join(5)
    assert hc.calls - avant <= 2


def test_pid_est_celui_du_processus_au_premier_plan():
    t = term("t1", "idle")
    t["pid"] = 5
    t["foreground"]["pid"] = 77
    _, b = board([t])
    assert b.snapshot()[0].pid == 77


# -- v2 : depuis, modèle, dossier court, sous-agents --

def _assistant(model, text="x"):
    import json
    return json.dumps({"type": "assistant", "message": {"model": model, "content": [
        {"type": "text", "text": text}]}}) + "\n"


def test_since_ms_lu_dans_status_ms():
    t = term("t1", "working")
    t["status_ms"] = 1794683980210
    _, b = board([t])
    assert b.snapshot()[0].since_ms == 1794683980210
    _, b = board([term("t2", None)])
    assert b.snapshot()[0].since_ms is None


def test_modele_depuis_argv(tmp_path):
    t = term("t1", "idle")
    t["foreground"]["argv"] = ["claude", "--model", "sonnet", "--resume"]
    t2 = term("t2", "idle")
    t2["foreground"]["argv"] = ["claude", "--model=opus"]
    hc = FakeHc([t, t2])
    b = AgentBoard(hc, layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert {a.address: a.model for a in b.snapshot()} == {"t1": "sonnet", "t2": "opus"}


def test_modele_depuis_le_transcript_sinon_null(tmp_path):
    import json
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "10.json").write_text(
        json.dumps({"sessionId": "s1", "cwd": "/work/proj"}))
    d = tmp_path / ".claude" / "projects" / "-work-proj"
    d.mkdir(parents=True)
    j = d / "s1.jsonl"
    j.write_text(_assistant("claude-haiku-4-5") + _assistant("claude-opus-5-5")
                 + _assistant("<synthetic>") + json.dumps({"type": "user", "message": {}}) + "\n")
    hc = FakeHc([term("t1", "idle")])
    b = AgentBoard(hc, layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "claude-opus-5-5"  # la dernière ligne assistant réelle
    with j.open("a") as f:
        f.write(_assistant("claude-sonnet-4-6"))
    b.refresh()
    assert b.snapshot()[0].model == "claude-sonnet-4-6"  # relu quand le journal bouge
    j.unlink()
    b.refresh()
    assert b.snapshot()[0].model is None


def test_modele_null_pour_un_programme_non_claude(tmp_path):
    t = term("t1", None)
    t["foreground"].update(name="herdr", argv=["herdr"])
    hc = FakeHc([t])
    b = AgentBoard(hc, layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model is None


def test_dossier_court(tmp_path):
    from herdr_web.agents import short_dir
    repo = tmp_path / "my-project"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts" / "x").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "veille-litestream"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {repo}/.git/worktrees/veille-litestream\n")
    assert short_dir(str(repo)) == "my-project"
    assert short_dir(str(repo / "scripts" / "x")) == "my-project"
    assert short_dir(str(wt / "src")) == "my-project › veille-litestream"
    sans = tmp_path / "rien" / "ici"
    sans.mkdir(parents=True)
    assert short_dir(str(sans)) == "ici"  # hors dépôt : le dernier segment
    assert short_dir("/nulle/part/.claude/worktrees/wt1") == "part › wt1"  # motif sans disque
    assert short_dir("") == ""


def test_dir_calcule_par_le_tableau(tmp_path):
    repo = tmp_path / "herdr-web"
    (repo / ".git").mkdir(parents=True)
    hc = FakeHc([term("t1", "idle", cwd=str(repo))])
    b = AgentBoard(hc, layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].dir == "herdr-web"


def test_sous_agents_depuis_records():
    t = term("t1", "working")
    t["records"] = {"a2db": {"app": "claude-code", "state": "working", "title": "Relire"},
                    "b7f1": {"app": "claude-code", "state": "done", "title": None}}
    hc, b = board([t])
    assert b.snapshot()[0].subagents == [
        {"id": "a2db", "title": "Relire", "state": "working", "model": None, "context": None, "context_max": None,
         "role": "reviewer", "role_infere": True},
        {"id": "b7f1", "title": "", "state": "done", "model": None, "context": None, "context_max": None,
         "role": None, "role_infere": False}]
    t["records"] = None  # relus à chaque refresh
    hc.fire()
    assert b.snapshot()[0].subagents == []


# -- ajouts : branche git, machine --

class _Git:
    def __init__(self, sortie="master\n", rc=0):
        self.sortie, self.rc, self.appels = sortie, rc, []

    def __call__(self, argv, **kw):
        import subprocess
        self.appels.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.rc, self.sortie, "")


def test_branche_git_en_cache_30_s():
    from herdr_web.agents import BranchCache
    git, t = _Git(), {"now": 1000.0}
    bc = BranchCache(run=git, clock=lambda: t["now"])
    assert bc.get("/w/proj") == "master"
    assert git.appels[0][0] == ["git", "--no-optional-locks", "-C", "/w/proj", "rev-parse",
                                "--abbrev-ref", "HEAD"]
    assert "shell" not in git.appels[0][1] and git.appels[0][1]["timeout"] <= 5
    t["now"] += 29
    git.sortie = "autre\n"
    assert bc.get("/w/proj") == "master" and len(git.appels) == 1  # en cache
    t["now"] += 2
    assert bc.get("/w/proj") == "autre" and len(git.appels) == 2  # relu après 30 s


def test_branche_null_hors_depot_ou_en_echec():
    import subprocess
    from herdr_web.agents import BranchCache
    assert BranchCache(run=_Git("", rc=128)).get("/tmp") is None
    assert BranchCache(run=_Git()).get("") is None

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)
    assert BranchCache(run=boom).get("/w") is None


def test_branche_et_machine_sur_l_agent():
    import socket
    from herdr_web.agents import BranchCache
    hc = FakeHc([term("t1", "idle", cwd="/w/proj")])
    b = AgentBoard(hc, layout=lambda: None, home=Path("/nonexistent"),
                   branches=BranchCache(run=_Git("feat/x\n")))
    b.refresh()
    a = b.snapshot()[0]
    assert a.branch == "feat/x"
    assert a.machine == socket.gethostname().lower()


# -- point 25 : le modèle est le dernier réellement utilisé --

def _session(home, pid=10, sid="s1", lignes=""):
    import json
    (home / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "sessions" / f"{pid}.json").write_text(
        json.dumps({"sessionId": sid, "cwd": "/work/proj"}))
    d = home / ".claude" / "projects" / "-work-proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text(lignes)
    return d


def _avec_argv(argv):
    t = term("t1", "idle")
    t["foreground"]["argv"] = argv
    return t


def test_transcript_l_emporte_sur_argv(tmp_path):
    _session(tmp_path, lignes=_assistant("claude-sonnet-4-6"))
    b = AgentBoard(FakeHc([_avec_argv(["claude", "--model", "opus"])]), layout=lambda: None,
                   home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "claude-sonnet-4-6"


def test_transcript_vide_rend_l_argv(tmp_path):
    _session(tmp_path, lignes="")
    b = AgentBoard(FakeHc([_avec_argv(["claude", "--model", "opus"])]), layout=lambda: None,
                   home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "opus"


def test_ni_transcript_ni_argv_rend_null(tmp_path):
    _session(tmp_path, lignes="")
    b = AgentBoard(FakeHc([_avec_argv(["claude"])]), layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model is None


def test_journal_apparu_apres_coup_est_lu_sans_changement_de_session(tmp_path):
    # réserve de la revue UI v2 : un Claude neuf écrit sessions/<pid>.json tout de suite mais son
    # journal au premier message ; _session gardait None tant que le .json ne bougeait pas
    d = _session(tmp_path)
    (d / "s1.jsonl").unlink()
    b = AgentBoard(FakeHc([_avec_argv(["claude", "--model", "opus"])]), layout=lambda: None,
                   home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "opus"
    (d / "s1.jsonl").write_text(_assistant("claude-sonnet-4-6"))
    b.refresh()
    assert b.snapshot()[0].model == "claude-sonnet-4-6"


def test_modele_d_un_sous_agent_par_son_transcript(tmp_path):
    d = _session(tmp_path)
    sub = d / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a2db.jsonl").write_text(_assistant("claude-haiku-4-5"))
    t = term("t1", "working")
    t["records"] = {"a2db": {"state": "working", "title": "Relire"},
                    "zz": {"state": "working", "title": "sans fil"}}
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    assert [s["model"] for s in b.snapshot()[0].subagents] == ["claude-haiku-4-5", None]


def test_tache_shell_de_fond_marquee_shell(tmp_path):
    """hc range aussi dans `records` les commandes Bash lancées en fond (id « b… », mesuré le
    2026-11-21 : « Attendre la fin de la CI de la PR 36 ») : pas de journal de sous-agent, donc
    rien à ouvrir. Session résolue mais journal absent : rôle « shell », que la page ne lie pas."""
    d = _session(tmp_path)
    sub = d / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a2db.jsonl").write_text(_assistant("claude-haiku-4-5"))
    t = term("t1", "working")
    t["records"] = {"a2db": {"state": "working", "title": "Relire"},
                    "bympx1i41": {"state": "working", "title": "Attendre la fin de la CI de la PR 36"}}
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    subs = {s["id"]: s for s in b.snapshot()[0].subagents}
    assert (subs["a2db"]["role"], subs["a2db"]["role_infere"]) == ("reviewer", True)
    assert (subs["bympx1i41"]["role"], subs["bympx1i41"]["role_infere"]) == ("shell", False)


def test_decompte_par_modele():
    from herdr_web.agents import Agent, model_counts

    def ag(state, model, subs=()):
        return Agent("t", None, "", "/w", "claude-code", 1, state, None, "", None, None, 0,
                     model=model, subagents=list(subs))
    agents = [
        ag("working", "claude-opus-5-5", [{"id": "a", "title": "", "state": "working",
                                           "model": "claude-haiku-4-5"},
                                          {"id": "b", "title": "", "state": "done",
                                           "model": "claude-haiku-4-5"}]),
        ag("blocked", "opus"),
        ag("idle", "claude-sonnet-4-6"),
        ag("unknown", None),  # terminal sans état : pas un agent actif
        ag("working", "gpt-x"),
    ]
    assert model_counts(agents) == {"opus": 2, "sonnet": 1, "haiku": 1, "gpt-x": 1}


# -- point 26 : rôle depuis le template hc --

def test_role_depuis_le_template():
    ts = []
    for i, tpl in enumerate(["maker", "concepteur", "checker", "checker-conception", "reviewer", None]):
        t = term(f"t{i}", "working", name=f"n{i}")
        t["template"] = tpl
        ts.append(t)
    shell = term("t9", None, name="n9")
    shell["foreground"].update(name="zsh", argv=["zsh"])
    _, b = board(ts + [shell])
    roles = {a.name: a.role for a in b.snapshot()}
    assert roles == {"n0": "maker", "n1": "concepteur", "n2": "checker", "n3": "checker-conception",
                     "n4": "reviewer", "n5": "claude", "n9": "terminal"}  # 26b ; point 39 : template review


# -- point 26b : activité « shell » tant qu'un Bash attend son résultat --

def _tool_use(i, name="Bash"):
    import json
    return json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
        {"type": "tool_use", "id": f"u{i}", "name": name, "input": {"command": "x"}}]}}) + "\n"


def _tool_result(i):
    import json
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"u{i}", "content": "ok"}]}}) + "\n"


def _activite(tmp_path, lignes):
    _session(tmp_path, lignes=lignes)
    b = AgentBoard(FakeHc([term("t1", "working")]), layout=lambda: None, home=tmp_path,
                   branches=_SansGit())
    b.refresh()
    return b.snapshot()[0].activity


def test_activite_shell_pendant_un_bash_sans_resultat(tmp_path):
    assert _activite(tmp_path, _tool_use(1) + _tool_result(1) + _tool_use(2)) == "shell"


def test_activite_nulle_quand_le_bash_a_rendu(tmp_path):
    assert _activite(tmp_path, _tool_use(1) + _tool_result(1)) is None


def test_activite_nulle_pour_un_autre_outil_en_cours(tmp_path):
    assert _activite(tmp_path, _tool_use(1, "Read")) is None


def test_activite_shell_avec_deux_outils_paralleles(tmp_path):
    lignes = _tool_use(1) + _tool_use(2, "Read") + _tool_result(2)
    assert _activite(tmp_path, lignes) == "shell"


def test_activite_nulle_pour_un_terminal(tmp_path):
    t = term("t1", None)
    t["foreground"].update(name="zsh", argv=["zsh"])
    _session(tmp_path, lignes=_tool_use(1))
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].activity is None


def test_colonnes_du_terminal():
    t = term("t1", None)
    t["cols"] = 166
    _, b = board([t, term("t2", None)])
    assert {a.address: a.cols for a in b.snapshot()} == {"t1": 166, "t2": None}


def test_parent_hc_lu_present_absent_vide_ou_mal_forme():
    a, b_, c, d = term("t1", None), term("t2", None), term("t3", None), term("t4", None)
    b_["parent"] = "t1"   # lancé par t1 (hc run)
    c["parent"] = ""      # vide : pas de parent
    d["parent"] = 7       # mal formé : pas de parent
    _, b = board([a, b_, c, d])  # t1 n'a pas la clé du tout
    assert {x.address: x.parent for x in b.snapshot()} == {"t1": None, "t2": "t1", "t3": None, "t4": None}


def test_activite_shell_si_un_bash_anterieur_attend_encore(tmp_path):
    # deux Bash en parallèle, le second a rendu, le premier tourne encore
    assert _activite(tmp_path, _tool_use(1) + _tool_use(2) + _tool_result(2)) == "shell"


# -- fix round 1 --

def test_banc_sans_faux_git_tomberait_sur_la_garde(tmp_path):
    """Témoin de la fixture : un AgentBoard sans branches= lancerait git, et la garde le refuse."""
    import pytest as _pytest
    b = AgentBoard(FakeHc([term("t1", "idle", cwd=str(tmp_path))]), layout=lambda: None, home=tmp_path)
    with _pytest.raises(AssertionError, match="processus réel"):
        b.refresh()


def test_modele_trouve_au_dela_de_la_fenetre_de_fin(tmp_path):
    """m3 : la dernière ligne assistant est plus loin que 256 Ko de la fin."""
    import json
    remplissage = "".join(json.dumps({"type": "user", "message": {"content": "x" * 1000}}) + "\n"
                          for _ in range(400))  # ~400 Ko sans modèle
    _session(tmp_path, lignes=_assistant("claude-opus-5-5") + remplissage)
    b = AgentBoard(FakeHc([_avec_argv(["claude", "--model", "sonnet"])]), layout=lambda: None,
                   home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "claude-opus-5-5"


def test_modele_connu_ne_regresse_pas(tmp_path, monkeypatch):
    """m3 : une fois vu, le modèle d'un transcript ne retombe pas sur l'argv ni sur None."""
    d = _session(tmp_path, lignes=_assistant("claude-opus-5-5"))
    b = AgentBoard(FakeHc([_avec_argv(["claude", "--model", "sonnet"])]), layout=lambda: None,
                   home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].model == "claude-opus-5-5"
    monkeypatch.setattr("herdr_web.agents.tail_info", lambda path: (None, None, None))
    with (d / "s1.jsonl").open("a") as f:
        f.write('{"type": "user", "message": {"content": "suite"}}\n')
    b.refresh()
    assert b.snapshot()[0].model == "claude-opus-5-5"


def test_sous_agent_lu_dans_le_dossier_de_session_sans_balayage(tmp_path, monkeypatch):
    """m4 : le fil d'un sous-agent se trouve depuis le dossier de la session parente,
    sans balayer tous les projets à chaque refresh."""
    d = _session(tmp_path)
    sub = d / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a2db.jsonl").write_text(_assistant("claude-haiku-4-5"))

    def balayage(*a, **k):
        raise AssertionError("resolve_subagent (glob de tous les projets) appelé au refresh")
    monkeypatch.setattr("herdr_web.agents.resolve_subagent", balayage, raising=False)
    t = term("t1", "working")
    t["records"] = {"a2db": {"state": "working", "title": "Relire"}}
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    assert b.snapshot()[0].subagents[0]["model"] == "claude-haiku-4-5"


def test_cache_des_transcripts_elague(tmp_path):
    """m4 : un transcript qui n'est plus référencé sort du cache."""
    _session(tmp_path, lignes=_assistant("claude-opus-5-5"))
    hc = FakeHc([term("t1", "idle")])
    b = AgentBoard(hc, layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    assert len(b._infos) == 1
    hc.terminals = []
    b.refresh()
    assert b._infos == {}


def test_records_viennent_de_terminal_list_sans_autre_appel():
    """I1 (contrat actuel) : les sous-agents viennent des records de terminal.list ; leur absence
    donne une liste vide, sans erreur et sans autre appel à hc."""
    t = term("t1", "working")
    t["records"] = {"a": {"state": "working", "title": "x"}}
    sans = term("t2", "working")
    sans.pop("records", None)
    hc = FakeHc([t, sans])  # FakeHc.request n'accepte que terminal.list
    b = AgentBoard(hc, layout=lambda: None, home=Path("/nonexistent"), branches=_SansGit())
    b.refresh()
    by = {a.address: a for a in b.snapshot()}
    assert [s["id"] for s in by["t1"].subagents] == ["a"]
    assert by["t2"].subagents == []
    assert hc.calls == 1


# -- point 35 : rôle d'un sous-agent in-process déduit du premier mot de son titre --

@pytest.mark.parametrize("titre,role", [
    ("Implement herdr-web UI v2", "maker"), ("implementer la page", "maker"), ("Fix the badge", "maker"),
    ("Corriger le menu", "maker"), ("Build it", "maker"), ("Écrire le rapport", "maker"),
    ("ecrire x", "maker"), ("Write tests", "maker"), ("Refactor agents", "maker"),
    ("Review fix round 1", "reviewer"), ("Re-review herdr-web", "reviewer"), ("Reviewer UI", "reviewer"),
    ("Check the diff", "checker"), ("Checker: spec", "checker"), ("Verify the merge", "checker"),
    ("Vérifier la CI", "checker"), ("verifier", "checker"), ("Relire le plan", "reviewer"),
    ("AUDIT parc", "reviewer"), ("Relecture finale", "reviewer"), ("relecture", "reviewer"),
    ("Revue du diff", "reviewer"), ("revue", "reviewer"),  # comme le mod agents-panel
    ("Explore the codebase", None), ("Plan herdr-web", None), ("", None), ("   ", None), (None, None),
    ("Reviewing everything", None),  # mot entier, pas un préfixe
])
def test_role_infere_d_un_sous_agent(titre, role):
    from herdr_web.agents import role_inferred
    assert role_inferred(titre) == role


# -- point 39 : rôle reviewer --

@pytest.mark.parametrize("template,nom,claude,role", [
    ("review", None, True, "reviewer"), ("code-reviewer", None, True, "reviewer"),
    (None, "Review herdr-web", True, "reviewer"), ("maker", "re-review", True, "reviewer"),
    ("checker", "verif", True, "checker"), ("maker", None, True, "maker"),
    (None, "relecteur", True, "claude"), (None, None, False, "terminal"),
    # rattrapage : « review » en mot entier — un shell « preview » n'est pas un reviewer
    (None, "preview", False, "terminal"), ("maker", "vite preview", True, "maker"),
    (None, "npm run preview", True, "claude"), ("previewer", None, True, "claude"),
])
def test_role_reviewer_par_template_ou_nom(template, nom, claude, role):
    from herdr_web.agents import role_of
    assert role_of(template, claude, nom) == role


def test_role_reviewer_dans_le_tableau():
    t = term("t1", "working")
    t["name"] = "review-ui"
    _, b = board([t])
    assert b.snapshot()[0].role == "reviewer"


# -- suivi du contexte : usage du dernier message assistant --

def _usage(model, inp, cache_read=0, cache_creation=0, sidechain=False):
    import json
    d = {"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "x"}],
         "usage": {"input_tokens": inp, "cache_read_input_tokens": cache_read,
                   "cache_creation_input_tokens": cache_creation, "output_tokens": 999}}}
    if sidechain:
        d["isSidechain"] = True
    return json.dumps(d) + "\n"


def test_contexte_du_dernier_message_assistant(tmp_path):
    from herdr_web.agents import tail_info
    p = tmp_path / "s.jsonl"
    p.write_text(_usage("claude-opus-5-5", 10, 300_000, 5_000) + _usage("claude-opus-5-5", 2, 400_000, 66_000)
                 + '{"type": "user", "message": {"content": "suite"}}\n')
    assert tail_info(p)[2] == 466_002  # entrée + cache lu + cache créé ; la sortie ne compte pas


def test_contexte_ignore_une_ligne_de_sous_agent_dans_le_fil_principal(tmp_path):
    from herdr_web.agents import tail_info
    p = tmp_path / "s.jsonl"
    p.write_text(_usage("claude-opus-5-5", 1, 200_000) + _usage("claude-sonnet-5-5", 1, 9_000, sidechain=True))
    assert tail_info(p)[2] == 200_001
    q = tmp_path / "agent-x.jsonl"  # fil d'un sous-agent : toutes ses lignes sont isSidechain
    q.write_text(_usage("claude-sonnet-5-5", 1, 9_000, sidechain=True))
    assert tail_info(q)[2] == 9_001


def test_contexte_trouve_au_dela_de_la_fenetre_de_fin(tmp_path):
    from herdr_web.agents import tail_info
    p = tmp_path / "s.jsonl"
    p.write_text(_usage("claude-opus-5-5", 1, 123_000)
                 + '{"type": "user", "message": {"content": "' + "y" * 600_000 + '"}}\n')
    assert tail_info(p)[2] == 123_001


def test_contexte_absent_sans_usage(tmp_path):
    from herdr_web.agents import tail_info
    p = tmp_path / "s.jsonl"
    p.write_text(_assistant("claude-opus-5-5"))
    assert tail_info(p)[2] is None


def test_contexte_et_fenetre_de_compaction_dans_l_agent_et_ses_sous_agents(tmp_path):
    import json
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"autoCompactWindow": 500000, "autoCompactEnabled": True}))
    d = _session(tmp_path, lignes=_usage("claude-opus-5-5", 3, 466_000))
    sub = d / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a2db.jsonl").write_text(_usage("claude-sonnet-5-5", 1, 40_000, sidechain=True))
    t = term("t1", "working")
    t["records"] = {"a2db": {"state": "working", "title": "Relire"}}
    t["foreground"]["pid"] = 10
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    a = b.snapshot()[0]
    assert (a.context, a.context_max) == (466_003, 500_000)
    assert (a.subagents[0]["context"], a.subagents[0]["context_max"]) == (40_001, 500_000)


def test_fenetre_de_compaction(tmp_path):
    import json
    from herdr_web.agents import compact_window
    s = tmp_path / ".claude" / "settings.json"
    assert compact_window(tmp_path) == 1_000_000  # pas de réglage : la fenêtre du modèle
    s.parent.mkdir()
    s.write_text(json.dumps({"autoCompactWindow": 500000}))
    assert compact_window(tmp_path) == 500_000
    s.write_text(json.dumps({"autoCompactWindow": 500000, "autoCompactEnabled": False}))
    assert compact_window(tmp_path) == 1_000_000  # compaction coupée : seule la fenêtre borne
    s.write_text("{pas du json")
    assert compact_window(tmp_path) == 1_000_000


def _catalogue(home, **fenetres):
    import json
    d = home / ".claude" / "cache" / "model-catalog"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x-cc.json").write_text(json.dumps({"catalog": {"config": {"models": [
        {"id": m, "runtime": {"max_input_tokens": n}} for m, n in fenetres.items()]}}}))


def test_seuil_de_compaction_borne_par_la_fenetre_du_modele(tmp_path):
    import json
    from herdr_web.agents import compact_window, fenetre_modele
    _catalogue(tmp_path, **{"claude-opus-5-5": 1_000_000, "claude-haiku-4-5-20251001": 200_000})
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 500000}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 500_000   # réglage sous la fenêtre
    assert compact_window(tmp_path, None, "claude-haiku-4-5-20251001") == 200_000  # fenêtre sous le réglage
    assert compact_window(tmp_path, None, "claude-inconnu") == 500_000     # hors catalogue : repli FENETRE
    assert compact_window(tmp_path) == 500_000
    assert fenetre_modele("claude-haiku-4-5-20251001[1m]", tmp_path) == 200_000  # suffixe d'argv ignoré
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"autoCompactEnabled": False}))
    assert compact_window(tmp_path, None, "claude-haiku-4-5-20251001") == 200_000  # compaction coupée
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000
    (tmp_path / ".claude" / "cache" / "model-catalog" / "x-cc.json").write_text("{pas du json")
    assert compact_window(tmp_path, None, "claude-haiku-4-5-20251001") == 1_000_000  # illisible : rien d'inventé


def test_reglages_du_projet_priment_sur_ceux_de_l_utilisateur(tmp_path):
    import json
    from herdr_web.agents import compact_window
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    (proj / "sous").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (proj / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 500000}))
    assert compact_window(home, str(proj / "sous")) == 500_000
    (proj / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 300000}))
    assert compact_window(home, str(proj / "sous")) == 300_000   # remonte jusqu'au projet
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 250000}))
    assert compact_window(home, str(proj)) == 250_000             # local avant partagé
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"autoCompactEnabled": False}))
    assert compact_window(home, str(proj)) == 1_000_000           # compaction coupée par le projet


def test_seuil_par_modele_prime_sur_la_cle_globale(tmp_path):
    """/autocompact écrit modelSettings.<modèle>.autoCompactWindow ; la clé globale reste à côté."""
    import json
    from herdr_web.agents import compact_window
    _catalogue(tmp_path, **{"claude-opus-5-5": 1_000_000, "claude-haiku-4-5-20251001": 200_000})
    s = tmp_path / ".claude" / "settings.json"
    s.write_text(json.dumps({"autoCompactWindow": 500000,
                             "modelSettings": {"claude-opus-5-5": {"effortLevel": "high", "autoCompactWindow": 700000}}}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 700_000            # modèle présent
    assert compact_window(tmp_path, None, "claude-opus-5-5[1m]") == 700_000        # spelling [1m]
    assert compact_window(tmp_path, None, "claude-haiku-4-5-20251001") == 200_000  # autre modèle : global, borné
    assert compact_window(tmp_path, None, "claude-sonnet-5-5") == 500_000          # autre modèle : global
    assert compact_window(tmp_path) == 500_000                                     # modèle inconnu : global
    s.write_text(json.dumps({"modelSettings": {"claude-opus-5-5": {"effortLevel": "high"}}}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000          # pas d'autoCompactWindow
    s.write_text(json.dumps({"autoCompactWindow": 500000,
                             "modelSettings": {"claude-opus-5-5": {"autoCompactWindow": "auto"}}}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000          # "auto" : fenêtre du modèle
    s.write_text(json.dumps({"autoCompactWindow": 500000, "autoCompactEnabled": False,
                             "modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 700000}}}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000          # désactivée
    s.write_text(json.dumps({"modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 2_000_000}}}))
    _catalogue(tmp_path, **{"claude-opus-5-5": 1_000_000})
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000          # borné par la fenêtre
    s.write_text(json.dumps({"modelSettings": "pas un dict"}))
    assert compact_window(tmp_path, None, "claude-opus-5-5") == 1_000_000          # illisible : rien d'inventé


def test_couche_projet_prime_meme_sur_la_cle_par_modele_utilisateur(tmp_path):
    import json
    from herdr_web.agents import compact_window
    home, proj = tmp_path / "home", tmp_path / "proj"
    (home / ".claude").mkdir(parents=True)
    (proj / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 700000}}}))
    assert compact_window(home, str(proj), "claude-opus-5-5") == 700_000
    (proj / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 300000}))
    assert compact_window(home, str(proj), "claude-opus-5-5") == 300_000           # projet global avant user par modèle
    (proj / ".claude" / "settings.local.json").write_text(json.dumps(
        {"modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 250000}}}))
    assert compact_window(home, str(proj), "claude-opus-5-5") == 250_000


def test_chaque_agent_a_la_fenetre_de_son_modele(tmp_path):
    import json
    _catalogue(tmp_path, **{"claude-opus-5-5": 1_000_000, "claude-haiku-4-5-20251001": 200_000})
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 500000}))
    d = _session(tmp_path, lignes=_usage("claude-haiku-4-5-20251001", 3, 150_000))
    sub = d / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a2db.jsonl").write_text(_usage("claude-opus-5-5", 1, 40_000, sidechain=True))
    t = term("t1", "working")
    t["records"] = {"a2db": {"state": "working", "title": "Relire"}}
    t["foreground"]["pid"] = 10
    b = AgentBoard(FakeHc([t]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b.refresh()
    a = b.snapshot()[0]
    assert a.context_max == 200_000                       # Haiku compacte à 200k, pas à 500k
    assert a.subagents[0]["context_max"] == 500_000       # le sous-agent Opus suit le réglage


def test_cwd_reel_du_processus_prime_sur_celui_de_hc(tmp_path):
    """t1c annonçait un worktree supprimé : la branche se lit dans le dossier réel du processus."""
    vrai = tmp_path / "clone"
    vrai.mkdir()
    proc = tmp_path / "proc"
    (proc / "10").mkdir(parents=True)
    (proc / "10" / "cwd").symlink_to(vrai)
    t = term("t1", "idle")
    t["foreground"]["pid"] = 10
    t["cwd"] = str(tmp_path / "worktree-supprime")
    t2 = term("t2", "idle")  # pas de /proc lisible : repli sur le cwd de hc
    t2["foreground"]["pid"] = 999
    t2["cwd"] = "/work/proj"
    b = AgentBoard(FakeHc([t, t2]), layout=lambda: None, home=tmp_path, branches=_SansGit())
    b._proc = proc
    b.refresh()
    assert {a.address: a.cwd for a in b.snapshot()} == {"t1": str(vrai), "t2": "/work/proj"}


def test_spaces_portent_leurs_onglets_avec_id():
    _, b = board([term("t1", "working")], layout=lambda: LAYOUT)
    assert b.spaces()[0]["tabs"] == [{"id": "tb1", "name": "build"}, {"id": "tb2", "name": "shell"}]


# -- harmonie des zones Agents (spec du 2026-11-15) --

@pytest.mark.parametrize("labels,attendu", [({"herdr.pinned": "1"}, True), ({"herdr.pinned": ""}, False),
                                            ({}, False), (None, False), ("x", False)])
def test_epingle_lue_dans_le_label_herdr_pinned(labels, attendu):
    t = term("t1", "idle")
    t["labels"] = labels
    _, b = board([t])
    assert b.snapshot()[0].pinned is attendu


def test_famille_fable_reconnue():
    from herdr_web.agents import family, model_counts
    assert family("claude-fable-5-2") == "fable"
    _, b = board([term("t1", "working")])
    a = b.snapshot()[0]
    a.model = "claude-fable-5-2"
    assert model_counts([a]) == {"fable": 1}
