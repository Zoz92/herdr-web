import base64
import json
import os
import threading

import pytest

from herdr_web import actions
from herdr_web.actions import AgentBlocked, Busy, EmptyTranscript, TooLarge
from herdr_web.core import HcError, HcTimeout


class FakeHc:
    def __init__(self, results=None, error=None):
        self.calls = []
        self.results = results or {}
        self.error = error

    def request(self, method, params, timeout=100.0):
        self.calls.append((method, params, timeout))
        if self.error:
            raise self.error
        return self.results.get(method, {})


class Done:
    def __init__(self, stdout=""):
        self.stdout = stdout


def test_send_text_sans_force():
    hc = FakeHc()
    actions.send_text(hc, "t1", "salut")
    assert hc.calls == [("terminal.send", {"terminal": "t1", "text": "salut"}, 90)]
    assert "force" not in hc.calls[0][1]


def test_send_text_bloque_leve_agentblocked():
    hc = FakeHc(error=HcError(-32000, "blocked", {"kind": "blocked"}))
    with pytest.raises(AgentBlocked):
        actions.send_text(hc, "t1", "x")


def test_send_text_autre_erreur_remonte():
    hc = FakeHc(error=HcError(-32000, "autre", {"kind": "other"}))
    with pytest.raises(HcError):
        actions.send_text(hc, "t1", "x")
    hc = FakeHc(error=HcTimeout("lent"))
    with pytest.raises(HcTimeout):
        actions.send_text(hc, "t1", "x")


def test_send_photo_put_puis_send_cite_le_chemin():
    hc = FakeHc(results={"terminal.put": {"path": "/tmp/p.png"}})
    actions.send_photo(hc, "t1", "p.png", b"\x89PNG", caption="regarde")
    (m1, p1, _), (m2, p2, _) = hc.calls
    assert m1 == "terminal.put"
    assert p1 == {"terminal": "t1", "name": "p.png", "data": base64.b64encode(b"\x89PNG").decode()}
    assert m2 == "terminal.send"
    assert p2["text"] == "regarde [image : /tmp/p.png]"
    hc = FakeHc(results={"terminal.put": {"path": "/tmp/q.png"}})
    actions.send_photo(hc, "t1", "q.png", b"x")
    assert hc.calls[1][1]["text"] == "[image : /tmp/q.png]"


def test_send_photo_trop_grosse():
    hc = FakeHc()
    with pytest.raises(TooLarge):
        actions.send_photo(hc, "t1", "p.png", b"0" * (8 * 1024 * 1024 + 1))
    assert hc.calls == []


def _fake_run(calls, text="Fin de session.", fail_on=None):
    def run(argv, **kw):
        calls.append((argv, kw))
        if fail_on == len(calls):
            raise RuntimeError("echec")
        if len(calls) == 2:
            return Done(json.dumps({"text": text}) + "\n")
        return Done()
    return run


def test_transcribe_appelle_ffmpeg_puis_handy():
    calls = []
    out = actions.transcribe(b"audio", run=_fake_run(calls), handy_bin="/bin/handy")
    assert out == "Fin de session."
    (a1, k1), (a2, k2) = calls
    assert a1[0] == "ffmpeg" and "-y" in a1
    assert a1[a1.index("-i") + 1] == "pipe:"
    assert a1[a1.index("-ar") + 1] == "16000" and a1[a1.index("-ac") + 1] == "1"
    assert k1["input"] == b"audio"
    wav = a1[-1]
    assert wav.endswith(".wav")
    assert a2 == ["/bin/handy", "--transcribe-file", wav, "--json"]
    assert not os.path.exists(wav)


@pytest.mark.parametrize("fail_on", [1, 2])
def test_transcribe_nettoie_en_echec(fail_on):
    calls = []
    with pytest.raises(RuntimeError):
        actions.transcribe(b"a", run=_fake_run(calls, fail_on=fail_on), handy_bin="/h")
    assert not os.path.exists(calls[0][0][-1])


def test_send_voice_vide_n_envoie_rien(monkeypatch):
    monkeypatch.setattr(actions, "resolve_handy_bin", lambda: "/h")
    hc = FakeHc()
    calls = []
    with pytest.raises(EmptyTranscript):
        actions.send_voice(hc, "t1", b"a", run=_fake_run(calls, text="  "))
    assert hc.calls == []


def test_send_voice_envoie_le_texte(monkeypatch):
    monkeypatch.setattr(actions, "resolve_handy_bin", lambda: "/h")
    hc = FakeHc()
    assert actions.send_voice(hc, "t1", b"a", run=_fake_run([])) == {"text": "Fin de session.", "avertissement": None}
    assert hc.calls[0][1]["text"] == "Fin de session."


def test_double_envoi_refuse():
    entered, release = threading.Event(), threading.Event()

    class Slow(FakeHc):
        def request(self, method, params, timeout=100.0):
            entered.set()
            release.wait(5)
            return {}

    hc = Slow()
    t = threading.Thread(target=actions.send_text, args=(hc, "t1", "a"))
    t.start()
    assert entered.wait(5)
    with pytest.raises(Busy):
        actions.send_text(hc, "t1", "b")
    actions.send_text(FakeHc(), "t2", "autre adresse")
    release.set()
    t.join()
    actions.send_text(FakeHc(), "t1", "libérée")


def test_double_envoi_libere_apres_erreur():
    with pytest.raises(HcTimeout):
        actions.send_text(FakeHc(error=HcTimeout("x")), "t9", "a")
    actions.send_text(FakeHc(), "t9", "b")


def test_handy_bin_lu_dans_desktop(tmp_path):
    d = tmp_path / "Handy.desktop"
    d.write_text("[Desktop Entry]\nName=Handy\nExec=/nix/store/abc-handy-0.9.8/bin/.handy-wrapped \n")
    assert actions.resolve_handy_bin(d) == "/nix/store/abc-handy-0.9.8/bin/.handy-wrapped"


# -- I4 (relecture finale) : ffmpeg et Handy bornés dans le temps, échec lisible --

def test_transcribe_borne_ffmpeg_30_s_et_handy_90_s():
    calls = []
    actions.transcribe(b"a", run=_fake_run(calls), handy_bin="/h")
    assert [kw.get("timeout") for _, kw in calls] == [30, 90]


@pytest.mark.parametrize("etape,attendu", [(1, "ffmpeg : délai de 30 s dépassé"),
                                            (2, "Handy : délai de 90 s dépassé")])
def test_transcribe_delai_depasse_echec_lisible(etape, attendu):
    import subprocess
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if len(calls) == etape:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return Done(json.dumps({"text": "x"}) + "\n")

    with pytest.raises(actions.TranscriptionFailed) as e:
        actions.transcribe(b"a", run=run, handy_bin="/h")
    assert str(e.value) == attendu
    assert not os.path.exists(calls[0][-1])  # wav nettoyé


def test_transcribe_echec_garde_la_fin_du_stderr():
    import subprocess

    def run(argv, **kw):
        raise subprocess.CalledProcessError(1, argv, b"", b"x" * 1000 + b"Invalid data found")

    with pytest.raises(actions.TranscriptionFailed) as e:
        actions.transcribe(b"a", run=run, handy_bin="/h")
    m = str(e.value)
    assert m.startswith("ffmpeg : rc=1 ") and m.endswith("Invalid data found") and len(m) < 330


def test_echec_de_transcription_rend_un_502_lisible():
    from herdr_web.server import _map_error
    h = _map_error(actions.TranscriptionFailed("Handy : délai de 90 s dépassé"))
    assert (h.status, h.code, h.detail) == (502, "transcription", "Handy : délai de 90 s dépassé")
