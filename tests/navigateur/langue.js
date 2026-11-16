// Captures de la page en anglais et en français (brief langue-web, 2026-11-15), sur un BANC LOCAL : la page du
// worktree servie par chromium lui-même (page.route) sur une origine fictive, toute l'API simulée. Aucun appel au
// service vivant ni au réseau : toute requête hors de l'origine fictive est refusée et comptée (« horsBanc »,
// attendu 0). La langue est posée dans <html lang> comme le fait le serveur ; la page « lien expiré » (rendue
// par le serveur, sans script) vient de <dossier-pages>/expire-<langue>.html.
// Usage : NODE_PATH=<node_modules avec playwright-core> node tests/navigateur/langue.js <chromium> <sortie> <dossier-pages>
const { chromium } = require("playwright-core");
const fs = require("fs"), path = require("path");
const STATIC = path.join(__dirname, "..", "..", "static");
const ORIGINE = "https://herdr.banc";
const [, , CHROMIUM, SORTIE, PAGES] = process.argv;
const maintenant = Date.now();
const AGENTS = [
  { address: "t1", name: "my-project", label: "~ · Comparatif", state: "blocked", msg: "Bash: git push", program: "claude-code",
    pid: 5, space: "sp1", tab: "t", cwd: "/home/user/Claude/my-project", dir: "my-project", branch: "main", subagents: [],
    model: "claude-opus-4-5", context: 123456, context_max: 200000, since_ms: maintenant - 180e3, machine: "my-laptop", os: "glfos" },
  { address: "t2", name: "maker-langue", role: "maker", parent: "t1", state: "working", program: "claude-code", pid: 7,
    space: "sp1", tab: "t", cwd: "/home/user/Claude/herdr-web", dir: "herdr-web", model: "sonnet-4-5", since_ms: maintenant - 3600e3,
    subagents: [{ id: "a", title: "Explore", state: "working", role: "checker" }] },
  { address: "t3", name: "zsh", state: null, program: "zsh", role: "terminal", pid: 6, space: "sp1", tab: "t",
    cwd: "/home/user", dir: "user", subagents: [], since_ms: maintenant - 2 * 86400e3 },
];
const CHAT = { mode: "chat", items: [
  { role: "moi", text: "Peux-tu pousser la branche ?", ts: "2026-11-15T13:58:00Z" },
  { role: "agent", text: "Je pousse la branche **langue** vers origin.", ts: "2026-11-15T13:59:00Z" },
  { role: "outil", tool: "Bash", text: "Bash: git push -u origin langue", ref: "r1", ts: "2026-11-15T13:59:10Z" },
  { role: "outil", tool: "Read", text: "Read: static/app.js", ref: "r2", ts: "2026-11-15T13:59:12Z" },
  { role: "resultat", ref: "r2", text: "3287 lignes" },
] };
const CHOIX = { msg: "Bash: git push", contexte: "git push -u origin langue", choices: [
  { digit: "1", label: "Yes" }, { digit: "2", label: "Yes, and don't ask again" }, { digit: "3", label: "No" }] };
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".html": "text/html",
  ".webmanifest": "application/manifest+json" };
const horsBanc = [];

function api(route, langue, connecte) {
  const r = route.request(), u = new URL(r.url()), m = r.method();
  const json = (o, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(o) });
  if (!connecte) return json({ erreur: "jeton", detail: "" }, 401);
  if (u.pathname === "/api/stream") return route.fulfill({ contentType: "text/event-stream", body: ": banc\n\n" });
  if (m === "GET") {
    if (u.pathname === "/api/agents") return json(AGENTS);
    if (u.pathname === "/api/spaces") return json([{ id: "sp1", name: "~", folder: "/home/user", tabs: [{ id: "tb1", name: "t" }] }]);
    if (u.pathname === "/api/reglages") return json({ langue, nom_affiche: "Alex",
      handy: { active: true, present: true, bin: "/nix/store/x-handy-0.9.8/bin/handy", version: "0.9.8", modele: "Qwen3",
        langue: "fr", delai_s: 90, ffmpeg: "/run/current-system/sw/bin/ffmpeg" },
      voix: { modele: null, langue: null, vocabulaire: [], source: null },
      version: { commit: "364421e0982c", cache: "herdr-coquille-v37" },
      session: { cookie_max_age_s: 31536000, appareils_push: ["fcm.googleapis.com"] } });
    if (u.pathname === "/api/known-agents") return json([{ id: "claude", name: "Claude Code", installed: true },
      { id: "codex", name: "Codex", installed: false }]);
    if (u.pathname === "/api/statusbar") return json({ cpu: "12 %", mem: "41 %", quota: "38 %", models: { opus: 1, sonnet: 2 } });
    if (u.pathname === "/api/agents/t1/chat") return json(CHAT);
    if (u.pathname === "/api/agents/t1/choices") return json(CHOIX);
    if (u.pathname === "/api/agents/t1/commands") return json([]);
    if (u.pathname === "/api/agents/t1/suggestion") return json({ suggestion: null, turn: 1 });
    return json({ erreur: "introuvable" }, 404);
  }
  return json({ erreur: "banc" }, 403); // aucune écriture
}

async function page(b, largeur, langue, connecte = true) {
  const tel = largeur <= 600;
  const ctx = await b.newContext({ viewport: { width: largeur, height: tel ? 720 : 820 }, deviceScaleFactor: tel ? 2 : 1,
    isMobile: tel, hasTouch: tel, serviceWorkers: "block" });
  await ctx.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGINE) { horsBanc.push(u.href); return route.abort(); }
    if (u.pathname.startsWith("/api/")) return api(route, langue, connecte);
    if (u.pathname.startsWith("/appairer/")) {
      return route.fulfill({ status: 410, contentType: "text/html", body: fs.readFileSync(path.join(PAGES, `expire-${langue}.html`)) });
    }
    if (u.pathname === "/") { // comme le serveur : la langue choisie dans <html lang>
      const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8").replace('<html lang="en">', `<html lang="${langue}">`);
      return route.fulfill({ contentType: "text/html", body: html });
    }
    const f = u.pathname === "/manifest.webmanifest" ? path.join(STATIC, "manifest.webmanifest")
      : u.pathname.startsWith("/static/") ? path.join(STATIC, u.pathname.slice(8)) : null;
    if (!f || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    return route.fulfill({ contentType: TYPES[path.extname(f)] || "application/octet-stream", body: fs.readFileSync(f) });
  });
  const p = await ctx.newPage();
  await p.goto(ORIGINE + "/");
  return { ctx, p };
}

const deborde = (p) => p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

(async () => {
  const b = await chromium.launch({ executablePath: CHROMIUM });
  const out = {};
  for (const langue of ["en", "fr"]) {
    for (const largeur of [320, 1280]) {
      const nom = (x) => `${SORTIE}/${langue}-${largeur}-${x}.png`;
      const { ctx, p } = await page(b, largeur, langue);
      await p.waitForSelector(".cote-tete .bouton-reglages", { timeout: 15000 });
      await p.waitForTimeout(400);
      await p.screenshot({ path: nom("liste") });
      out[`${langue}-${largeur}-liste`] = { deborde: await deborde(p), lang: await p.evaluate(() => I18N.courante()) };
      await p.evaluate(() => { location.hash = "#/a/t1"; });
      await p.waitForSelector("#choix:not([hidden])", { timeout: 15000 });
      await p.waitForTimeout(400);
      await p.screenshot({ path: nom("agent") });
      out[`${langue}-${largeur}-agent`] = { deborde: await deborde(p) };
      await p.evaluate(() => { try { localStorage.setItem("herdr.reglages-rubrique", "affichage"); } catch (_) { /* rien */ } });
      await p.evaluate(() => ouvrirPalette()); // téléphone : pas de bouton visible dans l'en-tête étroit
      await p.waitForSelector(".palette-entree");
      await p.waitForTimeout(250);
      await p.screenshot({ path: nom("palette") });
      await p.keyboard.press("Escape");
      if (largeur <= 600) { await p.evaluate(() => { location.hash = "#/"; }); await p.waitForTimeout(300); }
      await p.click(".cote-tete .bouton-reglages");
      await p.waitForSelector(".onglet-reglages");
      if (largeur <= 600) await p.click('.onglet-reglages[data-rubrique="affichage"]'); // téléphone : la liste d'abord
      await p.locator(".choix-langue").scrollIntoViewIfNeeded();
      await p.waitForTimeout(300);
      await p.screenshot({ path: nom("reglages-affichage") });
      out[`${langue}-${largeur}-reglages`] = { deborde: await deborde(p),
        langue: await p.$eval(".choix-langue", (g) => [...g.querySelectorAll(".segment")].map((s) => [s.textContent, s.getAttribute("aria-checked")])) };
      await ctx.close();
      const c = await page(b, largeur, langue, false); // jeton refusé : l'écran de connexion
      await c.p.waitForSelector(".connexion");
      await c.p.screenshot({ path: nom("connexion") });
      out[`${langue}-${largeur}-connexion`] = await c.p.$eval(".connexion", (f) => [f.querySelector("input").placeholder, f.querySelector("button").textContent]);
      await c.p.goto(ORIGINE + "/appairer/faux");
      await c.p.screenshot({ path: nom("lien-expire") });
      out[`${langue}-${largeur}-expire`] = await c.p.$eval("h1", (h) => h.textContent);
      await c.ctx.close();
    }
  }
  await b.close();
  out.horsBanc = horsBanc;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
