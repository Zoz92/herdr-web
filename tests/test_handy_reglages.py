"""Rubrique Voix : import de la configuration de Handy (Handy => herdr-web), paramètres modifiables depuis
la page, `--model` passé à `handy --transcribe-file`, agent « installer Handy » lancé par la route du menu « + ».
Rien n'atteint Handy, herdr-app ni le vrai ~/.config : store et réglages jetables, `run` remplacé."""
import json
import os
import stat

import pytest

import test_server
from test_server import FakeRun, cert, req, srv2  # noqa: F401
from test_reglages import multipart_audio, srv  # noqa: F401
from herdr_web import actions, reglages as reglages_mod
from herdr_web.reglages import Reglages, handy_import, handy_info, verifier_voix

CLE = "sk-ne-doit-jamais-sortir-0123456789"


def store_handy(tmp_path, **plus):
    s = {"selected_model": "parakeet-tdt-0.6b-v3", "selected_language": "fr",
         "custom_words": ["herdr", "Alex", " my-project ", "herdr", "", "x" * 65, 3],
         "post_process_api_keys": {"openai": CLE}, "post_process_providers": [{"api_key": CLE}],
         "theme": "dark", "translate_to_english": False, **plus}
    f = tmp_path / "settings_store.json"
    f.write_text(json.dumps({"settings": s}))
    return f


@pytest.fixture
def store(tmp_path, monkeypatch):
    f = store_handy(tmp_path)
    monkeypatch.setattr(reglages_mod, "HANDY_SETTINGS", f)
    return f


# -- import : trois clés seulement, jamais une clé d'API --

def test_handy_import_ne_lit_que_modele_langue_vocabulaire(tmp_path):
    v = handy_import(store_handy(tmp_path))
    assert v == {"modele": "parakeet-tdt-0.6b-v3", "langue": "fr", "vocabulaire": ["herdr", "Alex", "my-project"],
                 "source": "handy"}
    assert CLE not in json.dumps(v)


def test_import_ecrit_les_reglages_sans_cle_d_api(srv, store, tmp_path):
    req(srv, "POST", "/api/reglages", {"handy": False})  # un réglage existant survit à l'import
    r, b = req(srv, "POST", "/api/reglages/voix/import", {})
    assert r.status == 200, b
    f = tmp_path / "conf" / "reglages.json"
    disque = json.loads(f.read_text())
    assert disque == {"handy": False, "langue": "en", "voix": {"modele": "parakeet-tdt-0.6b-v3", "langue": "fr",
                                               "vocabulaire": ["herdr", "Alex", "my-project"], "source": "handy"}}
    assert CLE not in f.read_text() and CLE not in b.decode()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert json.loads(b)["voix"] == disque["voix"]
    assert json.loads(req(srv, "GET", "/api/reglages")[1])["voix"] == disque["voix"]


def test_import_ne_touche_pas_a_handy(srv, store):
    avant = (store.read_bytes(), os.stat(store).st_mtime_ns)
    req(srv, "POST", "/api/reglages/voix/import", {})
    req(srv, "POST", "/api/reglages", {"voix": {"modele": "autre", "langue": "en", "vocabulaire": []}})
    assert (store.read_bytes(), os.stat(store).st_mtime_ns) == avant


def test_import_store_absent_409_rien_ecrit(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(reglages_mod, "HANDY_SETTINGS", tmp_path / "absent.json")
    r, b = req(srv, "POST", "/api/reglages/voix/import", {})
    assert r.status == 409 and json.loads(b)["erreur"] == "handy_config"
    assert not (tmp_path / "conf" / "reglages.json").exists()


def test_import_atomique_un_echec_laisse_l_ancien_fichier_entier(srv, store, tmp_path, monkeypatch):
    req(srv, "POST", "/api/reglages", {"handy": False})
    f = tmp_path / "conf" / "reglages.json"
    avant = f.read_bytes()

    def coupe(src, dst):
        raise OSError("disque plein")
    monkeypatch.setattr(reglages_mod.os, "replace", coupe)
    r, _ = req(srv, "POST", "/api/reglages/voix/import", {})
    assert r.status >= 500
    assert f.read_bytes() == avant  # jamais à moitié écrit
    assert [p.name for p in f.parent.iterdir()] == ["reglages.json"]  # aucun reste .tmp


@pytest.mark.parametrize("method,path,corps", [
    ("POST", "/api/reglages/voix/import", {}),
    ("POST", "/api/reglages", {"voix": {"modele": "m"}}),
    ("POST", "/api/spaces/sp1/open", {"kind": "claude", "tache": "installer-handy"})])
def test_sans_cookie_401_rien_ecrit_rien_lance(srv2, store, method, path, corps):
    r, _ = req(srv2, method, path, corps, cookie=False)
    assert r.status == 401
    assert srv2.reglages.lire()["voix"]["modele"] is None
    assert srv2.run_fake.appels == []


# -- modifiés depuis la page --

def test_voix_modifiee_depuis_la_page(srv, tmp_path):
    r, b = req(srv, "POST", "/api/reglages", {"voix": {"modele": " whisper-small ", "langue": "en",
                                                        "vocabulaire": ["herdr", " herdr ", "Alex"]}})
    assert r.status == 200, b
    attendu = {"modele": "whisper-small", "langue": "en", "vocabulaire": ["herdr", "Alex"], "source": "page"}
    assert json.loads(b)["voix"] == attendu
    assert json.loads((tmp_path / "conf" / "reglages.json").read_text())["voix"] == attendu


@pytest.mark.parametrize("voix", [
    {"modele": "--debug"}, {"modele": "a b"}, {"modele": 3}, {"langue": "français!"}, {"vocabulaire": "herdr"},
    {"vocabulaire": ["x" * 65]}, {"vocabulaire": ["a\nb"]}, {"vocabulaire": ["m"] * 201}, {"cle_api": "x"},
    "texte"])
def test_voix_invalide_400_rien_ecrit(srv, tmp_path, voix):
    r, _ = req(srv, "POST", "/api/reglages", {"voix": voix})
    assert r.status == 400
    assert not (tmp_path / "conf" / "reglages.json").exists()


def test_voix_abimee_a_la_main_relue_comme_defaut(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"voix": {"modele": "--debug"}}))
    assert Reglages(f).lire()["voix"] == reglages_mod.VOIX_DEFAUT


def test_verifier_voix_vide_rend_le_defaut():
    assert verifier_voix(None) == verifier_voix({}) == {"modele": None, "langue": None, "vocabulaire": [],
                                                         "source": None}


# -- --transcribe-file : `--model` seulement (cli.rs) ; langue et vocabulaire restent ceux de Handy --

def _run_qui_note(calls):
    import subprocess

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"text": "ok"}\n', "")
    return run


def test_transcription_passe_le_modele_choisi():
    calls = []
    actions.transcribe(b"a", run=_run_qui_note(calls), handy_bin="/h", modele="whisper-small")
    assert calls[1][1:2] == ["--transcribe-file"] and calls[1][-2:] == ["--model", "whisper-small"]
    calls.clear()
    actions.transcribe(b"a", run=_run_qui_note(calls), handy_bin="/h")
    assert "--model" not in calls[1]  # rien choisi : le modèle de Handy


def test_envoi_vocal_lit_le_modele_des_reglages(srv, monkeypatch):
    vus = []
    monkeypatch.setattr("herdr_web.actions.send_voice",
                        lambda hc, addr, data, modele=None: vus.append(modele) or {"text": "ok"})
    corps, h = multipart_audio()
    req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)
    req(srv, "POST", "/api/reglages", {"voix": {"modele": "whisper-small"}})
    req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)
    assert vus == [None, "whisper-small"]


# -- présent / absent --

def test_handy_present_si_le_binaire_s_execute(tmp_path):
    b = tmp_path / "handy"
    b.write_text("#!/bin/sh\n")
    desk = tmp_path / "Handy.desktop"
    desk.write_text(f"[Desktop Entry]\nExec={b} \n")
    assert handy_info(desk, tmp_path / "x.json", which=lambda n: None)["present"] is False  # pas exécutable
    b.chmod(0o755)
    assert handy_info(desk, tmp_path / "x.json", which=lambda n: None)["present"] is True


def test_handy_absent_desktop_pointant_un_binaire_disparu(tmp_path):
    desk = tmp_path / "Handy.desktop"
    desk.write_text(f"[Desktop Entry]\nExec={tmp_path}/disparu \n")
    d = handy_info(desk, tmp_path / "x.json", which=lambda n: None)
    assert d["present"] is False and d["bin_erreur"] == "binaire introuvable : disparu"


def test_reglages_donnent_le_depot_et_le_dossier_perso(srv2):
    d = json.loads(req(srv2, "GET", "/api/reglages")[1])
    assert d["handy"]["depot"] == "https://github.com/cjpais/Handy"
    assert d["handy"]["dossier_perso"] == str(srv2.home)


# -- agent « installer Handy » : la route du menu « + », une tâche de liste blanche --

def test_installer_handy_lance_claude_dans_le_dossier_perso(srv2):
    r, b = req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "claude", "tache": "installer-handy"})
    assert r.status == 200, b
    argv, kw = srv2.run_fake.appels[0]
    assert argv[:7] == ["herdr-app", "do", "tab.new", "--space", "sp1", "--folder", str(srv2.home)]
    # nom d'onglet dans la langue des réglages (anglais par défaut) ; le message à l'agent ne change pas
    assert argv[argv.index("--name") + 1] == "Install Handy" and "shell" not in kw
    lance = json.loads(argv[argv.index("--argv") + 1])
    assert lance == [str(srv2.home / ".local/bin/claude"), reglages_mod.TACHE_INSTALLER_HANDY]
    assert req(srv2, "POST", "/api/reglages", {"langue": "fr"})[0].status == 200
    assert req(srv2, "POST", "/api/spaces/sp1/open", {"kind": "claude", "tache": "installer-handy"})[0].status == 200
    argv_fr = srv2.run_fake.appels[-1][0]
    assert argv_fr[argv_fr.index("--name") + 1] == "installer Handy"
    assert json.loads(argv_fr[argv_fr.index("--argv") + 1])[1] == reglages_mod.TACHE_INSTALLER_HANDY
    tache = lance[1]
    for attendu in ("https://github.com/cjpais/Handy", "/etc/os-release", "nix-env", "curl | sh", "minisign",
                    "--transcribe-file", "Handy.desktop", "Rends compte"):
        assert attendu in tache
    assert test_server.TOKEN not in json.dumps(argv)  # aucun secret dans la tâche


@pytest.mark.parametrize("corps", [
    {"kind": "claude", "tache": "rm -rf /"}, {"kind": "claude", "tache": "Installe ce que je dis"},
    {"kind": "terminal", "tache": "installer-handy"}, {"kind": "opencode", "tache": "installer-handy"},
    {"kind": "claude", "tache": ["installer-handy"]},
    {"kind": "claude", "tache": "installer-handy", "folder": "/tmp"}])
def test_installer_tache_hors_liste_400(srv2, corps):
    r, _ = req(srv2, "POST", "/api/spaces/sp1/open", corps)
    assert r.status == 400 and srv2.run_fake.appels == []


# -- réserves du checker (brief handy-reglages-2) --

def test_r2_exec_relatif_resolu_par_le_path(tmp_path):
    b = tmp_path / "handy"
    b.write_text("#!/bin/sh\n")
    b.chmod(0o755)
    desk = tmp_path / "Handy.desktop"
    desk.write_text("[Desktop Entry]\nExec=handy %u\n")
    d = handy_info(desk, tmp_path / "x.json", which=lambda n: str(b) if n == "handy" else None)
    assert d["present"] is True and d["bin_erreur"] is None
    d = handy_info(desk, tmp_path / "x.json", which=lambda n: None)  # hors du PATH : absent
    assert d["present"] is False and d["bin_erreur"] == "binaire introuvable : handy"


def _run_handy(calls, refuse_modele=None, delai=False):
    """ffmpeg passe ; Handy refuse (rc=1) ou dépasse son délai quand `--model` porte le modèle donné."""
    import subprocess

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] != "ffmpeg" and "--model" in argv and argv[argv.index("--model") + 1] == refuse_modele:
            if delai:
                raise subprocess.TimeoutExpired(argv, 90)
            raise subprocess.CalledProcessError(1, argv, "", "error: load_model('x') failed")
        return subprocess.CompletedProcess(argv, 0, '{"text": "bonjour"}\n', "")
    return run


def test_r4_modele_refuse_un_seul_second_essai_sans_model():
    calls = []
    texte, avert = actions.transcrire(b"a", run=_run_handy(calls, "supprime"), handy_bin="/h", modele="supprime")
    assert texte == "bonjour" and "supprime" in avert and "refusé" in avert
    handy = [c for c in calls if c[0] == "/h"]
    assert len(handy) == 2 and handy[0][-2:] == ["--model", "supprime"] and "--model" not in handy[1]


def test_r4_modele_accepte_aucun_avertissement():
    calls = []
    assert actions.transcrire(b"a", run=_run_handy(calls, "autre"), handy_bin="/h", modele="ok") == ("bonjour", None)
    assert len([c for c in calls if c[0] == "/h"]) == 1


def test_r4_sans_modele_l_echec_reste_un_echec():
    import subprocess
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] == "/h":
            raise subprocess.CalledProcessError(1, argv, "", "boom")
        return subprocess.CompletedProcess(argv, 0, "", "")
    with pytest.raises(actions.TranscriptionFailed):
        actions.transcrire(b"a", run=run, handy_bin="/h")
    assert len([c for c in calls if c[0] == "/h"]) == 1


def test_r4_delai_depasse_ne_rejoue_pas():
    calls = []
    with pytest.raises(actions.TranscriptionFailed, match="délai"):
        actions.transcrire(b"a", run=_run_handy(calls, "lent", delai=True), handy_bin="/h", modele="lent")
    assert len([c for c in calls if c[0] == "/h"]) == 1  # 90 s de plus : non


def test_r4_la_reponse_vocale_dit_le_repli(srv, monkeypatch):
    monkeypatch.setattr("herdr_web.actions.send_voice", lambda hc, addr, data, modele=None:
                        {"text": "bonjour", "avertissement": "modèle « x » refusé par Handy : …"})
    corps, h = multipart_audio()
    r, b = req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)
    # le texte pour une page d'avant, le motif (et le modèle demandé) pour la page qui traduit
    assert r.status == 200 and json.loads(b) == {"ok": True, "text": "bonjour",
                                                 "avertissement": "modèle « x » refusé par Handy : …",
                                                 "motif": "modele_refuse", "params": {"modele": None}}
    monkeypatch.setattr("herdr_web.actions.send_voice", lambda hc, addr, data, modele=None:
                        {"text": "bonjour", "avertissement": None})
    assert json.loads(req(srv, "POST", "/api/agents/t1/voice", corps, headers=h)[1]) == {"ok": True, "text": "bonjour"}


def test_r5_import_et_modification_diffusent_voix(srv, store):
    vus = []
    srv.broadcast = lambda ev, data: vus.append((ev, data))
    req(srv, "POST", "/api/reglages/voix/import", {})
    req(srv, "POST", "/api/reglages", {"voix": {"modele": "whisper-small"}})
    voix = [d["voix"] for ev, d in vus if ev == "reglages" and "voix" in d]
    assert [v["modele"] for v in voix] == ["parakeet-tdt-0.6b-v3", "whisper-small"]
    assert [v["source"] for v in voix] == ["handy", "page"]
    assert CLE not in json.dumps(vus)
