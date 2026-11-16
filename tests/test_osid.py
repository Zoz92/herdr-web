from pathlib import Path

import pytest

from herdr_web import osid

STATIC = Path(__file__).resolve().parent.parent / "static"

# (ID, clé attendue) : chaque OS de la liste validée
CONNUS = [("ubuntu", "ubuntu"), ("debian", "debian"), ("fedora", "fedora"), ("arch", "arch"),
          ("linuxmint", "mint"), ("pop", "pop"), ("manjaro", "manjaro"), ("opensuse-tumbleweed", "opensuse"),
          ("opensuse-leap", "opensuse"), ("nixos", "nixos"), ("glfos", "glfos"), ("endeavouros", "endeavouros"),
          ("cachyos", "cachyos"), ("bazzite", "bazzite"), ("steamos", "steamos"), ("gentoo", "gentoo"),
          ("alpine", "alpine"), ("void", "void"), ("kali", "kali"), ("elementary", "elementary"),
          ("zorin", "zorin"), ("rhel", "rhel"), ("rocky", "rocky"), ("almalinux", "almalinux"),
          ("centos", "centos"), ("raspbian", "raspios")]


@pytest.mark.parametrize("ident,cle", CONNUS)
def test_chaque_id_de_la_liste(ident, cle):
    assert osid.resoudre({"ID": ident}) == cle


def test_id_like_dans_l_ordre():
    assert osid.resoudre({"ID": "inconnu", "ID_LIKE": "truc ubuntu debian"}) == "ubuntu"
    assert osid.resoudre({"ID": "dérivé", "ID_LIKE": "rhel fedora"}) == "rhel"


def test_id_connu_l_emporte_sur_id_like():
    assert osid.resoudre({"ID": "bazzite", "ID_LIKE": "fedora"}) == "bazzite"


def test_linux_inconnu_est_tux():
    assert osid.resoudre({"ID": "exotique"}) == "linux"
    assert osid.resoudre({"ID": "exotique", "ID_LIKE": "autre"}) == "linux"


def test_illisible_est_generique():
    assert osid.resoudre({}) == ""
    assert osid.resoudre({"ID": "ubuntu"}, "FreeBSD") == ""


def test_darwin_et_windows():
    assert osid.resoudre({}, "Darwin") == "macos"
    assert osid.resoudre({"ID": "ubuntu"}, "Windows") == "windows"


def test_lire_guillemets_et_casse():
    assert osid.lire('NAME="X"\nID="Linuxmint"\nID_LIKE="Ubuntu debian"\n') == {
        "NAME": "x", "ID": "linuxmint", "ID_LIKE": "ubuntu debian"}


def test_wsl(tmp_path):
    v = tmp_path / "version"
    v.write_text("Linux version 5.15 (root@x) microsoft-standard-WSL2")
    assert osid.est_wsl(v, {}) is True
    v.write_text("Linux version 6.1 (nixbld)")
    assert osid.est_wsl(v, {}) is False
    assert osid.est_wsl(v, {"WSL_DISTRO_NAME": "Ubuntu"}) is True
    assert osid.est_wsl(tmp_path / "absent", {}) is False


def test_cette_machine_wsl_garde_la_distribution(tmp_path, monkeypatch):
    rel = tmp_path / "os-release"
    rel.write_text("ID=ubuntu\n")
    monkeypatch.setattr(osid, "est_wsl", lambda *a, **k: True)
    assert osid.cette_machine(rel, lambda: "Linux") == ("ubuntu", True)
    assert osid.cette_machine(rel, lambda: "Darwin") == ("macos", False)  # pas de WSL hors Linux
    assert osid.cette_machine(tmp_path / "absent", lambda: "Linux") == ("", False)


def test_chaque_icone_presente_et_saine():
    for cle in osid.CLES:
        f = STATIC / osid.fichier(cle)
        assert f.is_file() and f.stat().st_size > 100, cle
        if f.suffix == ".svg":
            t = f.read_text().lower()
            assert t.startswith("<svg") and "viewbox" in t and "<script" not in t and "http" not in t.replace(
                "http://www.w3.org/2000/svg", ""), cle


def test_aucune_icone_orpheline():
    attendus = {Path(osid.fichier(c)).name for c in osid.CLES} | {"LICENSE-simple-icons.txt", "os-logo.png"}
    assert {p.name for p in (STATIC / "os").iterdir()} <= attendus


def test_icones_dans_la_coquille_et_licence():
    sw = (STATIC / "sw.js").read_text()
    for cle in osid.CLES:
        assert f'"/static/{osid.fichier(cle)}"' in sw, cle
    assert "16.38.0" in (STATIC / "os" / "LICENSE-simple-icons.txt").read_text()


def test_page_lit_os_et_wsl():
    js = (STATIC / "app.js").read_text()
    assert "logoOs(a)" in js and "a.os_wsl" in js and '"/static/os/"' in js


def _lum(h):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return .2126 * r + .7152 * g + .0722 * b


def test_contraste_de_chaque_icone_sur_fond_sombre():
    import re
    fond = _lum("1E1E2E")
    for cle in osid.CLES:
        f = STATIC / osid.fichier(cle)
        if f.suffix != ".svg":
            continue  # glfos : PNG éclairci par CSS (mesuré ≈ 4,4:1)
        h = re.search(r"#([0-9A-Fa-f]{6})", f.read_text()).group(1)
        assert (_lum(h) + .05) / (fond + .05) >= 4.5, (cle, h)


def test_badge_wsl_visible():
    css = (STATIC / "style.css").read_text()
    assert ".os-wsl .os-badge" in css and "box-shadow" in css.split(".os-wsl .os-badge")[1].split("}")[0]


def test_easter_egg_quantique(tmp_path):
    rel = tmp_path / "os-release"
    rel.write_text("ID=ubuntu\n")
    for nom in ("quantum-01", "Mon-QPU", "ordi-quantique", "qubit"):
        assert osid.cette_machine(rel, lambda: "Linux", lambda n=nom: n) == ("quantum", False), nom
    assert osid.cette_machine(rel, lambda: "Linux", lambda: "serveur") == ("ubuntu", False)


def test_os_release_non_utf8_ne_plante_pas(tmp_path):
    rel = tmp_path / "os-release"
    rel.write_bytes(b"\xff\xfe\x00NAME=x\nID=ubuntu\n")
    assert osid.cette_machine(rel, lambda: "Linux", lambda: "srv") == ("ubuntu", False)
    rel.write_bytes(b"\xff\xfe\xfa\xfb")  # rien de lisible : générique, pas d'exception
    assert osid.cette_machine(rel, lambda: "Linux", lambda: "srv") == ("", False)
