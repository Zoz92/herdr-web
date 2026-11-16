"""Point 34 : agents du menu « + » détectés comme herdr-app (command -v dans le shell de l'utilisateur)."""
import json
import subprocess

from herdr_web.known_agents import KnownAgents


class FakeShell:
    def __init__(self, found, rc=0, boom=False):
        self.found, self.rc, self.boom, self.appels = found, rc, boom, []

    def __call__(self, argv, **kw):
        self.appels.append((argv, kw))
        if self.boom:
            raise subprocess.TimeoutExpired(argv, 1)
        noms = argv[4:]
        out = "bruit du .zshrc\n" + "".join(
            f"@@herdr@@{n}\t{self.found[n]}\n" for n in noms if n in self.found)
        return subprocess.CompletedProcess(argv, self.rc, out, "")


def ids(k):
    return [(a["id"], a["installed"]) for a in k.list()]


def test_liste_et_installation_comme_le_menu_de_herdr_app(tmp_path):
    sh = FakeShell({"claude": "/h/.local/bin/claude", "grok": "/h/.grok/bin/grok"})
    k = KnownAgents(home=tmp_path, run=sh, shell="/bin/zsh")
    assert ids(k) == [("claude", True), ("opencode", False), ("grok", True), ("codex", False),
                      ("pi", False)]
    argv, kw = sh.appels[0]
    assert argv[:2] == ["/bin/zsh", "-ic"] and "shell" not in kw
    assert argv[4:] == ["claude", "opencode", "grok", "codex", "pi"]  # en arguments, pas dans le script
    assert "claude" not in argv[2]
    inst = k.installed()
    assert inst["grok"]["argv"] == ["/h/.grok/bin/grok"]  # chemin absolu pour le core


def test_codex_sans_demon(tmp_path):
    k = KnownAgents(home=tmp_path, run=FakeShell({"codex": "/x/codex"}), shell="/bin/zsh")
    assert k.installed()["codex"]["argv"] == ["/x/codex", "--no-daemon"]


def test_une_fonction_du_shell_passe_par_ce_shell(tmp_path):
    k = KnownAgents(home=tmp_path, run=FakeShell({"pi": "pi"}), shell="/bin/zsh")
    assert k.installed()["pi"]["argv"] == ["/bin/zsh", "-ic", '"$@"', "herdr-web", "pi"]


def test_cache_une_minute(tmp_path):
    t = [0.0]
    sh = FakeShell({})
    k = KnownAgents(home=tmp_path, run=sh, shell="/bin/zsh", clock=lambda: t[0], fond=lambda f: f())
    k.list(); k.list()
    t[0] = 59
    k.list()
    assert len(sh.appels) == 1
    t[0] = 61
    k.list()
    assert len(sh.appels) == 2


def test_perimee_rendue_tout_de_suite_et_relue_en_fond(tmp_path):
    # réserve de la revue UI v2 : zsh -ic tournait dans le fil de la requête (≤ 15 s d'attente)
    t, jobs = [0.0], []
    sh = FakeShell({"claude": "/b/claude"})
    k = KnownAgents(home=tmp_path, run=sh, shell="/bin/zsh", clock=lambda: t[0], fond=jobs.append)
    assert k.installed()["claude"]["argv"] == ["/b/claude"]  # premier appel : attend le shell
    sh.found = {"grok": "/b/grok"}
    t[0] = 61
    assert "claude" in k.installed() and len(sh.appels) == 1  # périmée, rendue sans attendre
    k.list()
    assert len(jobs) == 1  # une seule relecture en vol
    jobs.pop()()
    assert set(k.installed()) == {"grok"} and len(sh.appels) == 2
    k.list()
    assert jobs == []  # fraîche : rien à relire


def test_un_alias_ne_passe_pas_pour_installe(tmp_path):
    # réserve de la revue UI v2 : `command -v` rend « alias x=… » sous zsh, et "$@" ne développe
    # pas un alias : l'agent était proposé puis ne se lançait pas
    k = KnownAgents(home=tmp_path, run=FakeShell({"claude": "alias claude='claude --x'"}),
                    shell="/bin/zsh")
    assert "claude" not in k.installed()


def test_le_script_ecarte_l_alias_et_garde_le_programme_masque():
    import shutil
    from herdr_web.known_agents import CHECK_SCRIPT
    zsh = shutil.which("zsh")
    if not zsh:
        import pytest
        pytest.skip("zsh absent")
    r = subprocess.run([zsh, "-c", "alias zzagent='echo x'; alias ls='ls --color'\n" + CHECK_SCRIPT,
                        "herdr-web", "zzagent", "ls"], capture_output=True, text=True, timeout=15)
    lignes = [x for x in r.stdout.splitlines() if x.startswith("@@herdr@@")]
    assert len(lignes) == 1 and lignes[0].startswith("@@herdr@@ls\t/")


def test_programs_json_ajoute_et_remplace(tmp_path):
    d = tmp_path / ".config" / "herdr-app"
    d.mkdir(parents=True)
    (d / "programs.json").write_text(json.dumps({"programs": {
        "claude": {"run": ["my-claude"]},
        "claude-alt": {"name": "Claude (alt)", "run": ["my-claude-alt"]},
        "lazygit": {"name": "Git"},  # pas de run : pas un agent
        "Mauvais Id": {"run": ["x"]},
        "evil": {"run": ["rm -rf /"]},  # un nom de programme, pas une ligne de shell
        "opencode": {"name": "OC"}}}))  # change le nom, garde le run
    sh = FakeShell({"my-claude": "/b/my-claude", "my-claude-alt": "/b/alt"})
    k = KnownAgents(home=tmp_path, run=sh, shell="/bin/zsh")
    by = {a["id"]: a for a in k.list()}
    assert set(by) == {"claude", "opencode", "grok", "codex", "pi", "claude-alt"}
    assert by["claude"]["argv"] == ["/b/my-claude"] and by["claude-alt"]["name"] == "Claude (alt)"
    assert by["opencode"]["name"] == "OC"


def test_repli_sur_which_si_le_shell_ne_repond_pas(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))  # seul ~/.local/bin (le faux home) reste
    b = tmp_path / ".local" / "bin"
    b.mkdir(parents=True)
    (b / "claude").write_text("#!/bin/sh\n")
    (b / "claude").chmod(0o755)
    k = KnownAgents(home=tmp_path, run=FakeShell({}, boom=True), shell="/bin/zsh")
    assert k.installed()["claude"]["argv"] == [str(b / "claude")]
