#!/usr/bin/env python3
"""herdr-web local installer (systemd user unit).

Dry run by default: lists the steps, checks preconditions read-only, writes NOTHING. `--go` executes.

Two modes for the secrets (access token + VAPID key, generated in memory):
  - local (default): written to ~/.config/herdr-web/env (file 600, folder 700); the token is shown
    ONCE at the end of a successful run, with the command to read it back from that file;
  - vault (`--org-id` + `--collection-id`, Vaultwarden through `bw-unlocked`): "vault first" — the
    item `herdr-web` is created, read back and compared by fingerprint; only then is the env written.
    Nothing secret is printed in this mode: fingerprints [:12] and lengths only.

Run:  python3 deploy/installer.py                     (dry run, no dependency)
      nix develop -c python3 deploy/installer.py --go --cert-autosigne
      ~/.local/share/herdr-web/pyenv/bin/python deploy/installer.py --go --cert-autosigne   (no Nix)
(`cryptography` is required as soon as a key or a certificate is generated.)
"""
import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ITEM = "herdr-web"
FIELDS = ("HERDR_WEB_TOKEN", "HERDR_WEB_VAPID_KEY")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{48}$")
DEFAULTS = {"bind": "127.0.0.1:10370", "vapid_subject": "mailto:herdr-web@localhost"}
# NixOS: the unit template's PATH (/run/wrappers/bin, /run/current-system/sw/bin) is kept as is.
# Elsewhere it is replaced by GENERIC_PATH, preceded by the folders where hc and ffmpeg were found.
NIXOS_BIN = Path("/run/current-system/sw/bin")
GENERIC_PATH = "/usr/local/bin:/usr/bin:/bin:%h/.nix-profile/bin:%h/.local/bin"
PIP_DEPS = "pywebpush cryptography"


class Refus(Exception):
    """Deliberate stop; the message never carries a secret value."""

    def __init__(self, msg, rc=1):
        super().__init__(msg)
        self.rc = rc


def fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _need_go(go, what):
    if not go:
        raise RuntimeError(f"{what}: write called without --go")


def have_crypto():
    return importlib.util.find_spec("cryptography") is not None


class Chemins:
    def __init__(self, home: Path, repo: Path = REPO):
        self.home, self.repo = Path(home), Path(repo)
        self.cfg = self.home / ".config/herdr-web"
        self.env = self.cfg / "env"
        self.tls = self.cfg / "tls"
        self.cert, self.key = self.tls / "cert.pem", self.tls / "key.pem"
        self.state = self.home / ".local/state/herdr-web"
        self.pyenv = self.home / ".local/share/herdr-web/pyenv"
        self.unit_src = self.repo / "deploy/herdr-web.service"
        self.unit = self.home / ".config/systemd/user/herdr-web.service"


# ---------- real execution (never reached by the tests: exit points are injected) ----------

def real_run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL).returncode


def real_bw(args):
    """bw-unlocked, stdin closed; returns the output, which may contain values: never printed."""
    done = subprocess.run(["bw-unlocked", *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise Refus(f"bw-unlocked {' '.join(args[:2])}: rc={done.returncode}", 2)
    return done.stdout


# ---------- detection ----------

def split_hostport(entry):
    host, _, port = entry.strip().rpartition(":")
    return host.strip("[]"), port


def default_hosts(bind):
    """Host headers accepted by the server, derived from --bind: a loopback bind accepts
    localhost and 127.0.0.1, a wildcard bind the machine's name, any other address itself."""
    out = []
    for entry in bind.split(","):
        host, port = split_hostport(entry)
        if host in ("127.0.0.1", "::1", "localhost"):
            names = ["localhost", "127.0.0.1"]
        elif host in ("0.0.0.0", "::", ""):
            names = [socket.gethostname(), "localhost"]
        else:
            names = [host]
        out += [f"{n}:{port}" for n in names if f"{n}:{port}" not in out]
    return ",".join(out)


def cert_names(hosts):
    """Names for the self-signed certificate: the hosts of --hosts, without port, deduplicated."""
    names = []
    for entry in hosts.split(","):
        host, _ = split_hostport(entry)
        if host and host not in names:
            names.append(host)
    return names or ["localhost"]


def in_store(found):
    return bool(found) and str(found).startswith("/nix/store/")


def store_only(which):
    """Tools found only under /nix/store (typically through `nix develop`): that folder has no
    GC root and vanishes at the next nix-collect-garbage, so it is kept out of the unit."""
    return [(t, which(t)) for t in ("hc", "ffmpeg") if in_store(which(t))]


def unit_path(which, nixos_bin=NIXOS_BIN):
    """None on NixOS (template kept verbatim); otherwise a PATH for the unit."""
    if Path(nixos_bin).is_dir():
        return None
    dirs = []
    for tool in ("hc", "ffmpeg"):
        found = which(tool)
        if found and not in_store(found) and str(Path(found).parent) not in dirs:
            dirs.append(str(Path(found).parent))
    rest = [d for d in GENERIC_PATH.split(":") if d not in dirs]
    return ":".join(dirs + rest)


# ---------- generation in memory ----------

def generate_secrets():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    token = secrets.token_urlsafe(36)
    assert TOKEN_RE.match(token), "token outside the expected alphabet/length"
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode().strip()  # one form only: vault and env
    return {"HERDR_WEB_TOKEN": token, "HERDR_WEB_VAPID_KEY": pem}


# ---------- vault ----------

AUDIT = ("item possibly created — audit the vault (bw-unlocked list items --search herdr-web, "
         "then fingerprint) before any retry")


def vault_store(values, org_id, collection_id, bw, go):
    """Creates the item, reads it back, compares fingerprints. Returns the id. Writes to the vault: needs go."""
    _need_go(go, "vault_store")
    if not (org_id and collection_id):
        raise Refus("--org-id and --collection-id are both required: without them the item would be personal")
    bw(["sync"])  # without sync, a stale cache lets a duplicate be created
    try:
        found = json.loads(bw(["list", "items", "--search", ITEM]))
        assert isinstance(found, list)
    except Exception as e:
        if isinstance(e, Refus):
            raise
        raise Refus("UNDECIDABLE — item list empty or unreadable (session refused?); nothing created", 2) from None
    if any(i.get("name") == ITEM for i in found):
        raise Refus(f"an item \"{ITEM}\" already exists in the vault: inspect it, do not duplicate")
    item = json.loads(bw(["get", "template", "item"]))
    item.update({
        "type": 2, "name": ITEM, "secureNote": {"type": 0}, "login": None, "folderId": None,
        "organizationId": org_id, "collectionIds": [collection_id],
        "fields": [{"name": n, "value": values[n], "type": 1} for n in FIELDS],
    })
    b64 = base64.b64encode(json.dumps(item).encode()).decode()
    item_id = None
    try:  # from here the action may have happened: any failure is UNDECIDABLE, not a clean refusal
        created = json.loads(bw(["create", "item", b64]))  # carries the values: dropped except the id
        item_id = created.get("id")
        del created, item, b64
        if not item_id:
            raise Refus(f"UNDECIDABLE — creation returned no id; {AUDIT}", 2)
        back = json.loads(bw(["get", "item", item_id]))
        got = {f.get("name"): f.get("value") for f in back.get("fields") or []}
        for n in FIELDS:
            if n not in got or got[n] is None or fp(got[n]) != fp(values[n]):
                raise Refus(f"UNDECIDABLE — field {n} missing or with a different fingerprint "
                            f"(expected {fp(values[n])}); item {item_id}; nothing written to disk; {AUDIT}", 2)
    except Refus as e:
        if e.rc == 2 and "UNDECIDABLE" in str(e):
            raise
        raise Refus(f"UNDECIDABLE — {e}; {AUDIT}" + (f" (item {item_id})" if item_id else ""), 2) from None
    except Exception as e:
        raise Refus(f"UNDECIDABLE — failure after creation ({type(e).__name__}); {AUDIT}"
                    + (f" (item {item_id})" if item_id else ""), 2) from None
    return item_id


# ---------- disk ----------

def env_text(values, bind, hosts, subject, ch: Chemins):
    # PEM between single quotes: systemd reads such an EnvironmentFile verbatim, over several lines,
    # without escape sequences (man systemd.exec); a PEM has no single quote.
    pem = values["HERDR_WEB_VAPID_KEY"]
    assert "'" not in pem and pem == pem.strip()
    lines = [
        f"HERDR_WEB_TOKEN={values['HERDR_WEB_TOKEN']}",
        f"HERDR_WEB_VAPID_KEY='{pem}'",
        f"HERDR_WEB_BIND={bind}",
        f"HERDR_WEB_CERT={ch.cert}",
        f"HERDR_WEB_KEY={ch.key}",
        f"HERDR_WEB_STATE={ch.state}",
        f"HERDR_WEB_HOSTS={hosts}",
        f"HERDR_WEB_VAPID_SUBJECT={subject}",
    ]
    return "\n".join(lines) + "\n"


def parse_env(text):
    """Reader for the two forms written here (unquoted; multi-line single quotes)."""
    out, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if v.startswith("'") and not v.endswith("'"):
                while not lines[i].endswith("'"):
                    i += 1
                    v += "\n" + lines[i]
            out[k] = v[1:-1] if v.startswith("'") else v
        i += 1
    return out


# py_vapid (pywebpush) only accepts as `sub` mailto:<addr> or https://<host> WITHOUT PORT (measured):
# a subject `https://host:port` would make every notification fail.
SUBJECT_RE = re.compile(r"^(mailto:[^@\s]+@[^@\s]+|https://[A-Za-z0-9.-]+)$")
NON_SECRET = {"bind": "HERDR_WEB_BIND", "hosts": "HERDR_WEB_HOSTS", "vapid_subject": "HERDR_WEB_VAPID_SUBJECT"}


def _rewrite_lines(text, diffs):
    lines, out, i, done = text.split("\n"), [], 0, set()
    while i < len(lines):
        line = lines[i]
        k = line.split("=", 1)[0]
        if "=" in line and k in diffs:
            out.append(f"{k}={diffs[k]}")
            done.add(k)
        else:
            out.append(line)
            v = line.split("=", 1)[1] if "=" in line else ""
            if v.startswith("'") and not v.endswith("'"):  # multi-line block kept verbatim
                while not lines[i].endswith("'"):
                    i += 1
                    out.append(lines[i])
        i += 1
    while out and out[-1] == "":
        out.pop()
    out += [f"{k}={v}" for k, v in diffs.items() if k not in done]
    return "\n".join(out) + "\n"


def rewrite_env_keys(ch, diffs, go):
    """Rewrites only these non-secret keys; atomic, 600. The token and key fingerprints are read
    before and on the content TO BE WRITTEN: any difference => UNDECIDABLE, original file intact."""
    _need_go(go, "rewrite_env_keys")
    old = ch.env.read_text()
    new = _rewrite_lines(old, diffs)
    before, after = parse_env(old), parse_env(new)
    for n in FIELDS:
        if n not in before or fp(before[n]) != fp(after.get(n, "")):
            raise Refus(f"UNDECIDABLE — {n} would change in the env rewrite; "
                        f"{ch.env} left intact", 2)
    tmp = ch.env.with_name("env.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(new)
    os.replace(tmp, ch.env)


def write_private(path: Path, text: str, go):
    _need_go(go, f"write_private {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


ENV_AFTER_VAULT = ("the item is in the vault; remove ~/.config/herdr-web/env before any retry, then retry "
                   "reusing the item (or delete it after audit)")
ENV_AFTER_LOCAL = "remove ~/.config/herdr-web/env before any retry (nothing else holds these values)"


def write_env(values, bind, hosts, subject, ch, go, vault=True):
    _need_go(go, "write_env")
    after = ENV_AFTER_VAULT if vault else ENV_AFTER_LOCAL
    origin = "the vault" if vault else "memory"
    try:
        write_private(ch.env, env_text(values, bind, hosts, subject, ch), go)
    except OSError as e:
        raise Refus(f"UNDECIDABLE — env write failed ({type(e).__name__}), file possibly partial; "
                    f"{after}", 2) from None
    back = parse_env(ch.env.read_text())  # read back from disk against the value kept
    for n in FIELDS:
        if fp(back.get(n, "")) != fp(values[n]):
            raise Refus(f"UNDECIDABLE — {n} read back from disk with a fingerprint different from "
                        f"{origin}'s; {after}", 2)


def make_selfsigned(ch: Chemins, go, names=("localhost", "127.0.0.1")):
    _need_go(go, "make_selfsigned")
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    sans = []
    for n in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            sans.append(x509.DNSName(n))
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256()))
    write_private(ch.key, key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode(), go)
    write_private(ch.cert, cert.public_bytes(serialization.Encoding.PEM).decode(), go)


def unit_text(ch: Chemins, path=None):
    text = ch.unit_src.read_text().replace("@REPO@", str(ch.repo))
    assert "@REPO@" not in text
    if path is not None:
        text, n = re.subn(r"(?m)^Environment=PATH=.*$", f"Environment=PATH={path}", text)
        assert n == 1, "unit template: exactly one Environment=PATH= line expected"
    return text


def install_unit(ch: Chemins, go, path=None):
    _need_go(go, "install_unit")
    text = unit_text(ch, path)
    changed = not ch.unit.exists() or ch.unit.read_text() != text
    ch.unit.parent.mkdir(parents=True, exist_ok=True)
    ch.unit.write_text(text)
    return changed


# ---------- plan ----------

def preconditions(ch, which, vault):
    rows = []
    for tool in ("hc", "nix", "ffmpeg") + (("bw-unlocked",) if vault else ()):
        rows.append((f"{tool} found", bool(which(tool))))
    rows.append((f"Python env ({ch.pyenv}/bin/python)", (ch.pyenv / "bin/python").exists()))
    rows.append((f"existing env ({ch.env})", ch.env.exists()))
    rows.append((f"certificate ({ch.cert})", ch.cert.exists() and ch.key.exists()))
    rows.append((f"unit already installed ({ch.unit})", ch.unit.exists()))
    return rows


def plan(ch, args, env_exists, cert_exists, vault, has_nix, path):
    if has_nix:
        steps = [f"nix build .#pyenv -o {ch.pyenv}   (cwd {ch.repo})"]
    elif (ch.pyenv / "bin/python").exists():
        steps = [f"Python env {ch.pyenv} provided (no nix): used as is"]
    else:
        steps = [f"MISSING a Python env: without nix, create it first — python3 -m venv {ch.pyenv} && "
                 f"{ch.pyenv}/bin/pip install {PIP_DEPS}"]
    if env_exists:
        steps.append(f"{ch.env} exists: neither token nor key regenerated" + (", nothing in the vault" if vault else ""))
    else:
        steps.append("generate in memory: token (48 chars [A-Za-z0-9_-]) + VAPID private key P-256 (PEM)")
        if vault:
            steps += [
                f"vault: create the item \"{ITEM}\" (secure note, 2 hidden fields {', '.join(FIELDS)}, "
                f"org={args.org_id}, collection={args.collection_id})",
                "vault: read the item back, compare sha256[:12] of both fields; any difference => stop, nothing on disk",
            ]
        steps.append(f"write {ch.env} (folder 700, file 600, O_EXCL)")
    if not cert_exists:
        steps.append(f"generate a self-signed certificate in {ch.tls} for {', '.join(cert_names(args.cert_hosts))}"
                     if args.cert_autosigne
                     else f"MISSING the certificate {ch.cert} (--cert-autosigne for a self-signed one)")
    steps.append(f"install {ch.unit} (@REPO@ -> {ch.repo}"
                 + (f"; PATH -> {path})" if path is not None else "; PATH of the template, NixOS)"))
    steps += [
        "systemctl --user daemon-reload",
        "systemctl --user enable --now herdr-web.service",
        "systemctl --user restart herdr-web.service   (only if unit changed or env rewritten)",
    ]
    if not env_exists and not vault:
        steps.append("print the access token ONCE, with the command to read it back from the env")
    return steps


def main(argv=None, home=None, repo=None, bw=real_bw, run=real_run, which=shutil.which, out=print,
         nixos_bin=NIXOS_BIN):
    p = argparse.ArgumentParser(prog="installer.py", description=__doc__.split("\n")[0])
    p.add_argument("--go", action="store_true", help="execute (dry run otherwise)")
    p.add_argument("--org-id", default="", help="Vaultwarden organization (vault mode, with --collection-id)")
    p.add_argument("--collection-id", default="", help="Vaultwarden collection (vault mode, with --org-id)")
    p.add_argument("--bind", default=None, help=f"host:port[,host:port...], default {DEFAULTS['bind']}")
    p.add_argument("--hosts", default=None,
                   help="accepted Host headers, host:port[,...]; default derived from --bind")
    p.add_argument("--vapid-subject", default=None, help=f"default {DEFAULTS['vapid_subject']}")
    p.add_argument("--unit-path", default=None,
                   help="PATH of the unit; default: the template's on NixOS, otherwise detected")
    p.add_argument("--cert-autosigne", action="store_true",
                   help="generate a self-signed certificate if there is none")
    args = p.parse_args(argv)
    if args.vapid_subject is not None and not SUBJECT_RE.match(args.vapid_subject):
        out("REFUSED: --vapid-subject must be mailto:<address> or https://<host> without port "
            "(pywebpush rejects https://host:port)")
        return 1
    if bool(args.org_id) != bool(args.collection_id):
        out("REFUSED: --org-id and --collection-id go together (vault mode), or neither (local mode)")
        return 1
    vault = bool(args.org_id and args.collection_id)
    ch = Chemins(home or Path.home(), repo or REPO)
    go = args.go
    env_exists = ch.env.exists()
    cert_exists = ch.cert.exists() and ch.key.exists()
    half_cert = ch.cert.exists() != ch.key.exists()
    explicit = {k: getattr(args, k) for k in NON_SECRET if getattr(args, k) is not None}
    for k, v in DEFAULTS.items():
        if getattr(args, k) is None:
            setattr(args, k, v)
    if args.hosts is None:
        args.hosts = default_hosts(args.bind)
    has_nix = bool(which("nix"))
    path = args.unit_path if args.unit_path is not None else unit_path(which, nixos_bin)
    try:
        cur = parse_env(ch.env.read_text()) if env_exists else {}
        diffs = {NON_SECRET[k]: v for k, v in explicit.items() if cur.get(NON_SECRET[k]) != v}
    except Exception as e:  # malformed env: no trace, never str(e)
        out(f"UNDECIDABLE — existing env unreadable ({type(e).__name__}): inspect {ch.env}, nothing written")
        return 2
    # certificate names: explicit --hosts, else the existing env's hosts (a renewal), else the default
    args.cert_hosts = explicit.get("hosts") or cur.get("HERDR_WEB_HOSTS") or args.hosts
    in_worktree = ".claude/worktrees" in ch.repo.as_posix()

    out(f"herdr-web installer — {'EXECUTION (--go)' if go else 'DRY RUN, nothing is written'}"
        f" — {'vault' if vault else 'local'} mode")
    out(f"repository served (PYTHONPATH): {ch.repo}")
    out("preconditions:")
    for label, ok in preconditions(ch, which, vault):
        out(f"  [{'x' if ok else ' '}] {label}")
    out("steps, in order:")
    if in_worktree:
        out("  [ ] WARNING: repository under .claude/worktrees — the unit would point to a folder bound to disappear")
    if half_cert:
        out(f"  [ ] WARNING: certificate half installed in {ch.tls} (cert or key alone)")
    if path is not None and args.unit_path is None:
        for tool, found in store_only(which):
            out(f"  [ ] WARNING: {tool} found only under /nix/store ({found}): not GC-rooted, left out of the "
                f"unit's PATH — install it system-wide or in your Nix profile, or pass --unit-path")
    for i, s in enumerate(plan(ch, args, env_exists, cert_exists, vault, has_nix, path), 1):
        out(f"  {i}. {s}")
    for k, v in diffs.items():
        out(f"  existing env: {k} differs from the explicit argument ({v}) — "
            f"{'rewritten' if go else 'would be rewritten with --go'} (token and VAPID key unchanged)")
    if not go:
        return 0

    rewritten = False
    shown_token = None
    try:
        if in_worktree:
            raise Refus("repository under .claude/worktrees: run the installer from the main clone")
        if half_cert:
            raise Refus(f"certificate half installed in {ch.tls} (cert or key alone): complete or remove it")
        if not has_nix and not (ch.pyenv / "bin/python").exists():
            raise Refus(f"neither nix nor a Python env at {ch.pyenv}: create it with "
                        f"`python3 -m venv {ch.pyenv} && {ch.pyenv}/bin/pip install {PIP_DEPS}`, "
                        f"then run this installer with {ch.pyenv}/bin/python")
        if vault and not env_exists and not which("bw-unlocked"):
            raise Refus("bw-unlocked not found (vault mode)")
        if not cert_exists and not args.cert_autosigne:
            raise Refus(f"no certificate ({ch.cert}): install one, or pass --cert-autosigne")
        if (not env_exists or not cert_exists) and not have_crypto():
            raise Refus("the Python module `cryptography` is missing: run this installer from `nix develop` "
                        f"or with {ch.pyenv}/bin/python")
        if has_nix:
            ch.pyenv.parent.mkdir(parents=True, exist_ok=True)
            if run(["nix", "build", ".#pyenv", "-o", str(ch.pyenv)], cwd=str(ch.repo)) != 0:
                raise Refus("nix build .#pyenv failed", 2)
        if not env_exists:
            values = generate_secrets()
            out(f"generated: token {len(values['HERDR_WEB_TOKEN'])} chars (fingerprint {fp(values['HERDR_WEB_TOKEN'])}), "
                f"VAPID key {len(values['HERDR_WEB_VAPID_KEY'])} chars (fingerprint {fp(values['HERDR_WEB_VAPID_KEY'])})")
            if vault:
                item_id = vault_store(values, args.org_id, args.collection_id, bw, go)
                out(f"vault: item {item_id} read back, same fingerprints — writing the env")
            write_env(values, args.bind, args.hosts, args.vapid_subject, ch, go, vault=vault)
            if not vault:
                shown_token = values["HERDR_WEB_TOKEN"]
            del values
        elif diffs:
            rewrite_env_keys(ch, diffs, go)
            rewritten = True
            out(f"env: {', '.join(diffs)} rewritten (token and VAPID key unchanged)")
        if not cert_exists:
            make_selfsigned(ch, go, cert_names(args.cert_hosts))
            out(f"self-signed certificate installed in {ch.tls}")
        unit_changed = install_unit(ch, go, path)
        cmds = [["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", "herdr-web.service"]]
        if unit_changed or rewritten:  # enable --now does not restart an already active unit
            cmds.append(["systemctl", "--user", "restart", "herdr-web.service"])
        for cmd in cmds:
            if run(cmd) != 0:
                raise Refus(f"{' '.join(cmd)} failed", 2)
    except Refus as e:
        out(f"REFUSED: {e}")
        if shown_token is not None:
            out(f"the env is written: read the token from it — grep '^HERDR_WEB_TOKEN=' {ch.env} | cut -d= -f2-")
        return e.rc
    except Exception as e:  # never str(e): it could quote a value
        out(f"UNDECIDABLE — {type(e).__name__} along the way"
            + (f"; if the vault was touched: {AUDIT}" if vault else ""))
        if shown_token is not None:
            out(f"the env is written: read the token from it — grep '^HERDR_WEB_TOKEN=' {ch.env} | cut -d= -f2-")
        return 2
    out("done: check with `systemctl --user is-active herdr-web`")
    if shown_token is not None:
        out("access token — shown ONCE, keep it in a password manager; the device asks for it on its first visit:")
        out(f"  {shown_token}")
        out(f"read it back later: grep '^HERDR_WEB_TOKEN=' {ch.env} | cut -d= -f2-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
