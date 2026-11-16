import json
from pathlib import Path

from herdr_web.chat import ChatReader, resolve_session

FIXTURE = Path(__file__).parent / "fixtures" / "session.jsonl"


def _user(text):
    return json.dumps({"type": "user", "timestamp": "t", "message": {"content": text}}) + "\n"


def test_resolve_session_par_pid(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "42.json").write_text(
        json.dumps({"pid": 42, "sessionId": "abc", "cwd": "/tmp/x.y/z"}))
    d = tmp_path / ".claude" / "projects" / "-tmp-x-y-z"
    d.mkdir(parents=True)
    (d / "abc.jsonl").write_text("")
    assert resolve_session(42, home=tmp_path) == d / "abc.jsonl"


def test_resolve_session_absente_rend_none(tmp_path):
    assert resolve_session(1, home=tmp_path) is None
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "1.json").write_text(
        json.dumps({"sessionId": "abc", "cwd": "/nope"}))
    assert resolve_session(1, home=tmp_path) is None


def test_message_nu_reste_a_user():
    items = ChatReader(FIXTURE).tail()
    assert items[0].role == "moi" and items[0].text == "Crée un fichier vide"


def test_tool_use_bash_resume():
    items = ChatReader(FIXTURE).tail()
    outils = [i for i in items if i.role == "outil"]
    assert outils[0].text == "Bash: touch a.txt" and outils[0].tool == "Bash"
    assert outils[1].text == "Read: /work/a.txt"
    assert outils[2].text == "AskUserQuestion"
    assert [i.role for i in items if i.role == "resultat"] == ["resultat"]


def test_thinking_ignore():
    items = ChatReader(FIXTURE).tail()
    assert [i.text for i in items if i.role == "agent"] == ["Fichier créé."]
    assert all(i.text != "rappel interne" for i in items)  # isMeta
    assert len(items) == 7  # moi, 3 outils, resultat, agent, moi : aucune ligne thinking


def test_lecture_incrementale(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("un"))
    r = ChatReader(p)
    assert len(r.read_new()) == 1
    with p.open("a") as f:
        f.write(_user("deux") + _user("trois"))
    assert [i.text for i in r.read_new()] == ["deux", "trois"]
    assert r.read_new() == []


def test_ligne_incomplete_relue(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("complet") + _user("coupe")[:-10])
    r = ChatReader(p)
    assert [i.text for i in r.read_new()] == ["complet"]
    with p.open("a") as f:
        f.write(_user("coupe")[-10:])
    assert [i.text for i in r.read_new()] == ["coupe"]


def test_tail_200(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(_user(str(n)) for n in range(250)))
    items = ChatReader(p).tail()
    assert len(items) == 200 and items[-1].text == "249"
    assert len(ChatReader(p).tail(5)) == 5


def test_image_citee_detectee(monkeypatch):
    monkeypatch.setenv("HOME", "/home/user")  # la fixture cite un chemin sous /home/user
    items = ChatReader(FIXTURE).tail()
    assert items[-1].image == "/home/user/.local/state/herdr-core/default/files/t1g/carre.png"
    assert items[0].image is None


def test_image_citee_suit_le_home_du_service(tmp_path, monkeypatch):
    # réserve de la revue : /home/user/ en dur dans la regex, un autre HOME ne voyait aucune photo
    monkeypatch.setenv("HOME", "/home/autre")
    p = tmp_path / "s.jsonl"
    p.write_text(_user("vois [image : /home/autre/.local/state/herdr-core/default/files/t1/a.png]")
                 + _user("[image : /home/user/.local/state/herdr-core/default/files/t1/b.png]"))
    items = ChatReader(p).tail()
    assert items[0].image == "/home/autre/.local/state/herdr-core/default/files/t1/a.png"
    assert items[1].image is None  # hors du HOME du service : /api/image le refuserait


# --- correction round 1 ---
import pytest

from herdr_web import chat


def test_tail_ne_parse_que_la_fin(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(_user(str(n)) for n in range(5000)))
    appels = []
    vrai = chat._parse
    monkeypatch.setattr(chat, "_parse", lambda d: appels.append(1) or vrai(d))
    r = ChatReader(p)
    items = r.tail(200)
    assert [i.text for i in items] == [str(n) for n in range(4800, 5000)]
    assert len(appels) < 600
    with p.open("a") as f:
        f.write(_user("neuf"))
    assert [i.text for i in r.read_new()] == ["neuf"]


def test_journal_tronque_repart_de_zero(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("aaaa") + _user("bbbb"))
    r = ChatReader(p)
    r.read_new()
    p.write_text(_user("c"))
    assert [i.text for i in r.read_new()] == ["c"]
    assert r.reset is True
    assert r.read_new() == [] and r.reset is False


def test_journal_remplace_repart_de_zero(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("un"))
    r = ChatReader(p)
    r.read_new()
    autre = tmp_path / "autre.jsonl"
    autre.write_text(_user("neuf-long-texte") + _user("deux"))
    autre.replace(p)
    assert [i.text for i in r.read_new()] == ["neuf-long-texte", "deux"]
    assert r.reset is True


ATYPIQUES = [
    "[1, 2]",
    '{"type": "user", "message": "x"}',
    '{"type": "user", "message": {"content": [{"type": "text", "text": null}]}}',
    '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": 3}]}}',
    '{"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"text": null}]}]}}',
]


@pytest.mark.parametrize("ligne", ATYPIQUES)
def test_ligne_atypique_ne_bloque_pas(tmp_path, ligne):
    p = tmp_path / "s.jsonl"
    p.write_text(ligne + "\n" + _user("suite"))
    r = ChatReader(p)
    assert "suite" in [i.text for i in r.read_new()]
    assert r.read_new() == []


def test_lignes_ecartees_comptees(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("[1]\n" + _user("ok"))
    r = ChatReader(p)
    r.read_new()
    assert r.skipped == 1


def _session(tmp_path, cwd):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "sessions" / "7.json").write_text(
        json.dumps({"sessionId": "sid", "cwd": cwd}))


def test_encodage_underscore_et_espace(tmp_path):
    _session(tmp_path, "/tmp/mon_dossier/a b")
    d = tmp_path / ".claude" / "projects" / "-tmp-mon-dossier-a-b"
    d.mkdir(parents=True)
    (d / "sid.jsonl").write_text("")
    assert resolve_session(7, home=tmp_path) == d / "sid.jsonl"


def test_dossier_inattendu_retrouve_par_recherche(tmp_path):
    _session(tmp_path, "/tmp/zzz")
    d = tmp_path / ".claude" / "projects" / "nom-bizarre"
    d.mkdir(parents=True)
    (d / "sid.jsonl").write_text("")
    assert resolve_session(7, home=tmp_path) == d / "sid.jsonl"


def test_tail_multi_blocs_exact(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(_user("x" * 300 + str(n)) for n in range(1000)))  # > 64 Kio
    items = ChatReader(p).tail(500)
    assert [i.text[-3:] for i in items] == [str(n)[-3:] for n in range(500, 1000)]
    assert items[0].text == "x" * 300 + "500"


def test_tail_borne_a_end(tmp_path):
    import json as _j
    from herdr_web.chat import ChatReader
    lignes = [_j.dumps({"type": "assistant", "timestamp": "t",
                        "message": {"content": [{"type": "text", "text": t}]}}) + "\n"
              for t in ("a", "b", "c")]
    p = tmp_path / "s.jsonl"
    p.write_text("".join(lignes))
    fin_b = len((lignes[0] + lignes[1]).encode())
    assert [i.text for i in ChatReader(p).tail(10, end=fin_b)] == ["a", "b"]


# -- v2 : lien outil/résultat, erreur, résumé d'outil, sous-agent --

def _ligne(kind, blocks):
    return json.dumps({"type": kind, "timestamp": "t", "message": {"content": blocks}}) + "\n"


def test_outil_et_resultat_lies_par_ref():
    items = ChatReader(FIXTURE).tail()
    outil = next(i for i in items if i.role == "outil")
    res = next(i for i in items if i.role == "resultat")
    assert outil.ref == "tu1" and res.ref == "tu1" and res.error is False
    assert all(i.ref is None for i in items if i.role in ("moi", "agent"))


def test_resultat_en_erreur(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_ligne("assistant", [{"type": "tool_use", "id": "x1", "name": "Bash",
                                       "input": {"command": "false"}}])
                 + _ligne("user", [{"type": "tool_result", "tool_use_id": "x1",
                                    "content": "Exit code 1", "is_error": True}]))
    res = ChatReader(p).tail()[1]
    assert res.role == "resultat" and res.error is True and res.ref == "x1"


def test_resume_des_autres_outils(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_ligne("assistant", [
        {"type": "tool_use", "id": "a", "name": "Edit", "input": {"file_path": "/w/f.py"}},
        {"type": "tool_use", "id": "b", "name": "Grep", "input": {"pattern": "def x"}},
        {"type": "tool_use", "id": "c", "name": "Agent", "input": {"description": "Relire"}},
        {"type": "tool_use", "id": "d", "name": "TodoWrite", "input": {"todos": []}}]))
    assert [i.text for i in ChatReader(p).tail()] == [
        "Edit: /w/f.py", "Grep: def x", "Agent: Relire", "TodoWrite"]


def test_resolve_subagent(tmp_path):
    from herdr_web.chat import resolve_subagent
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "42.json").write_text(
        json.dumps({"sessionId": "abc", "cwd": "/w"}))
    d = tmp_path / ".claude" / "projects" / "-autre" / "abc" / "subagents"
    d.mkdir(parents=True)
    (d / "agent-a2db.jsonl").write_text("")
    assert resolve_subagent(42, "a2db", home=tmp_path) == d / "agent-a2db.jsonl"
    assert resolve_subagent(42, "zzz", home=tmp_path) is None
    assert resolve_subagent(7, "a2db", home=tmp_path) is None  # session inconnue
    for mauvais in ("../x", "a/b", "*", "", "a.b"):
        assert resolve_subagent(42, mauvais, home=tmp_path) is None


def test_contrat_injecte_retire_de_la_consigne():
    from herdr_web.chat import CONTRAT_FIN, ChatItem, strip_contract
    it = strip_contract([ChatItem("moi", "=== CONTRAT ===\nrègles\n" + CONTRAT_FIN
                                  + "\n\nFais la tâche."), ChatItem("agent", CONTRAT_FIN)])
    assert it[0].text == "Fais la tâche."
    assert it[1].text == CONTRAT_FIN  # seule une consigne (moi) est rognée


# -- I2 (relecture finale) : tail() linéaire et plafonné, reset sans relire depuis 0 --

class _Fichier:
    def __init__(self, f, compte):
        self.f, self.compte = f, compte

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.f.close()

    def read(self, n=-1):
        d = self.f.read(n)
        self.compte[0] += len(d)
        return d

    def seek(self, *a):
        return self.f.seek(*a)

    def fileno(self):
        return self.f.fileno()


def _compteur(p):
    """Un Path qui compte les octets réellement lus, quelle que soit l'implémentation testée."""
    compte = [0]

    class P(type(p)):
        def open(self, *a, **kw):
            return _Fichier(super().open(*a, **kw), compte)
    return P(p), compte


def _meta(n, taille=100):
    ligne = json.dumps({"type": "user", "isMeta": True, "message": {"content": "x" * taille}}) + "\n"
    return ligne * n


def test_tail_lit_chaque_octet_une_fois_au_plus(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("début") + _meta(60_000))  # ~9 Mo sans élément : tout doit être remonté
    q, compte = _compteur(p)
    items = ChatReader(q).tail(200)
    assert [i.text for i in items] == ["début"]
    taille = p.stat().st_size
    assert compte[0] <= taille + 64 * 1024  # avant : relu depuis la fin à chaque doublement (≈ 2×)


def test_tail_plafonne_a_32_mo(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("trop loin") + _meta(230_000))  # ~35 Mo de lignes sans élément
    q, compte = _compteur(p)
    items = ChatReader(q).tail(200)
    assert items == []  # au-delà du plafond, le fil rendu est plus court, pas plus lent
    assert compte[0] <= 32 * 1024 * 1024 + 64 * 1024


def test_reset_ne_relit_pas_depuis_zero(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_user("un"))
    q, compte = _compteur(p)
    r = ChatReader(q)
    r.read_new()
    autre = tmp_path / "autre.jsonl"
    autre.write_text(_meta(40_000) + "".join(_user(f"m{i}") for i in range(300)))  # ~6 Mo
    autre.replace(p)
    compte[0] = 0
    items = r.read_new()
    assert r.reset is True
    assert [i.text for i in items][-1] == "m299" and len(items) == 200
    assert compte[0] < 512 * 1024  # la fin seulement ; avant : les 6 Mo depuis 0
    assert r.read_new() == [] and r.reset is False  # l'offset est bien en fin de fichier


def _file(prompt, kind="human", mode="prompt", sidechain=False):
    return {"type": "attachment", "isSidechain": sidechain, "timestamp": "2026-11-14T22:24:58.356Z",
            "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": mode,
                           "origin": {"kind": kind}, "timestamp": "2026-11-14T22:24:58.356Z"}}


def test_message_tape_pendant_un_tour_affiche():
    """L'utilisateur : « j'ai écrit un message et tu l'as lu mais il s'affiche pas » — Claude Code range un
    message tapé pendant un tour en attachment queued_command, pas en ligne user."""
    from herdr_web.chat import _parse
    [it] = _parse(_file("Pourquoi j ai pas la branche"))
    assert (it.role, it.text, it.ts) == ("moi", "Pourquoi j ai pas la branche", "2026-11-14T22:24:58.356Z")
    assert [i.text for i in _parse(_file("depuis Discord", kind="channel"))] == ["depuis Discord"]


def test_file_d_attente_hors_zoz_ignoree():
    from herdr_web.chat import _parse
    assert _parse(_file("<task-notification>…", kind="task-notification", mode="task-notification")) == []
    assert _parse(_file("rapport d'un sous-agent", kind="peer")) == []
    assert _parse(_file("tour de sous-agent", sidechain=True)) == []
    assert _parse(_file("   ")) == []
    assert _parse({"type": "attachment", "attachment": {"type": "edited_text_file"}}) == []


def _op(op, content, ts="2026-11-14T22:41:12.304Z"):
    return {"type": "queue-operation", "operation": op, "timestamp": ts, "content": content}


def test_file_d_attente_de_claude_code():
    """L'utilisateur : « c'est grisé lorsque tu travailles car pas encore envoyé, il faut reproduire ce
    comportement » — une mise en file donne un élément « attente », son retrait un « retrait »."""
    from herdr_web.chat import _parse
    [a] = _parse(_op("enqueue", "et je sais pas pourquoi"))
    assert (a.role, a.text, a.ts) == ("attente", "et je sais pas pourquoi", "2026-11-14T22:41:12.304Z")
    [r] = _parse(_op("remove", "et je sais pas pourquoi"))
    assert (r.role, r.text) == ("retrait", "et je sais pas pourquoi")


def test_file_d_attente_hors_texte_de_zoz_ignoree():
    from herdr_web.chat import _parse
    for c in ("<task-notification>x</task-notification>", '<agent-message from="a">x</agent-message>',
              '<hc from="t7 x">rapport</hc>', '<channel source="discord">x</channel>', "  ", None):
        assert _parse(_op("enqueue", c)) == []
    assert _parse({"type": "queue-operation", "operation": "dequeue", "timestamp": "t"}) == []


# --- réserve de la revue : images collées dans Claude Code ---
import base64 as _b64
import hashlib as _hl


def _collee_ligne(data: bytes, mt="image/png", texte="[Image #1] vois"):
    return json.dumps({"type": "user", "timestamp": "t", "message": {"content": [
        {"type": "text", "text": texte},
        {"type": "image", "source": {"type": "base64", "media_type": mt,
                                     "data": _b64.b64encode(data).decode()}}]}}) + "\n"


def test_image_collee_rendue_et_servable(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_collee_ligne(b"\x89PNGcolle"))
    items = ChatReader(p).tail()
    assert [(i.role, i.text) for i in items] == [("moi", "[Image #1] vois"), ("moi", "")]
    cle = items[1].collee
    assert cle == _hl.sha256(_b64.b64encode(b"\x89PNGcolle")).hexdigest()
    assert chat.image_collee(cle) == ("image/png", b"\x89PNGcolle")


def test_image_collee_hors_liste_ou_invalide_ignoree(tmp_path):
    p = tmp_path / "s.jsonl"
    lignes = _collee_ligne(b"<svg/>", mt="image/svg+xml")
    lignes += json.dumps({"type": "user", "message": {"content": [
        {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}]}}) + "\n"
    p.write_text(lignes)
    assert [i.collee for i in ChatReader(p).tail()] == [None]
    assert chat.image_collee("0" * 64) is None


def test_images_collees_bornees_en_memoire(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "COLLEES_TOTAL", 100)
    monkeypatch.setattr(chat, "_collees", chat.OrderedDict())
    monkeypatch.setattr(chat, "_collees_taille", 0)
    p = tmp_path / "s.jsonl"
    p.write_text(_collee_ligne(b"a" * 60) + _collee_ligne(b"b" * 60))
    cles = [i.collee for i in ChatReader(p).tail() if i.collee]
    assert chat.image_collee(cles[0]) is None  # la plus ancienne est sortie
    assert chat.image_collee(cles[1]) == ("image/png", b"b" * 60)
    assert chat._collees_taille <= 100


# -- pseudo-balises que Claude Code écrit en message « user » (commandes `!` et `/`, notifications) --

BALISES = Path(__file__).parent / "fixtures" / "pseudo_balises.jsonl"  # lignes réelles de ~/.claude/projects


def _trame():
    return [(i.role, i.tool, i.text) for i in ChatReader(BALISES).tail()]


def test_commande_shell_bang_n_est_plus_une_bulle_de_user():
    """L'utilisateur, 2026-11-15 : `!cmd` s'affichait « <bash-input>cmd</bash-input> » dans une bulle."""
    t = _trame()
    assert ("shell", "input", "python3 ~/.claude/jobs/293ad885/tmp/buildtime.p") in t
    assert ("shell", "input", "echo ok") in t
    assert not any("<bash" in x[2] or "<local-command" in x[2] or "<command-" in x[2] for x in t)
    assert not any(r == "moi" for r, _, _ in t)


def test_sortie_shell_stdout_stderr_separes_et_vides_ecartes():
    t = _trame()
    assert ("shell", "stderr", "/run/current-system/sw/bin/python3: can't open file "
            "'/home/user/.claude/jobs/293ad885/tmp/buildtime.p': [Errno 2] No such file or directory") in t
    assert ("shell", "stdout", "ok") in t
    assert [x for x in t if x[2] == ""] == []  # <bash-stdout></bash-stdout> vide : rien


def test_commande_slash_et_sortie_locale():
    t = _trame()
    assert ("commande", None, "/exit") in t
    assert ("shell", "stdout", "Bye!") in t


def test_notification_de_tache_en_une_ligne_et_caveat_masque():
    t = _trame()
    assert ("notif", None, 'Background command "Attendre la fin du run CI 773" completed (exit code 0)') in t
    assert not any("caveat" in x[2] or "task-id" in x[2] for x in t)


def test_commande_slash_avec_arguments():
    from herdr_web.chat import _parse
    [it] = _parse({"type": "user", "timestamp": "t", "message": {"content":
        "<command-name>/model</command-name>\n<command-message>model</command-message>\n"
        "<command-args>opus</command-args>"}})
    assert (it.role, it.text) == ("commande", "/model opus")


def test_texte_de_zoz_avec_chevron_reste_une_bulle():
    from herdr_web.chat import _parse
    [it] = _parse({"type": "user", "timestamp": "t", "message": {"content": "<div> pourquoi ça casse ?"}})
    assert (it.role, it.text) == ("moi", "<div> pourquoi ça casse ?")


def test_texte_de_zoz_avec_bash_input_au_milieu_reste_entier():
    """Un texte libre qui CITE la balise n'est pas une commande `!` : bulle `moi`, texte intact."""
    from herdr_web.chat import _parse
    texte = "regarde ce que Claude Code écrit : <bash-input>ls</bash-input> et dis-moi pourquoi"
    [it] = _parse({"type": "user", "timestamp": "t", "message": {"content": texte}})
    assert (it.role, it.text) == ("moi", texte)


def test_notification_sans_summary_repli_tache_terminee():
    from herdr_web.chat import _parse
    [it] = _parse({"type": "user", "timestamp": "t", "message": {"content":
        "<task-notification><task-id>x</task-id><result>ok</result></task-notification>"}})
    assert (it.role, it.text) == ("notif", "tâche de fond terminée")


def test_commande_slash_sans_command_name_repli_sur_command_message():
    from herdr_web.chat import _parse
    [it] = _parse({"type": "user", "timestamp": "t", "message": {"content":
        "<command-message>compact</command-message>\n<command-args>court</command-args>"}})
    assert (it.role, it.text) == ("commande", "/compact court")


# --- messages tapés par hc : jamais attribués à l'utilisateur (2026-11-15) ---
# formes relevées dans 828 journaux : 104 enveloppes entières, toutes classées (52 + 49 `from`, 3 sans)
FORMES_HC = [
    ('<hc from="t1c">Brief : /tmp/x/brief.md.</hc>', ("t1c", None, "Brief : /tmp/x/brief.md.")),
    ('<hc from="t4s design-reglages">compte rendu\nsur deux lignes</hc>', ("t4s", "design-reglages", "compte rendu\nsur deux lignes")),
    ('<hc from="t3k" reach="hc send t3k">Tu as été lancé</hc>', ("t3k", None, "Tu as été lancé")),
    ("<hc>t44 harmonie-web-2 is blocked (permission). See: hc read t44</hc>",
     ("t44", "harmonie-web-2", "t44 harmonie-web-2 is blocked (permission). See: hc read t44")),
    ("<hc>t4 essai-template exited (exit 127). Last lines:\nsh: erreur\nRead: hc read t4</hc>",
     ("t4", "essai-template", "t4 essai-template exited (exit 127). Last lines:\nsh: erreur\nRead: hc read t4")),
    ("<hc>autre chose sans émetteur lisible</hc>", (None, None, "autre chose sans émetteur lisible")),
]


def _queued(prompt, kind="human"):
    return {"type": "attachment", "timestamp": "t", "attachment": {
        "type": "queued_command", "prompt": prompt, "origin": {"kind": kind}}}


def test_enveloppe_hc_entiere_est_un_item_hc():
    from herdr_web.chat import _parse
    for texte, (de, nom, corps) in FORMES_HC:
        for ligne in (json.loads(_user(texte)), _queued(texte)):  # ligne user, et message mis en file
            [i] = _parse(ligne)
            assert (i.role, i.de, i.nom, i.text) == ("hc", de, nom, corps), texte


def test_enveloppe_hc_parmi_du_texte_reste_a_user():
    from herdr_web.chat import _parse
    for texte in ('regarde ce message :\n<hc from="t1">x</hc>',
                  '\n\n<pasted_content id="a247">\n<hc from="t3k">x</hc>\n</pasted_content>',
                  '<hc from="t1">x</hc> puis <hc from="t2">y</hc>',
                  '<hc from="t1">pas fermée', 'hc from="t1">x</hc>'):
        [i] = _parse(json.loads(_user(texte)))
        assert (i.role, i.de) == ("moi", None), texte
        [i] = _parse(_queued(texte))
        assert i.role == "moi", texte


def test_hc_via_le_lecteur(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(_user("salut") + _user('<hc from="t4s design-reglages">fini</hc>'))
    a, b = ChatReader(f).tail()
    assert (a.role, b.role, b.de, b.nom) == ("moi", "hc", "t4s", "design-reglages")
