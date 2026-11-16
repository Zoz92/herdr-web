import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_manifeste_valide():
    m = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert m["name"] == "herdr"
    assert m["display"] == "standalone"
    assert m["start_url"] == "/"
    tailles = {i["sizes"]: i for i in m["icons"]}
    assert set(tailles) >= {"192x192", "512x512", "any"}
    for i in m["icons"]:
        f = STATIC / Path(i["src"]).name
        assert f.is_file()
        if i["type"] == "image/png":
            octets = f.read_bytes()
            assert octets[:8] == b"\x89PNG\r\n\x1a\n"
            assert int.from_bytes(octets[16:20], "big") == int(i["sizes"].split("x")[0])  # IHDR
            assert i["purpose"] == "any maskable"
        else:
            assert i["type"] == "image/svg+xml" and f.name == "herdr.svg"


def test_favicon_et_apple_touch_icon():
    html = (STATIC / "index.html").read_text()
    assert '<link rel="icon" href="/static/herdr.svg" type="image/svg+xml">' in html
    assert 'rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in html
    octets = (STATIC / "apple-touch-icon.png").read_bytes()
    assert octets[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(octets[16:20], "big") == 180
    assert '"/static/apple-touch-icon.png"' in (STATIC / "sw.js").read_text()


def test_inputs_capture_presents():
    html = (STATIC / "index.html").read_text()
    assert 'accept="image/*"' in html and 'accept="audio/*"' in html
    assert html.count("capture") >= 2


def test_pas_de_innerhtml_ni_stockage_navigateur():
    fichiers = sorted(STATIC.glob("*.js"))
    assert {f.name for f in fichiers} >= {"app.js", "md.js", "sw.js", "commandes.js", "palette.js"}
    for f in fichiers:
        js = f.read_text()
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", js), f.name
        assert "sessionStorage" not in js, f.name
        # localStorage : l'état replié du panneau (point 16) et la taille du fil (point 38), la largeur du panneau, jamais le jeton
        usages = re.findall(r"localStorage\.(\w+)\(([^,)]*)", js)
        assert all(u[0] in ("getItem", "setItem", "removeItem") and u[1] == "k" for u in usages), usages
        if usages:
            assert "lireStockage(CLE_COTE)" in js and "ecrireStockage(CLE_COTE" in js
            assert "lireStockage(CLE_TAILLE)" in js and "ecrireStockage(CLE_TAILLE" in js
            apres = js.split("const effacerStockage")[1]
            # spec 2026-11-15 : mode de tri et plis de la zone Agents, préférences de l'appareil ;
            # réglages (roue crantée) : notifications Needs you et sections masquées, par appareil ;
            # dernière rubrique ouverte du panneau de réglages (brief design-reglages) ;
            # raccourci de la palette de commandes, choisi par appareil (brief palette)
            assert not re.search(r"(lire|ecrire|effacer)Stockage\((?!CLE_COTE|CLE_TAILLE|CLE_LARGEUR|CLE_TRI|CLE_PLIS"
                                 r"|CLE_NOTIF\b|CLE_MASQUE_TERMINAUX\b|CLE_RUBRIQUE\b|CLE_RACCOURCI\b|CLE_RACCOURCI_NETTOYER\b|CLE_NETTOYER\b)", apres)
        assert "console." not in js, f.name  # le jeton ne passe par aucun journal


def test_image_par_la_route():
    js = (STATIC / "app.js").read_text()
    assert '"/api/image?p=" + encodeURIComponent(' in js
    assert "data:image" not in js


def test_barre_de_saisie_sans_emoji():
    html = (STATIC / "index.html").read_text()
    assert not re.search("[\U0001F300-\U0001FAFF]", html)  # 📷 🎤 remplacés par des SVG
    assert html.count("<svg") >= 4
    galerie = re.search(r'<input id="in-galerie"[^>]*>', html).group(0)
    photo = re.search(r'<input id="in-photo"[^>]*>', html).group(0)
    assert 'accept="image/*"' in galerie and "capture" not in galerie
    assert 'accept="image/*"' in photo and 'capture="environment"' in photo
    assert re.search(r'<button id="micro"[^>]*type="button"', html)
    assert html.index("/static/md.js") < html.index("/static/app.js")


def test_micro_maintenu():
    js = (STATIC / "app.js").read_text()
    for geste in ("pointerdown", "pointerup", "pointercancel", "setPointerCapture", "MediaRecorder"):
        assert geste in js, geste
    assert "VOCAL_MIN_MS = 500" in js
    css = (STATIC / "style.css").read_text()
    assert re.search(r"#micro\s*\{[^}]*touch-action:\s*none", css)


def test_coquille_en_cache_porte_md_js():
    sw = (STATIC / "sw.js").read_text()
    for f in ("md.js", "commandes.js", "palette.js", "herdr.svg"):
        assert f'"/static/{f}"' in sw, f


def test_logo_herdr_statique_sans_script():
    svg = (STATIC / "herdr.svg").read_text()
    assert svg.startswith("<svg") and "<script" not in svg.lower() and "foreignobject" not in svg.lower()
    assert not re.search(r"\son\w+\s*=", svg)  # aucun gestionnaire d'événement
    assert not re.search(r"href=\"(?!#)", svg)  # références internes seulement


def test_palette_catppuccin_mocha():
    css = (STATIC / "style.css").read_text()
    racine = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    for couleur in ("#1e1e2e", "#cdd6f4", "#cba6f7", "#89b4fa", "#a6e3a1", "#f38ba8"):
        assert couleur in racine.lower(), couleur


def test_max_voix_de_la_page_tient_sous_max_body_du_serveur():
    """Mineur 3 : un vocal à la limite de la page dépassait MAX_BODY une fois mis en multipart."""
    import re
    from herdr_web.server import MAX_BODY
    src = (Path(__file__).parent.parent / "static" / "app.js").read_text()
    m = re.search(r"^const MAX_VOIX = ([0-9 *+-]+);", src, re.M)
    assert m, "MAX_VOIX introuvable"
    valeur = eval(m.group(1), {"__builtins__": {}})  # chiffres et opérateurs seulement (motif)
    assert valeur == MAX_BODY - 64 * 1024


def test_nodelist_jamais_traitee_comme_un_tableau():
    # un vrai NodeList n'a que forEach : .map/.find y lèvent TypeError. Le faux DOM du banc rend un
    # tableau, donc seul le navigateur le voyait — la palette restait vide (brief palette, 2026-11-15)
    for f in sorted(STATIC.glob("*.js")):
        fautes = re.findall(r"querySelectorAll\([^()]*\)\.(?:map|find|filter|some|every|reduce|flatMap|includes)\b",
                            f.read_text())
        assert fautes == [], (f.name, fautes)
