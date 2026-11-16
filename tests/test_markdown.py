"""Rendu Markdown de la page (static/md.js), joué sous node avec un faux DOM (tests/md_banc.js).

Le faux DOM lève sur innerHTML/outerHTML/insertAdjacentHTML : un rendu qui y passerait échoue."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

BANC = Path(__file__).parent / "md_banc.js"
LIEN = 'rel="noopener noreferrer" target="_blank"'

CAS = [
    ("Bonjour **monde** et *toi* et `code`",
     "<p>Bonjour <strong>monde</strong> et <em>toi</em> et <code>code</code></p>"),
    ("# Titre\n## Sous-titre", "<h1>Titre</h1><h2>Sous-titre</h2>"),
    ("- a\n- b **c**", "<ul><li>a</li><li>b <strong>c</strong></li></ul>"),
    ("* un\n+ deux", "<ul><li>un</li><li>deux</li></ul>"),
    ("1. un\n2. deux", "<ol><li>un</li><li>deux</li></ol>"),
    ("3. trois\n4. quatre", '<ol start="3"><li>trois</li><li>quatre</li></ol>'),
    ("- a\n  - b\n- c", "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"),
    ("```py\nx = 1 < 2\n  y\n```", "<pre><code>x = 1 &lt; 2\n  y</code></pre>"),
    ("```\ncode sans fin", "<pre><code>code sans fin</code></pre>"),
    ("[site](https://ex.org/a?b=1)", f'<p><a href="https://ex.org/a?b=1" {LIEN}>site</a></p>'),
    ("[a](http://x.y)", f'<p><a href="http://x.y" {LIEN}>a</a></p>'),
    ("[**b**](https://x.y)", f'<p><a href="https://x.y" {LIEN}><strong>b</strong></a></p>'),
    ("[x](javascript:alert(1))", "<p>[x](javascript:alert(1))</p>"),
    ("[x](data:text/html,1)", "<p>[x](data:text/html,1)</p>"),
    ("voir https://ex.org/p.", f'<p>voir <a href="https://ex.org/p" {LIEN}>https://ex.org/p</a>.</p>'),
    ("<b>gras</b> <script>x</script>",
     "<p>&lt;b&gt;gras&lt;/b&gt; &lt;script&gt;x&lt;/script&gt;</p>"),
    ("| a | b |\n|---|:-:|\n| 1 | **2** |",
     "<table><thead><tr><th>a</th><th>b</th></tr></thead>"
     "<tbody><tr><td>1</td><td><strong>2</strong></td></tr></tbody></table>"),
    ("a | b\n--- | ---\n1 | 2",
     "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>"),
    ("ligne un\nligne deux\n\npara deux", "<p>ligne un<br>ligne deux</p><p>para deux</p>"),
    ("le fichier mon_fichier_test.py", "<p>le fichier mon_fichier_test.py</p>"),
    ("_ital_ et ~~barré~~", "<p><em>ital</em> et <del>barré</del></p>"),
    ("**gras non fermé", "<p>**gras non fermé</p>"),
    ("`**pas gras**`", "<p><code>**pas gras**</code></p>"),
    ("> citation\n> suite", "<blockquote><p>citation<br>suite</p></blockquote>"),
    ("avant\n\n---\n\naprès", "<p>avant</p><hr><p>après</p>"),
    ("Texte\n- puce", "<p>Texte</p><ul><li>puce</li></ul>"),
    ("", ""),
]


def _rendre(textes):
    node = shutil.which("node")
    assert node, "node absent : il est dans le devShell (flake.nix), lancer via nix develop"
    r = subprocess.run([node, str(BANC)], input=json.dumps(textes), capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def rendus():
    return dict(zip([c[0] for c in CAS], _rendre([c[0] for c in CAS])))


@pytest.mark.parametrize("source,attendu", CAS, ids=[f"cas{i}" for i in range(len(CAS))])
def test_rendu_markdown(rendus, source, attendu):
    assert rendus[source] == attendu


def test_le_faux_dom_mord_sur_innerhtml(tmp_path):
    """Témoin du banc : un rendu qui passe par innerHTML le fait échouer."""
    src = (Path(__file__).parent.parent / "static" / "md.js").read_text()
    faux = src.replace("doc.createElement(\"p\")", "(function(){ const e = doc.createElement(\"p\");"
                       " e.innerHTML = \"x\"; return e; })()", 1)
    assert faux != src
    (tmp_path / "static").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "static" / "md.js").write_text(faux)
    (tmp_path / "tests" / "md_banc.js").write_text(BANC.read_text())
    r = subprocess.run([shutil.which("node"), str(tmp_path / "tests" / "md_banc.js")],
                       input=json.dumps(["un paragraphe"]), capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "innerHTML interdit" in r.stderr
