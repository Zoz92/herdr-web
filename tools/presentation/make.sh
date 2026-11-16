#!/usr/bin/env bash
# Fabrique docs/media/agents.gif et docs/media/phone.png depuis la page de ce dépôt et des données
# synthétiques (faux-core.js). Voir README.md à côté.
set -euo pipefail
ICI=$(cd "$(dirname "$0")" && pwd)
RACINE=$(cd "$ICI/../.." && pwd)
: "${CHROMIUM:?CHROMIUM = path to a Chromium binary}"
: "${NODE_PATH:?NODE_PATH = a node_modules folder holding playwright-core}"
export NODE_PATH
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$RACINE/docs/media"

node "$ICI/capture.js" "$CHROMIUM" frames "$TMP/f"
# palette calculée sur les seules zones qui bougent, puis GIF en boucle infinie (images en rectangles de diff)
ffmpeg -loglevel error -y -framerate 12.5 -i "$TMP/f/f%04d.png" \
  -vf "palettegen=max_colors=128:stats_mode=diff" "$TMP/palette.png"
ffmpeg -loglevel error -y -framerate 12.5 -i "$TMP/f/f%04d.png" -i "$TMP/palette.png" \
  -lavfi "[0:v][1:v]paletteuse=dither=none:diff_mode=rectangle" -loop 0 "$RACINE/docs/media/agents.gif"

node "$ICI/capture.js" "$CHROMIUM" phone "$RACINE/docs/media/phone.png"
ls -l "$RACINE/docs/media"
