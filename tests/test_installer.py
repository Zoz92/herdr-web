"""Installeur : jamais le vrai bw-unlocked, nix ni systemctl — points de sortie remplacés."""
import base64
import importlib.util
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "installer", Path(__file__).resolve().parent.parent / "deploy/installer.py")
inst = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inst)

ORG, COL = "org-1", "col-1"


@pytest.fixture(autouse=True)
def no_real_exit(monkeypatch):
    """Rien ne peut atteindre un vrai processus : tout subprocess lève."""
    def boom(*a, **k):
        raise AssertionError(f"subprocess réel atteint : {a[:1]}")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


class FakeBw:
    """Enregistre les appels et l'état du disque à chaque appel ; rend des réponses factices."""

    def __init__(self, home, existing=None, tamper=False, drop_field=False, fail_on=None, list_out=None):
        self.home, self.calls, self.disk = Path(home), [], []
        self.existing, self.tamper, self.drop_field = existing or [], tamper, drop_field
        self.stored = None
        self.fail_on, self.list_out = fail_on, list_out

    def __call__(self, args):
        self.calls.append(list(args))
        self.disk.append((args[0], (self.home / ".config/herdr-web/env").exists()))
        if self.fail_on and args[0] == self.fail_on[0] and args[1] == self.fail_on[1]:
            raise inst.Refus(f"bw-unlocked {args[0]} {args[1]} : rc=1", 2)
        if args == ["sync"]:
            return "Syncing complete."
        if args[:2] == ["list", "items"] and self.list_out is not None:
            return self.list_out
        if args[:3] == ["get", "template", "item"]:
            return json.dumps({"type": 1, "name": "", "login": {}, "fields": [], "folderId": "x"})
        if args[:2] == ["list", "items"]:
            return json.dumps(self.existing)
        if args[:2] == ["create", "item"]:
            self.stored = json.loads(base64.b64decode(args[2]))
            return json.dumps({"id": "item-1", **self.stored})
        if args[:2] == ["get", "item"]:
            back = json.loads(json.dumps(self.stored))
            if self.tamper:
                back["fields"][0]["value"] += "x"
            if self.drop_field:
                back["fields"] = back["fields"][:1]
            return json.dumps(back)
        raise AssertionError(f"appel bw imprévu : {args}")


class FakeRun:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, cwd=None):
        self.cmds.append(list(cmd))
        return 0


def which_all(name):
    return f"/fake/{name}"


def fake_repo(home):
    """Dépôt factice À CÔTÉ du HOME jetable : le vrai dépôt peut être un worktree (.claude/worktrees),
    que l'installeur refuse de servir — d'où, avant, 19 échecs dès que la suite tournait en worktree."""
    repo = Path(home).parent / f"{Path(home).name}-repo"
    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    unit = repo / "deploy/herdr-web.service"
    if not unit.exists():
        unit.write_text((Path(inst.REPO) / "deploy/herdr-web.service").read_text())
    return repo


def go(home, bw, run, *extra, out=None, vault=True, nixos=True, which=None):
    """--go dans un HOME jetable ; nixos=True simule NixOS (PATH du gabarit gardé)."""
    lines = [] if out is None else out
    coffre = ["--org-id", ORG, "--collection-id", COL] if vault else []
    nixos_bin = Path(home).parent / (f"{Path(home).name}-nixos" if nixos else "absent")
    if nixos:
        nixos_bin.mkdir(exist_ok=True)
    rc = inst.main(["--go", *coffre, "--cert-autosigne", *extra],
                   home=home, repo=fake_repo(home), bw=bw, run=run, which=which or which_all,
                   out=lines.append, nixos_bin=nixos_bin)
    return rc, lines


def test_simulation_n_ecrit_rien(tmp_path):
    before, lines = tree(tmp_path), []
    bw, run = FakeBw(tmp_path), FakeRun()
    rc = inst.main([], home=tmp_path, bw=bw, run=run, which=which_all, out=lines.append)
    assert rc == 0 and tree(tmp_path) == before == []
    assert bw.calls == [] and run.cmds == []
    assert any("DRY RUN" in l for l in lines)


def test_argument_inconnu_refuse(tmp_path):
    with pytest.raises(SystemExit) as e:
        inst.main(["--nimporte"], home=tmp_path)
    assert e.value.code == 2


def test_fonctions_qui_ecrivent_refusent_sans_go(tmp_path):
    ch = inst.Chemins(tmp_path)
    for call in (lambda: inst.write_private(ch.env, "x", False),
                 lambda: inst.write_env({}, "b", "h", "s", ch, False),
                 lambda: inst.make_selfsigned(ch, False),
                 lambda: inst.install_unit(ch, False),
                 lambda: inst.vault_store({}, ORG, COL, FakeBw(tmp_path), False)):
        with pytest.raises(RuntimeError):
            call()
    assert tree(tmp_path) == []


def test_env_existant_ne_regenere_rien(tmp_path):
    env = tmp_path / ".config/herdr-web/env"
    env.parent.mkdir(parents=True)
    env.write_text("HERDR_WEB_TOKEN=garde\n")
    bw, run = FakeBw(tmp_path), FakeRun()
    rc, _ = go(tmp_path, bw, run)
    assert rc == 0
    assert bw.calls == []
    assert env.read_text() == "HERDR_WEB_TOKEN=garde\n"
    assert (tmp_path / ".config/systemd/user/herdr-web.service").exists()


def test_empreinte_differente_rien_sur_le_disque(tmp_path):
    bw, run = FakeBw(tmp_path, tamper=True), FakeRun()
    rc, lines = go(tmp_path, bw, run)
    assert rc == 2 and any("UNDECIDABLE" in l for l in lines)
    assert not (tmp_path / ".config/herdr-web/env").exists()
    assert not (tmp_path / ".config/systemd/user/herdr-web.service").exists()
    assert run.cmds[-1][0] == "nix"  # ni daemon-reload ni enable


def test_champ_absent_au_coffre_rien_sur_le_disque(tmp_path):
    bw = FakeBw(tmp_path, drop_field=True)
    rc, lines = go(tmp_path, bw, FakeRun())
    assert rc == 2 and not (tmp_path / ".config/herdr-web/env").exists()


def test_succes_coffre_puis_disque(tmp_path):
    bw, run, lines = FakeBw(tmp_path), FakeRun(), []
    rc, _ = go(tmp_path, bw, run, out=lines)
    assert rc == 0
    # ordre : tout appel bw voit l'env ABSENT ; l'env existe après
    assert [a for a, _ in bw.disk] == ["sync", "list", "get", "create", "get"]
    assert all(existed is False for _, existed in bw.disk)
    env = tmp_path / ".config/herdr-web/env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert stat.S_IMODE(env.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / ".config/herdr-web/tls/key.pem").stat().st_mode) == 0o600
    # item : note sécurisée, organisation + collection, deux champs cachés
    item = bw.stored
    assert item["type"] == 2 and item["secureNote"] == {"type": 0} and item["name"] == "herdr-web"
    assert item["organizationId"] == ORG and item["collectionIds"] == [COL]
    assert [(f["name"], f["type"]) for f in item["fields"]] == [
        ("HERDR_WEB_TOKEN", 1), ("HERDR_WEB_VAPID_KEY", 1)]
    token = item["fields"][0]["value"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{48}", token)
    # aucune valeur imprimée
    assert not any(token in l or "BEGIN" in l for l in lines)
    # clés de l'env
    text = env.read_text()
    for k in ("HERDR_WEB_TOKEN", "HERDR_WEB_VAPID_KEY", "HERDR_WEB_BIND", "HERDR_WEB_CERT",
              "HERDR_WEB_KEY", "HERDR_WEB_STATE", "HERDR_WEB_HOSTS", "HERDR_WEB_VAPID_SUBJECT"):
        assert re.search(rf"^{k}=", text, re.M), k
    assert "HERDR_WEB_BIND=127.0.0.1:10370" in text
    # gestes systemd, dans l'ordre
    assert run.cmds[0][:2] == ["nix", "build"]
    assert run.cmds[-3:] == [["systemctl", "--user", "daemon-reload"],
                             ["systemctl", "--user", "enable", "--now", "herdr-web.service"],
                             ["systemctl", "--user", "restart", "herdr-web.service"]]
    unit = (tmp_path / ".config/systemd/user/herdr-web.service").read_text()
    assert "@REPO@" not in unit and f"PYTHONPATH={fake_repo(tmp_path)}" in unit


parse_env_systemd = inst.parse_env


def test_pem_de_lenv_est_accepte_par_le_serveur(tmp_path):
    from herdr_web.__main__ import make_push

    class Board:
        def on_blocked(self, cb):
            pass
    bw = FakeBw(tmp_path)
    go(tmp_path, bw, FakeRun())
    env = parse_env_systemd((tmp_path / ".config/herdr-web/env").read_text())
    assert env["HERDR_WEB_VAPID_KEY"] == bw.stored["fields"][1]["value"].rstrip()
    env["HERDR_WEB_STATE"] = str(tmp_path / "state")
    assert make_push(env, Board()) is not None


def test_item_deja_au_coffre_refuse(tmp_path):
    bw = FakeBw(tmp_path, existing=[{"name": "herdr-web"}, {"name": "herdr-web-autre"}])
    rc, lines = go(tmp_path, bw, FakeRun())
    assert rc != 0 and not [c for c in bw.calls if c[0] == "create"]
    assert not (tmp_path / ".config/herdr-web/env").exists()


@pytest.mark.parametrize("seul", [["--org-id", ORG], ["--collection-id", COL]])
def test_org_ou_collection_seule_refus_sans_rien_ecrire(tmp_path, seul):
    bw, run, lines = FakeBw(tmp_path), FakeRun(), []
    rc = inst.main(["--go", "--cert-autosigne", *seul], home=tmp_path, repo=fake_repo(tmp_path), bw=bw,
                   run=run, which=which_all, out=lines.append)
    assert rc == 1 and bw.calls == [] and run.cmds == []
    assert "go together" in "\n".join(lines)
    assert tree(tmp_path) == []


def test_certificat_absent_sans_option_refuse(tmp_path):
    lines = []
    rc = inst.main(["--go", "--org-id", ORG, "--collection-id", COL], home=tmp_path,
                   repo=fake_repo(tmp_path), bw=FakeBw(tmp_path), run=FakeRun(),
                   which=which_all, out=lines.append)
    assert rc == 1 and not (tmp_path / ".config/herdr-web/env").exists()


def test_certificat_autosigne_san(tmp_path):
    from cryptography import x509
    ch = inst.Chemins(tmp_path)
    inst.make_selfsigned(ch, True)
    cert = x509.load_pem_x509_certificate(ch.cert.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert [str(i) for i in san.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]
    assert stat.S_IMODE(ch.key.stat().st_mode) == 0o600


SECRET = "SECRETVALUE-do-not-print-0123456789"


def test_real_bw_en_echec_ne_cite_aucune_valeur(monkeypatch):
    class Done:
        returncode, stdout, stderr = 1, SECRET, SECRET
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(inst.Refus) as e:
        inst.real_bw(["create", "item", "b64"])
    assert SECRET not in str(e.value) and "rc=1" in str(e.value)


def test_create_en_echec_indecidable_audit(tmp_path):
    bw = FakeBw(tmp_path, fail_on=("create", "item"))
    rc, lines = go(tmp_path, bw, FakeRun())
    txt = "\n".join(lines)
    assert rc == 2 and "UNDECIDABLE" in txt and "list items --search herdr-web" in txt
    assert not (tmp_path / ".config/herdr-web/env").exists()


def test_get_en_echec_indecidable_audit_avec_id(tmp_path):
    class Bw(FakeBw):
        def __call__(self, args):
            if args[:2] == ["get", "item"]:
                raise inst.Refus("bw-unlocked get item : rc=1", 2)
            return super().__call__(args)
    rc, lines = go(tmp_path, Bw(tmp_path), FakeRun())
    txt = "\n".join(lines)
    assert rc == 2 and "UNDECIDABLE" in txt and "item-1" in txt and "audit the vault" in txt
    assert not (tmp_path / ".config/herdr-web/env").exists()


def test_liste_vide_indecidable_rien_cree(tmp_path):
    bw = FakeBw(tmp_path, list_out="")
    rc, lines = go(tmp_path, bw, FakeRun())
    assert rc == 2 and any("UNDECIDABLE" in l for l in lines)
    assert not [c for c in bw.calls if c[0] == "create"]


def test_ecriture_env_en_oserror_indecidable(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disque")
    monkeypatch.setattr(inst, "write_private", boom)
    rc, lines = go(tmp_path, FakeBw(tmp_path), FakeRun())
    assert rc == 2 and any("UNDECIDABLE" in l and "in the vault" in l for l in lines)


def test_certificat_a_moitie_pose_refuse(tmp_path):
    (tmp_path / ".config/herdr-web/tls").mkdir(parents=True)
    (tmp_path / ".config/herdr-web/tls/key.pem").write_text("k")
    bw = FakeBw(tmp_path)
    rc, lines = go(tmp_path, bw, FakeRun())
    assert rc == 1 and any("half installed" in l for l in lines) and bw.calls == []


def test_depot_sous_worktree_refuse(tmp_path):
    wt = tmp_path / "r/.claude/worktrees/x"
    wt.mkdir(parents=True)
    lines = []
    rc = inst.main(["--go", "--org-id", ORG, "--collection-id", COL, "--cert-autosigne"],
                   home=tmp_path / "h", repo=wt, bw=FakeBw(tmp_path), run=FakeRun(),
                   which=which_all, out=lines.append)
    assert rc == 1 and any("worktrees" in l for l in lines)
    assert not (tmp_path / "h").exists()


def env_existant(tmp_path):
    env = tmp_path / ".config/herdr-web/env"
    env.parent.mkdir(parents=True)
    env.write_text("HERDR_WEB_TOKEN=garde\nHERDR_WEB_VAPID_KEY='-----BEGIN X\nabc\n-----END X-----'\n"
                   "HERDR_WEB_BIND=127.0.0.1:10370\nHERDR_WEB_HOSTS=h:1\n")
    os.chmod(env, 0o600)
    return env


def test_env_existant_argument_different_simulation_l_imprime(tmp_path):
    env = env_existant(tmp_path)
    before, lines = env.read_text(), []
    rc = inst.main(["--bind", "100.64.0.10:10370"], home=tmp_path, bw=FakeBw(tmp_path),
                   run=FakeRun(), which=which_all, out=lines.append)
    assert rc == 0 and env.read_text() == before
    assert any("HERDR_WEB_BIND differs" in l for l in lines)


def test_env_existant_argument_different_go_reecrit_seulement_le_non_secret(tmp_path):
    env = env_existant(tmp_path)
    bw = FakeBw(tmp_path)
    rc, _ = go(tmp_path, bw, FakeRun(), "--bind", "100.64.0.10:10370", "--vapid-subject", "mailto:a@b")
    assert rc == 0 and bw.calls == []
    cur = inst.parse_env(env.read_text())
    assert cur["HERDR_WEB_BIND"] == "100.64.0.10:10370" and cur["HERDR_WEB_VAPID_SUBJECT"] == "mailto:a@b"
    assert cur["HERDR_WEB_TOKEN"] == "garde" and cur["HERDR_WEB_HOSTS"] == "h:1"
    assert cur["HERDR_WEB_VAPID_KEY"] == "-----BEGIN X\nabc\n-----END X-----"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600 and not (env.parent / "env.tmp").exists()


def test_env_existant_sans_argument_explicite_inchange(tmp_path):
    env = env_existant(tmp_path)
    before = env.read_text()
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun())
    assert rc == 0 and env.read_text() == before


def test_meme_pem_au_coffre_et_sur_le_disque(tmp_path):
    bw = FakeBw(tmp_path)
    go(tmp_path, bw, FakeRun())
    cur = inst.parse_env((tmp_path / ".config/herdr-web/env").read_text())
    for f in bw.stored["fields"]:
        assert inst.fp(cur[f["name"]]) == inst.fp(f["value"])


def test_unit_sans_cycle_et_sans_boucle_de_redemarrage():
    txt = (Path(inst.REPO) / "deploy/herdr-web.service").read_text()
    assert "RestartSteps=5" in txt and "RestartMaxDelaySec=60s" in txt
    assert "After=default.target" not in txt and "RestartPreventExitStatus=78" in txt
    unit_section = txt.split("[Service]")[0]
    assert "StartLimitIntervalSec=0" in unit_section


def restarts(run):
    return [c for c in run.cmds if "restart" in c]


def test_restart_si_env_reecrit_ou_unit_changee_pas_sinon(tmp_path):
    env_existant(tmp_path)
    go(tmp_path, FakeBw(tmp_path), FakeRun())  # pose la unit, sans argument explicite
    run = FakeRun()
    go(tmp_path, FakeBw(tmp_path), run, "--bind", "100.64.0.10:10370")
    assert len(restarts(run)) == 1  # env réécrit, unit IDENTIQUE : le cas de N2 isolé
    run2 = FakeRun()
    go(tmp_path, FakeBw(tmp_path), run2, "--bind", "100.64.0.10:10370")  # plus rien ne change
    assert restarts(run2) == []
    unit = tmp_path / ".config/systemd/user/herdr-web.service"
    unit.write_text(unit.read_text() + "# vieux\n")
    run3 = FakeRun()
    go(tmp_path, FakeBw(tmp_path), run3)
    assert len(restarts(run3)) == 1  # unit changée, env identique


def test_simulation_annonce_le_restart_conditionnel(tmp_path):
    lines = []
    inst.main([], home=tmp_path, bw=FakeBw(tmp_path), run=FakeRun(), which=which_all, out=lines.append)
    assert any("restart herdr-web.service" in l and "unit changed or env rewritten" in l for l in lines)


def test_reecriture_alteree_refuse_et_original_intact(tmp_path, monkeypatch):
    env = env_existant(tmp_path)
    before = env.read_text()
    real = inst._rewrite_lines
    monkeypatch.setattr(inst, "_rewrite_lines", lambda t, d: real(t, d).replace("abc", "abd"))
    rc, lines = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--bind", "100.64.0.10:10370")
    assert rc == 2 and any("UNDECIDABLE" in l and "left intact" in l for l in lines)
    assert env.read_text() == before and not (env.parent / "env.tmp").exists()


def test_env_tmp_preexistant_en_mode_large_finit_en_600(tmp_path):
    env = env_existant(tmp_path)
    tmp = env.parent / "env.tmp"
    tmp.write_text("vieux")
    os.chmod(tmp, 0o666)
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--bind", "100.64.0.10:10370")
    assert rc == 0 and stat.S_IMODE(env.stat().st_mode) == 0o600 and not tmp.exists()


def test_env_partiel_message_dit_de_le_retirer(tmp_path, monkeypatch):
    real = inst.write_private

    def partial(path, text, go):
        real(path, text[:10], go)
        raise OSError("disque plein")
    monkeypatch.setattr(inst, "write_private", partial)
    rc, lines = go(tmp_path, FakeBw(tmp_path), FakeRun())
    txt = "\n".join(lines)
    assert rc == 2 and "remove ~/.config/herdr-web/env before any retry" in txt and "in the vault" in txt


def test_env_mal_forme_indecidable_sans_trace_en_simulation(tmp_path, monkeypatch):
    env_existant(tmp_path)
    monkeypatch.setattr(inst, "parse_env", lambda t: (_ for _ in ()).throw(ValueError(SECRET)))
    lines = []
    rc = inst.main(["--bind", "x:1"], home=tmp_path, bw=FakeBw(tmp_path), run=FakeRun(),
                   which=which_all, out=lines.append)
    assert rc == 2 and any("UNDECIDABLE" in l for l in lines)
    assert not any(SECRET in l for l in lines)


def test_champ_absent_porte_la_consigne_d_audit(tmp_path):
    rc, lines = go(tmp_path, FakeBw(tmp_path, drop_field=True), FakeRun())
    assert any("field HERDR_WEB_VAPID_KEY missing" in l and "list items --search herdr-web" in l for l in lines)


def test_sujet_vapid_https_sans_port_ecrit_tel_quel(tmp_path):
    env = env_existant(tmp_path)
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--vapid-subject", "https://herdr.example.org")
    assert rc == 0
    assert inst.parse_env(env.read_text())["HERDR_WEB_VAPID_SUBJECT"] == "https://herdr.example.org"


@pytest.mark.parametrize("bad", ["https://herdr.example.org:10370", "herdr.example.org", "mailto:sans-arobase"])
def test_sujet_vapid_invalide_refuse_meme_en_simulation(tmp_path, bad):
    env = env_existant(tmp_path)
    before, lines = env.read_text(), []
    rc = inst.main(["--vapid-subject", bad], home=tmp_path, bw=FakeBw(tmp_path), run=FakeRun(),
                   which=which_all, out=lines.append)
    assert rc == 1 and "without port" in "\n".join(lines) and env.read_text() == before


def test_bind_liste_reecrit_l_env_et_un_seul_restart(tmp_path):
    env = env_existant(tmp_path)
    go(tmp_path, FakeBw(tmp_path), FakeRun())  # pose la unit
    run = FakeRun()
    liste = "100.64.0.10:10370,192.168.1.10:10370"
    rc, _ = go(tmp_path, FakeBw(tmp_path), run, "--bind", liste)
    assert rc == 0
    assert inst.parse_env(env.read_text())["HERDR_WEB_BIND"] == liste
    assert len(restarts(run)) == 1


# ---------- mode local (sans coffre), détections — publication ----------

def env_lu(home):
    return inst.parse_env((Path(home) / ".config/herdr-web/env").read_text())


def test_local_succes_sans_coffre_jeton_affiche_une_seule_fois(tmp_path):
    bw, run, lines = FakeBw(tmp_path), FakeRun(), []
    rc, _ = go(tmp_path, bw, run, out=lines, vault=False)
    assert rc == 0 and bw.calls == []
    env = tmp_path / ".config/herdr-web/env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert stat.S_IMODE(env.parent.stat().st_mode) == 0o700
    token = env_lu(tmp_path)["HERDR_WEB_TOKEN"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{48}", token)
    porteuses = [i for i, l in enumerate(lines) if token in l]
    assert len(porteuses) == 1 and lines[porteuses[0]] == f"  {token}"
    assert porteuses[0] > max(i for i, l in enumerate(lines) if l.startswith("done"))  # après les gestes
    assert any(f"grep '^HERDR_WEB_TOKEN=' {env} | cut -d= -f2-" in l for l in lines)
    assert not any("BEGIN" in l for l in lines)
    assert run.cmds[-1] == ["systemctl", "--user", "restart", "herdr-web.service"]


def test_local_echec_apres_env_n_affiche_pas_le_jeton_mais_la_commande(tmp_path):
    class Run(FakeRun):
        def __call__(self, cmd, cwd=None):
            super().__call__(cmd, cwd)
            return 1 if cmd[:2] == ["systemctl", "--user"] and "enable" in cmd else 0
    lines = []
    rc, _ = go(tmp_path, FakeBw(tmp_path), Run(), out=lines, vault=False)
    token = env_lu(tmp_path)["HERDR_WEB_TOKEN"]
    assert rc == 2 and not any(token in l for l in lines)
    assert any("grep '^HERDR_WEB_TOKEN='" in l for l in lines)


def test_coffre_le_jeton_n_est_jamais_affiche(tmp_path):
    bw, lines = FakeBw(tmp_path), []
    rc, _ = go(tmp_path, bw, FakeRun(), out=lines)
    assert rc == 0 and not any(bw.stored["fields"][0]["value"] in l for l in lines)
    assert not any("grep '^HERDR_WEB_TOKEN='" in l for l in lines)


def test_local_env_existant_rien_affiche_rien_regenere(tmp_path):
    env = env_existant(tmp_path)
    before, lines = env.read_text(), []
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), out=lines, vault=False)
    assert rc == 0 and env.read_text() == before
    assert not any("garde" in l or "shown ONCE" in l for l in lines)


def test_local_hosts_et_certificat_derives_du_bind(tmp_path):
    from cryptography import x509
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--bind", "100.64.0.10:10370,127.0.0.1:10370",
               vault=False)
    assert rc == 0
    assert env_lu(tmp_path)["HERDR_WEB_HOSTS"] == "100.64.0.10:10370,localhost:10370,127.0.0.1:10370"
    cert = x509.load_pem_x509_certificate((tmp_path / ".config/herdr-web/tls/cert.pem").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sorted(str(i) for i in san.get_values_for_type(x509.IPAddress)) == ["100.64.0.10", "127.0.0.1"]
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]


@pytest.mark.parametrize("bind,attendu", [
    ("127.0.0.1:10370", "localhost:10370,127.0.0.1:10370"),
    ("100.64.0.10:10370", "100.64.0.10:10370"),
    ("[::1]:8443", "localhost:8443,127.0.0.1:8443"),
])
def test_default_hosts(bind, attendu):
    assert inst.default_hosts(bind) == attendu


def test_default_hosts_joker_porte_le_nom_de_la_machine(monkeypatch):
    monkeypatch.setattr(inst.socket, "gethostname", lambda: "atelier")
    assert inst.default_hosts("0.0.0.0:10370") == "atelier:10370,localhost:10370"


def unit_posee(home):
    return (Path(home) / ".config/systemd/user/herdr-web.service").read_text()


def test_nixos_unit_identique_au_gabarit(tmp_path):
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), vault=False)
    gabarit = (fake_repo(tmp_path) / "deploy/herdr-web.service").read_text()
    assert rc == 0 and unit_posee(tmp_path) == gabarit.replace("@REPO@", str(fake_repo(tmp_path)))


def test_hors_nixos_path_de_l_unit_detecte(tmp_path):
    def which(name):
        return {"hc": "/opt/herdr/bin/hc", "ffmpeg": "/usr/bin/ffmpeg", "nix": "/fake/nix"}.get(name)
    rc, lines = go(tmp_path, FakeBw(tmp_path), FakeRun(), vault=False, nixos=False, which=which)
    assert rc == 0
    paths = [l for l in unit_posee(tmp_path).splitlines() if l.startswith("Environment=PATH=")]
    assert paths == ["Environment=PATH=/opt/herdr/bin:/usr/bin:/usr/local/bin:/bin:%h/.nix-profile/bin:%h/.local/bin"]


def test_unit_path_explicite_l_emporte(tmp_path):
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--unit-path", "/x/bin:/usr/bin", vault=False)
    assert rc == 0 and "Environment=PATH=/x/bin:/usr/bin\n" in unit_posee(tmp_path)


def sans_nix(name):
    return None if name == "nix" else f"/fake/{name}"


def test_sans_nix_ni_pyenv_refus_avec_la_recette_venv(tmp_path):
    run, lines = FakeRun(), []
    rc, _ = go(tmp_path, FakeBw(tmp_path), run, out=lines, vault=False, which=sans_nix)
    txt = "\n".join(lines)
    assert rc == 1 and run.cmds == [] and tree(tmp_path) == []
    assert "python3 -m venv" in txt and "pip install pywebpush cryptography" in txt


def test_sans_nix_pyenv_fourni_utilise_tel_quel(tmp_path):
    py = tmp_path / ".local/share/herdr-web/pyenv/bin/python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    run = FakeRun()
    rc, _ = go(tmp_path, FakeBw(tmp_path), run, vault=False, which=sans_nix)
    assert rc == 0 and not [c for c in run.cmds if c[0] == "nix"]
    assert (tmp_path / ".config/herdr-web/env").exists()


def test_cryptography_absent_refus_avant_tout_geste(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "have_crypto", lambda: False)
    run, lines = FakeRun(), []
    rc, _ = go(tmp_path, FakeBw(tmp_path), run, out=lines, vault=False)
    assert rc == 1 and run.cmds == [] and tree(tmp_path) == []
    assert any("cryptography" in l for l in lines)


def test_simulation_locale_n_ecrit_rien_et_annonce_le_jeton(tmp_path):
    bw, run, lines = FakeBw(tmp_path), FakeRun(), []
    rc = inst.main(["--cert-autosigne"], home=tmp_path, repo=fake_repo(tmp_path), bw=bw, run=run,
                   which=which_all, out=lines.append)
    assert rc == 0 and tree(tmp_path) == [] and bw.calls == [] and run.cmds == []
    txt = "\n".join(lines)
    assert "local mode" in txt and "print the access token ONCE" in txt and "bw-unlocked" not in txt


# ---------- réserves du checker, PR #26 ----------

def test_m1_hors_nixos_un_dossier_du_store_n_entre_pas_dans_la_unit(tmp_path):
    """`nix develop` met ffmpeg sous /nix/store : sans racine GC, ce chemin disparaît au premier
    nix-collect-garbage, et la voix casse en silence."""
    store = "/nix/store/0000aaaa-ffmpeg-9.0.2-bin/bin"

    def which(name):
        return {"hc": "/opt/herdr/bin/hc", "ffmpeg": f"{store}/ffmpeg", "nix": "/fake/nix"}.get(name)
    lines = []
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), out=lines, vault=False, nixos=False, which=which)
    assert rc == 0
    path = [l for l in unit_posee(tmp_path).splitlines() if l.startswith("Environment=PATH=")][0]
    assert "/nix/store" not in path and path.startswith("Environment=PATH=/opt/herdr/bin:")
    assert any("ffmpeg" in l and "/nix/store" in l and "WARNING" in l for l in lines)


def test_m2_certificat_regenere_pour_les_hosts_de_l_env_existant(tmp_path):
    from cryptography import x509
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--hosts", "box.tail.ts.net:10370", vault=False)
    assert rc == 0
    tls = tmp_path / ".config/herdr-web/tls"
    (tls / "cert.pem").unlink()
    (tls / "key.pem").unlink()
    lines = []
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), out=lines, vault=False)  # renouvellement, sans --hosts
    assert rc == 0
    san = x509.load_pem_x509_certificate((tls / "cert.pem").read_bytes()).extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["box.tail.ts.net"]
    assert san.get_values_for_type(x509.IPAddress) == []
    assert any("for box.tail.ts.net" in l for l in lines)


def test_m2_hosts_explicite_l_emporte_sur_l_env_pour_le_certificat(tmp_path):
    from cryptography import x509
    env_existant(tmp_path)  # HERDR_WEB_HOSTS=h:1
    rc, _ = go(tmp_path, FakeBw(tmp_path), FakeRun(), "--hosts", "autre.example.org:10370", vault=False)
    assert rc == 0
    cert = x509.load_pem_x509_certificate((tmp_path / ".config/herdr-web/tls/cert.pem").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["autre.example.org"]


def test_m4_exception_imprevue_apres_env_donne_la_commande_de_relecture(tmp_path):
    class Run(FakeRun):
        def __call__(self, cmd, cwd=None):
            super().__call__(cmd, cwd)
            if cmd[:1] == ["systemctl"]:
                raise RuntimeError("imprévu")
            return 0
    lines = []
    rc, _ = go(tmp_path, FakeBw(tmp_path), Run(), out=lines, vault=False)
    token = env_lu(tmp_path)["HERDR_WEB_TOKEN"]
    assert rc == 2 and not any(token in l for l in lines)
    assert any("UNDECIDABLE" in l for l in lines)
    assert any("grep '^HERDR_WEB_TOKEN='" in l for l in lines)


def test_m4_exception_avant_env_ne_parle_pas_de_relecture(tmp_path):
    class Run(FakeRun):
        def __call__(self, cmd, cwd=None):
            raise RuntimeError("imprévu")
    lines = []
    rc, _ = go(tmp_path, FakeBw(tmp_path), Run(), out=lines, vault=False)
    assert rc == 2 and not (tmp_path / ".config/herdr-web/env").exists()
    assert not any("grep '^HERDR_WEB_TOKEN='" in l for l in lines)


def test_m5_write_private_n_ecrase_jamais_un_fichier_existant(tmp_path):
    p = tmp_path / "d/env"
    p.parent.mkdir()
    p.write_text("ancien")
    with pytest.raises(FileExistsError):
        inst.write_private(p, "nouveau", True)
    assert p.read_text() == "ancien"
