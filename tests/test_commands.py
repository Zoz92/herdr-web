import json
import logging

from herdr_web.commands import CommandIndex, frontmatter


def _skill(d, nom, desc, brut=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(brut if brut is not None else
                                f"---\nname: {nom}\ndescription: {desc}\n---\n\n# corps\n")


def _arbo(tmp_path):
    home = tmp_path / "home"
    c = home / ".claude"
    _skill(c / "skills" / "fin-de-session", "fin-de-session", "Clôture la session")
    _skill(c / "skills" / "sans-front", "", "", brut="# pas de frontmatter\n")
    _skill(c / "skills" / "casse", "", "", brut="---\nname: [non fermé\n")
    (c / "commands").mkdir(parents=True)
    (c / "commands" / "deploie.md").write_text("---\ndescription: \"Déploie le service\"\n---\ncorps\n")
    (c / "commands" / "nu.md").write_text("Juste un corps sans frontmatter.\n")
    # deux plugins installés, un seul activé
    for nom, ver in (("superpowers", "7.0.2"), ("frontend-design", "1.1.1")):
        racine = c / "plugins" / "cache" / "mk" / nom / ver
        _skill(racine / "skills" / "brainstorming", "brainstorming", f"Idées de {nom}")
        (racine / "commands").mkdir(parents=True)
        (racine / "commands" / "plan.md").write_text("---\ndescription: Planifie\n---\n")
    (c / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        f"{nom}@mk": [{"scope": "user", "installPath": str(c / "plugins" / "cache" / "mk" / nom / ver)}]
        for nom, ver in (("superpowers", "7.0.2"), ("frontend-design", "1.1.1"))}}))
    (c / "settings.json").write_text(json.dumps({"enabledPlugins": {
        "superpowers@mk": True, "frontend-design@mk": False}}))
    projet = tmp_path / "projet"
    _skill(projet / ".claude" / "skills" / "deploy-local", "deploy-local", "Déploiement du projet")
    (projet / ".claude" / "commands").mkdir(parents=True)
    (projet / ".claude" / "commands" / "test-rapide.md").write_text("---\ndescription: Tests ciblés\n---\n")
    return home, projet


def test_inventaire_par_source(tmp_path, caplog):
    home, projet = _arbo(tmp_path)
    idx = CommandIndex(home=home)
    with caplog.at_level(logging.INFO, logger="herdr_web.commands"):
        cmds = idx.get(str(projet))
    par = {}
    for c in cmds:
        par.setdefault(c["source"], set()).add(c["name"])
    assert par["user"] == {"fin-de-session", "deploie", "nu"}
    assert par["plugin"] == {"superpowers:brainstorming", "superpowers:plan"}  # pas le plugin désactivé
    assert par["project"] == {"deploy-local", "test-rapide"}
    assert len(par["builtin"]) > 100 and {"model", "resume", "config", "clear"} <= par["builtin"]
    d = {c["name"]: c for c in cmds}
    assert d["fin-de-session"]["description"] == "Clôture la session"
    assert d["deploie"]["description"] == "Déploie le service"
    assert d["model"]["interactive"] and d["resume"]["interactive"] and d["config"]["interactive"]
    assert not d["clear"]["interactive"]
    assert {"model opus", "model sonnet", "model haiku"} <= set(d)  # formes avec argument
    assert not d["model opus"]["interactive"]
    trace = " ".join(r.getMessage() for r in caplog.records)
    assert "builtin=" in trace and "user=3" in trace and "plugin=2" in trace and "project=2" in trace


def test_frontmatter_absent_ou_casse_ignore(tmp_path):
    home, projet = _arbo(tmp_path)
    noms = {c["name"] for c in CommandIndex(home=home).get(str(projet))}
    assert "sans-front" not in noms and "casse" not in noms
    assert frontmatter("pas de frontmatter") is None
    assert frontmatter("---\nname: x\n") is None  # jamais fermé
    assert frontmatter("---\nname: x\ndescription: >-\n  plié\n  sur deux lignes\n---\n") == {
        "name": "x", "description": "plié sur deux lignes"}


def test_cache_60_s(tmp_path):
    home, projet = _arbo(tmp_path)
    t = {"now": 0.0}
    idx = CommandIndex(home=home, clock=lambda: t["now"])
    avant = len(idx.get(str(projet)))
    _skill(home / ".claude" / "skills" / "neuve", "neuve", "Neuve")
    t["now"] = 59
    assert len(idx.get(str(projet))) == avant
    t["now"] = 61
    assert len(idx.get(str(projet))) == avant + 1


def test_rien_ne_casse_sans_claude(tmp_path):
    cmds = CommandIndex(home=tmp_path / "vide").get(str(tmp_path / "nulle-part"))
    assert {c["source"] for c in cmds} == {"builtin"}
