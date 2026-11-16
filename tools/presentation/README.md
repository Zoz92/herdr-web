# Presentation media

`make.sh` builds the two images of the top-level README from **this** repository's page (`static/`)
and **synthetic** data only — no agent, server or network is involved:

- `docs/media/agents.gif`: the Agents list at 1120×1080, 105 frames at 12.5 fps (8.4 s loop). Every CSS
  animation is paused and set frame by frame; each one is sped up or slowed down by a few percent so
  that it runs a whole number of cycles in the loop, which makes the loop seamless.
- `docs/media/phone.png`: the page at phone width (390 px, device scale 2) inside a drawn phone frame.

`faux-core.js` answers the page's `/api/*` requests with invented agents, folders, branches and
machines; `capture.js` serves `static/` to headless Chromium on a fictitious origin and refuses (and
reports) any other request.

Requirements: Node.js, ffmpeg, a Chromium binary, and the `playwright-core` package (tested with
1.49.1):

    npm install --prefix /tmp/pw playwright-core@1.49.1
    CHROMIUM=$(command -v chromium) NODE_PATH=/tmp/pw/node_modules tools/presentation/make.sh
