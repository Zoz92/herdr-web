"""python -m herdr_web --bind 127.0.0.1:10370 --cert c.pem --key k.pem"""
import argparse
import logging
import os
import sys
import threading
from pathlib import Path

from herdr_web import sante, server as server_mod
from herdr_web.reglages import Reglages
from herdr_web.agents import AgentBoard
from herdr_web.core import HcClient
from herdr_web.push import NotifyWorker, Push
from herdr_web.server import DEFAULT_HOSTS, make_server
from herdr_web.statusbar import StatusBar


log = logging.getLogger(__name__)

EX_CONFIG = 78  # erreur de configuration : l'unit pose RestartPreventExitStatus=78 (inutile de relancer)


class ConfigParser(argparse.ArgumentParser):
    def error(self, message):
        print(f"herdr_web : argument invalide : {message}", file=sys.stderr)
        raise SystemExit(EX_CONFIG)


def make_push(env, board):
    """Push depuis l'environnement ; None (serveur sans push) si la clé est absente.
    Clé présente mais illisible : ValueError, sans la valeur."""
    pem = env.get("HERDR_WEB_VAPID_KEY", "")
    if not pem.strip():
        log.info("push désactivé (HERDR_WEB_VAPID_KEY absente)")
        return None
    state = Path(env.get("HERDR_WEB_STATE") or Path.home() / ".local/state/herdr-web")
    push = Push(state, pem, env.get("HERDR_WEB_VAPID_SUBJECT") or "mailto:herdr-web@localhost")
    # le rappel ne fait que poser dans une file : le réseau part d'un fil dédié, hors refresh
    board.on_blocked(NotifyWorker(push).submit)
    return push


def barre_etat(**kw) -> StatusBar:
    """Barre d'état du service : quota lu dans reglages.json à chaque tour (posé sans redémarrer)."""
    return StatusBar(quota_script=Reglages(server_mod.REGLAGES).quota_script, **kw)


def parse_binds(spec):
    """'h:p,h:p' -> [(h, p)] ; ValueError si liste vide, élément mal formé ou doublon."""
    binds = []
    for item in spec.split(","):
        host, _, port = item.strip().rpartition(":")
        if not (port.isascii() and port.isdigit() and int(port) <= 65535):
            raise ValueError("--bind doit être hôte:port[,hôte:port] (port 0-65535)")
        addr = (host or "127.0.0.1", int(port))
        if addr in binds:
            raise ValueError("--bind : adresse en double")
        binds.append(addr)
    return binds


def serve_all(servers):
    """Un fil par serveur ; le principal attend. Ctrl-C : arrêt de tous."""
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.shutdown()


def main(argv=None):
    p = ConfigParser(prog="herdr_web")
    p.add_argument("--bind", default="127.0.0.1:10370", help="hôte:port[,hôte:port...]")
    p.add_argument("--cert", required=True)
    p.add_argument("--key", required=True)
    args = p.parse_args(argv)
    try:
        binds = parse_binds(args.bind)
    except ValueError as e:
        print(f"herdr_web : {e}", file=sys.stderr)
        raise SystemExit(EX_CONFIG)
    hosts = [h.strip() for h in os.environ.get("HERDR_WEB_HOSTS", "").split(",") if h.strip()]
    logging.basicConfig(level=logging.INFO)
    hc = HcClient()
    board = AgentBoard(hc)
    statusbar = barre_etat()
    servers = []
    try:
        push = make_push(os.environ, board)
        for addr in binds:
            servers.append(make_server(hc, board, os.environ.get("HERDR_WEB_TOKEN", ""),
                                       addr, hosts or DEFAULT_HOSTS,
                                       cert=args.cert, key=args.key, push=push,
                                       statusbar=statusbar))
    except ValueError as e:
        print(f"herdr_web : configuration invalide : {e}", file=sys.stderr)
        raise SystemExit(EX_CONFIG)
    except OSError:  # une adresse prise : rc 1, l'unit relance ; on libère celles déjà liées
        for s in servers:
            s.server_close()
        raise
    hc.start()
    sante.demarrer(hc)  # une ligne par minute : RSS et file d'événements hc
    statusbar.start(demand=lambda: any(s.clients for s in servers))  # seulement si une page regarde
    serve_all(servers)


if __name__ == "__main__":
    main()
