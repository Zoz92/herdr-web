"""Génère les icônes PNG de la PWA depuis static/herdr.svg (le logo de herdr-app) avec rsvg-convert :
fond #1e1e2e (Catppuccin Mocha base), marge de sécurité de 10 % sur chaque bord (icône « maskable »).

    nix shell nixpkgs#librsvg -c python3 tools/icones.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
FOND = "#1e1e2e"
MARGE = 0.10
TAILLES = {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}


def main() -> int:
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        print("rsvg-convert absent : nix shell nixpkgs#librsvg -c python3 tools/icones.py", file=sys.stderr)
        return 1
    for nom, n in TAILLES.items():
        bord = round(n * MARGE)
        cote = n - 2 * bord
        subprocess.run([rsvg, "--width", str(cote), "--height", str(cote),
                        "--page-width", str(n), "--page-height", str(n),
                        "--left", str(bord), "--top", str(bord),
                        "--background-color", FOND, "--format", "png",
                        "--output", str(STATIC / nom), str(STATIC / "herdr.svg")], check=True)
        print(nom, n, "px, marge", bord, "px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
