"""Notification « X attend » restée sur le téléphone après la réponse (constaté le 2026-11-16) :
le service worker la ferme sur un push « clear », la page ferme celles qui sont périmées."""
import json
import shutil
import subprocess
from pathlib import Path

from test_page import banc

STATIC = Path(__file__).parent.parent / "static"

SW = r"""
const fs = require("fs"), vm = require("vm");
const ecoute = {}, montrees = [], attentes = [];
const notifs = ["t1", "t2"].map((tag) => ({ tag, ferme: false, close() { this.ferme = true; } }));
const registration = {
  getNotifications: async (o) => notifs.filter((n) => !n.ferme && (!o || !o.tag || n.tag === o.tag)),
  showNotification: async (t, o) => { montrees.push(o.tag); },
};
const self = { addEventListener: (t, f) => { ecoute[t] = f; }, registration, clients: {} };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { self, caches: {}, location: {}, URL, Promise, JSON });
const pousser = (d) => ecoute.push({ data: { json: () => d }, waitUntil: (p) => attentes.push(p) });
(async () => {
  pousser({ clear: "t1" });
  await Promise.all(attentes);
  const apres = notifs.filter((n) => !n.ferme).map((n) => n.tag);
  pousser({ title: "t3 attend", body: "?", addr: "t3" });
  await Promise.all(attentes);
  process.stdout.write(JSON.stringify({ apres, montrees }));
})();
"""


def test_service_worker_ferme_la_notification_sur_clear():
    node = shutil.which("node")
    assert node, "node absent : il est dans le devShell (flake.nix), lancer via nix develop"
    r = subprocess.run([node, "-e", SW, str(STATIC / "sw.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    assert out["apres"] == ["t2"]       # seule celle de t1 fermée, sans en afficher d'autre
    assert out["montrees"] == ["t3"]    # un push d'attente s'affiche toujours


def test_page_ferme_les_notifications_perimees():
    b = banc("notif-resolue")
    assert b["attente"] == ["t1", "t2", None]
    assert b["repondu"] == ["t2", None]  # t1 n'attend plus ; la notification sans adresse reste
    assert b["ferme"] == [None]          # t2 disparu de la liste
