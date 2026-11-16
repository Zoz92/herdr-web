// Faux core : l'API de herdr-web servie avec des données SYNTHÉTIQUES (noms d'agents, dossiers, branches,
// machines inventés), pour fabriquer les images du README sans aucun agent ni serveur réel.
// Les routes et les champs sont ceux que la page lit (herdr_web/server.py) ; aucune écriture acceptée.
"use strict";

function agents(now) {
  const base = { program: "claude-code", space: "atlas", tab: "parser", machine: "my-laptop", os: "ubuntu", subagents: [] };
  return [
    { ...base, address: "t1", pid: 101, name: "atlas", label: "atlas · Split the parser", state: "working",
      cwd: "/home/user/src/atlas", dir: "atlas", branch: "split-parser", model: "claude-opus-4-5",
      context: 84000, context_max: 200000, since_ms: now - 25 * 60e3 },
    { ...base, address: "t2", pid: 102, name: "maker-parser", role: "maker", parent: "t1", state: "working",
      cwd: "/home/user/src/atlas/.claude/worktrees/parser", dir: "atlas › parser", branch: "parser",
      model: "claude-sonnet-4-5", context: 41000, context_max: 200000, since_ms: now - 12 * 60e3 },
    { ...base, address: "t3", pid: 103, name: "checker-parser", role: "checker", parent: "t1", state: "working",
      cwd: "/home/user/src/atlas", dir: "atlas", branch: "parser", model: "claude-opus-4-5",
      context: 23000, context_max: 200000, since_ms: now - 4 * 60e3 },
    { ...base, address: "t4", pid: 104, name: "reviewer-docs", role: "reviewer", parent: "t1", state: "working",
      cwd: "/home/user/src/atlas", dir: "atlas", branch: "docs", model: "claude-sonnet-4-5",
      context: 17000, context_max: 200000, since_ms: now - 2 * 60e3 },
    { ...base, address: "t5", pid: 105, name: "release-notes", label: "garden · Publish the release", state: "blocked",
      msg: "Bash: npm publish --access public", space: "garden", tab: "release", cwd: "/home/user/src/garden",
      dir: "garden", branch: "main", model: "claude-sonnet-4-5", context: 52000, context_max: 200000,
      since_ms: now - 90e3 },
    { ...base, address: "t6", pid: 106, name: "build", program: "zsh", role: "terminal", activity: "shell",
      state: "working", space: "garden", tab: "release", cwd: "/home/user/src/garden", dir: "garden", branch: "main",
      since_ms: now - 40e3 },
    { ...base, address: "t7", pid: 107, name: "notes", label: "notes · Weekly summary", state: "idle",
      space: "notes", tab: "notes", cwd: "/home/user/notes", dir: "notes", model: "claude-haiku-4-5",
      context: 9000, context_max: 200000, since_ms: now - 3 * 3600e3 },
    { ...base, address: "t8", pid: 108, name: "zsh", program: "zsh", role: "terminal", state: null,
      space: "notes", tab: "notes", cwd: "/home/user", dir: "~", machine: "build-box", os: "fedora",
      since_ms: now - 26 * 3600e3 },
  ];
}

const SPACES = [
  { id: "sp1", name: "atlas", folder: "/home/user/src/atlas", tabs: [{ id: "tb1", name: "parser" }] },
  { id: "sp2", name: "garden", folder: "/home/user/src/garden", tabs: [{ id: "tb2", name: "release" }] },
  { id: "sp3", name: "notes", folder: "/home/user/notes", tabs: [{ id: "tb3", name: "notes" }] },
];

const CHAT = {
  mode: "chat", items: [
    { role: "moi", text: "Publish 2.4.0 once the changelog is ready.", ts: "2026-11-15T10:02:00Z" },
    { role: "agent", text: "The changelog is ready. Publishing **garden 2.4.0** to npm.", ts: "2026-11-15T10:03:00Z" },
    { role: "outil", tool: "Read", text: "Read: CHANGELOG.md", ref: "r1", ts: "2026-11-15T10:03:05Z" },
    { role: "resultat", ref: "r1", text: "212 lines" },
    { role: "outil", tool: "Bash", text: "Bash: npm publish --access public", ref: "r2", ts: "2026-11-15T10:03:20Z" },
  ],
};
const CHOIX = { msg: "Bash: npm publish --access public", contexte: "npm publish --access public", choices: [
  { digit: "1", label: "Yes" }, { digit: "2", label: "Yes, and don't ask again" }, { digit: "3", label: "No" }] };

function repondre(route, now) {
  const r = route.request(), u = new URL(r.url());
  const json = (o, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(o) });
  if (r.method() !== "GET") return json({ erreur: "faux-core" }, 403);
  switch (u.pathname) {
    case "/api/stream": return route.fulfill({ contentType: "text/event-stream", body: ": faux core\n\n" });
    case "/api/agents": return json(agents(now));
    case "/api/spaces": return json(SPACES);
    case "/api/reglages": return json({ langue: "en", nom_affiche: "You", handy: { active: false, present: false },
      voix: { modele: null, langue: null, vocabulaire: [], source: null },
      version: { commit: "000000000000", cache: "herdr-coquille-demo" },
      session: { cookie_max_age_s: 31536000, appareils_push: [] } });
    case "/api/known-agents": return json([{ id: "claude", name: "Claude Code", installed: true }]);
    // format réel de herdr_web/statusbar.py ; quota null : aucun script de quota (installation neuve)
    case "/api/statusbar": return json({ cpu: "◔ CPU 18%", mem: "◑ MEM 6,2G", quota: null, models: { opus: 2, sonnet: 3, haiku: 1 } });
    default: {
      const m = u.pathname.match(/^\/api\/agents\/([^/]+)\/(chat|choices|commands|suggestion)$/);
      if (m && m[2] === "chat") return json(m[1] === "t5" ? CHAT : { mode: "chat", items: [] });
      if (m && m[2] === "choices") return json(m[1] === "t5" ? CHOIX : { choices: [] });
      if (m && m[2] === "commands") return json([]);
      if (m && m[2] === "suggestion") return json({ suggestion: null, turn: 1 });
      return json({ erreur: "introuvable" }, 404);
    }
  }
}

module.exports = { repondre, agents };
