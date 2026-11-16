# AGENTS.md — herdr-web

Instructions for coding agents (Claude Code, Codex, opencode…) working with this repository. Two jobs:
**[install it](#installing-herdr-web)** for a user, or **[contribute](#contributing)** to it.

herdr-web is a small Python web server that drives herdr-core agents from a phone. It runs on the
machine where the agents run, as a `systemd --user` unit, over HTTPS, behind a single access token,
and is meant to be reached from a private network (Tailscale or a LAN), never from the Internet.
`README.md` is the human-facing reference; this file is the same thing in checklist form.

## Safety rules (both jobs)

- **Never print, log, echo or paste the access token** (`HERDR_WEB_TOKEN`) or the push key
  (`HERDR_WEB_VAPID_KEY`), not even truncated, not even to debug. They live in
  `~/.config/herdr-web/env` (mode `600`). Do not `cat` that file; read only the non-secret keys.
- **Ask the user before opening a port beyond their private network**: any `--bind` other than
  `127.0.0.1` or a Tailscale/LAN address (`0.0.0.0` included), any firewall change, any port
  forwarding, any public DNS name. The server must not be exposed to the Internet.
- **Ask before doing anything outside the user's home directory** or needing `sudo`. The default
  install needs neither.
- **On NixOS, configuration is declarative**: do not `nix-env -i` or edit `/etc`. Use `nix develop`
  for the Python environment, `nix profile install nixpkgs#<pkg>` for a missing tool, or tell the
  user which line to add to their system configuration.
- Never delete `~/.config/herdr-web` without being asked: it holds the token. Uninstalling is a
  user decision.
- The installer is a dry run unless given `--go`. Always run the dry run first and show its output.

## Installing herdr-web

### 1. Detect the prerequisites (read-only)

    uname -sm                                   # Linux required; x86_64 for the Nix flake
    systemctl --user is-system-running          # systemd user units must work
    command -v hc                               # herdr-core's command: required
    command -v claude                           # Claude Code: the agents shown are its sessions
    command -v ffmpeg                           # voice notes only (optional)
    command -v tailscale && tailscale ip -4     # private network (strongly advised)
    command -v nix; python3 --version           # Nix flake, or Python 3 + a virtualenv

If `hc` or `systemctl --user` is missing, stop and tell the user: herdr-web cannot work without
them. Missing `ffmpeg` or Tailscale is not blocking (voice and phone access respectively).

### 2. Clone, then choose the network address

    git clone <repository URL> ~/herdr-web && cd ~/herdr-web

By default the server listens on `127.0.0.1:10370`, which a phone cannot reach. For a phone over
Tailscale, bind the machine's Tailscale address (`tailscale ip -4`). For a LAN, bind the LAN
address, **after asking the user**. If the phone will use another name or address than the bind
(a DNS name, a LAN IP behind a wildcard bind), list it with `--hosts` (`host:port`, comma
separated): the server answers only requests whose `Host` header is in that list.

### 3. Dry run, then install

    python3 deploy/installer.py --bind 100.x.y.z:10370 --cert-autosigne        # dry run: writes nothing
    nix develop -c python3 deploy/installer.py --go --bind 100.x.y.z:10370 --cert-autosigne

Without Nix, build the environment first and run the installer with it:

    python3 -m venv ~/.local/share/herdr-web/pyenv
    ~/.local/share/herdr-web/pyenv/bin/pip install pywebpush cryptography
    ~/.local/share/herdr-web/pyenv/bin/python deploy/installer.py --go --bind 100.x.y.z:10370 --cert-autosigne

Local mode (no password vault) is the default: the installer generates the token and the push key
in memory, writes them to `~/.config/herdr-web/env` (`600`, folder `700`), creates a self-signed
certificate in `~/.config/herdr-web/tls/`, installs `~/.config/systemd/user/herdr-web.service` and
starts it. **The token is printed once, at the end of the `--go` run.** Do not copy it into your
reply, a file or a log: tell the user it was printed in their terminal, to store it in a password
manager, and how to read it again themselves (see step 5). If the run is captured by your tooling,
tell the user so; the token is then in your transcript, and a new one should be generated
(see "Changing the token" in `README.md`).

Certificate: `--cert-autosigne` is enough to start, but the phone's browser warns once and **push
notifications need a certificate the browser trusts**. With Tailscale HTTPS enabled for the tailnet,
put a real one at `~/.config/herdr-web/tls/cert.pem` and `key.pem` **before** installing (no
`--cert-autosigne`): `tailscale cert --cert-file cert.pem --key-file key.pem <machine>.<tailnet>.ts.net`
from that folder (`chmod 600 key.pem`), and pass the name in `--hosts`. A Tailscale certificate lasts
90 days and nothing renews it: tell the user.

Optional vault mode (`--org-id`, `--collection-id`, a `bw-unlocked` wrapper in the `PATH`): only if
the user asks; it is described in `README.md`.

### 4. Verify the installation

    systemctl --user is-active herdr-web                       # expect: active
    journalctl --user -u herdr-web -n 20 --no-pager            # no traceback, no token
    curl -sk -o /dev/null -w '%{http_code}\n' https://100.x.y.z:10370/api/agents   # expect: 401

The decisive check is that the server **refuses without the cookie**: `/api/agents` (any `/api/`
route) requested without the access cookie must answer `401`, never `200`. Use the exact address
given in `--bind` (a `Host` that is in `--hosts`, otherwise the answer is `403`). Do **not** send
the token to check the other way: logging in is the user's step.
If the unit is not `active`, read the journal, fix, and run
the installer again: it is idempotent (an existing env file keeps its token and key).

To keep the service running after logout: `loginctl enable-linger $USER`.

### 5. Hand over to the user

Tell them: open `https://<address>:10370/` on the phone (same private network), accept the
certificate warning if self-signed, type the token once. To read the token again, **they** run:

    grep '^HERDR_WEB_TOKEN=' ~/.config/herdr-web/env | cut -d= -f2-

Voice input is optional and local: the separate open-source app
[Handy](https://github.com/cjpais/Handy), found through `~/.config/autostart/Handy.desktop`, plus
`ffmpeg`. Without it everything works except the microphone button. Do not install Handy unless asked.

### Uninstalling (only on request)

    systemctl --user disable --now herdr-web.service
    rm ~/.config/systemd/user/herdr-web.service && systemctl --user daemon-reload
    rm -r ~/.config/herdr-web ~/.local/state/herdr-web ~/.local/share/herdr-web

then delete the cloned repository. Nothing else is installed.

## Contributing

### Layout

- `herdr_web/` — the Python server (standard library plus `pywebpush` and `cryptography`).
  `server.py` is the HTTP layer; `core.py` talks to herdr-core through `hc connect`;
  `agents.py`, `chat.py`, `push.py`, `reglages.py`, `statusbar.py`… hold one concern each.
- `static/` — the page: plain `index.html`, `app.js`, `style.css`, no build step, no framework.
  `sw.js` is its service worker; `static/i18n/` holds the texts.
- `deploy/` — the installer and the systemd unit. `tools/` — helper scripts.
- `tests/` — pytest suite; page tests run the real `static/app.js` under node
  (`tests/page_banc.js`, a faithful fake DOM), driven by `tests/test_page*.py`.

### Run the tests

    nix develop -c python3 -m pytest -q

Or in a virtualenv with `pywebpush`, `cryptography`, `pytest` and **node** on the `PATH` (the page
tests need it). Run the whole suite before proposing a change and report the actual count of
passed tests, not "all green". A change that touches code gets a test that fails without it.

### Rules that the tests enforce

- **Bump `CACHE` in `static/sw.js`** (`herdr-coquille-vNN`, +1) in every change that touches a file
  under `static/`. Without it, installed phones keep serving the old page from the service worker
  cache. A file added to `static/` and needed offline also goes into the `COQUILLE` list there.
- **Internationalisation.** The page is in English and French. Every visible string goes through
  `t("key")` (page) or `herdr_web/textes.py` (the few server-rendered texts); never a literal
  string in `app.js` or `index.html`. `static/i18n/en.js` and `fr.js` must have **the same keys
  and the same `{parameters}`**; `tests/test_i18n.py` fails on a missing key, a different
  parameter, a hard-coded visible text, or French text appearing in the English page.
- **No secret in the code, the tests or the logs.** Tests use throw-away directories
  (`tests/conftest.py` redirects the settings file and `/proc`); a test must never reach a real
  herdr-core, the user's real `~/.config/herdr-web`, or the network.
- Page behaviour changes are tested in the node bench (`tests/page_banc.js` scenarios), not only by
  grepping the source.

### Style

Match the surrounding code: small functions, standard library first, no new dependency without a
strong reason, comments that explain *why* (a measured behaviour, an incident), not *what*. Keep
the page free of build tooling. Commit messages: a short imperative subject, then why.
