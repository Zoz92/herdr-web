// Captures de la rubrique Voix (brief handy-reglages, 2026-11-15) : Handy présent, Handy absent, confirmation
// d'installation. Aucun réseau ni service vivant : la page du worktree est servie par chromium lui-même
// (page.route) et chaque /api/* rend une réponse fabriquée ; un POST est refusé et compté.
// Usage : NODE_PATH=<node_modules avec playwright-core> node tests/navigateur/handy.js <chromium> <dossier des captures>
const { chromium } = require("playwright-core");
const fs = require("fs"), path = require("path");
const STATIC = path.join(__dirname, "..", "..", "static");
const ORIGINE = "https://herdr.test";
const SORTIE = process.argv[3];
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
  ".html": "text/html", ".webmanifest": "application/manifest+json" };

const BASE = { active: true, version: "0.10.0", modele: "handy-computer/Qwen3-ASR-1.7B-gguf/Qwen3-ASR-1.7B-Q8_0.gguf",
  langue: "fr", delai_s: 90, ffmpeg: "/run/current-system/sw/bin/ffmpeg", depot: "https://github.com/cjpais/Handy",
  dossier_perso: "/home/user", config: "/home/user/.local/share/com.pais.handy/settings_store.json" };
const ETATS = {
  present: { ...BASE, present: true, bin: "/nix/store/x-handy-0.10.0/bin/.handy-wrapped", bin_erreur: null },
  absent: { ...BASE, present: false, version: null, modele: null, langue: null, bin: null,
    bin_erreur: "FileNotFoundError : Handy.desktop" },
};
const VOIX = { modele: "handy-computer/Qwen3-ASR-1.7B-gguf/Qwen3-ASR-1.7B-Q8_0.gguf", langue: "fr",
  vocabulaire: ["herdr", "Alex", "my-project"], source: "page" };
const VOIX_ECART = { ...VOIX, modele: "parakeet-tdt-0.6b-v3" }; // forcé, différent de celui de Handy : la page le signale

async function page(b, largeur, etat, posts) {
  const ctx = await b.newContext({ viewport: { width: largeur, height: largeur < 500 ? 820 : 900 },
    isMobile: largeur < 500, hasTouch: largeur < 500 });
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.origin !== ORIGINE) return r.abort(); // rien ne sort de chromium
    if (r.request().method() !== "GET") { posts.push(u.pathname); return r.abort(); }
    const api = {
      "/api/reglages": { handy: ETATS[etat], voix: etat === "present" ? VOIX_ECART : { modele: null, langue: null, vocabulaire: [], source: null },
        nom_affiche: "Alex", version: { commit: "worktree", cache: "herdr-coquille-v99" }, session: { cookie_max_age_s: 31536000, appareils_push: [] } },
      "/api/agents": [{ address: "t1", name: "herdr-web", state: "idle", program: "claude-code", pid: 1, space: "my-project",
        tab: "1", cwd: "/home/user/Claude/herdr-web", dir: "herdr-web", subagents: [] }],
      "/api/spaces": [{ id: "sp0", name: "my-project", folder: "/home/user", tabs: [] }],
      "/api/known-agents": [{ id: "claude", name: "Claude Code", installed: true }],
      "/api/statusbar": { cpu: "—", mem: "—", quota: "—", models: {} },
    }[u.pathname];
    if (api !== undefined) return r.fulfill({ contentType: "application/json", body: JSON.stringify(api) });
    if (u.pathname.startsWith("/api/")) return r.abort();
    const f = u.pathname === "/" ? "index.html" : u.pathname.replace(/^\/static\//, "").replace(/^\//, "");
    const chemin = path.join(STATIC, f);
    if (u.pathname === "/sw.js" || !chemin.startsWith(STATIC) || !fs.existsSync(chemin)) return r.fulfill({ status: 404, body: "" });
    return r.fulfill({ contentType: TYPES[path.extname(f)] || "application/octet-stream", body: fs.readFileSync(chemin) });
  });
  const p = await ctx.newPage();
  await p.goto(ORIGINE + "/");
  await p.waitForSelector(".cote-tete .bouton-reglages", { timeout: 15000 });
  await p.click(".cote-tete .bouton-reglages");
  await p.waitForSelector(".onglet-reglages");
  if (largeur < 500) await p.click(".onglet-reglages"); // téléphone : la liste des rubriques, puis la page Voix
  await p.waitForSelector(".reglages-page:not([hidden])");
  await p.waitForTimeout(300);
  return { ctx, p };
}

(async () => {
  const b = await chromium.launch({ executablePath: process.argv[2] });
  const out = {};
  for (const largeur of [390, 1280]) {
    for (const etat of ["present", "absent"]) {
      const posts = [];
      const { ctx, p } = await page(b, largeur, etat, posts);
      const nom = `voix-${etat}-${largeur}`;
      const pageVoix = await p.$(".reglages-page:not([hidden])");
      await p.screenshot({ path: `${SORTIE}/${nom}.png` });
      await pageVoix.evaluate((n) => n.querySelector(".details-reglages").scrollIntoView({ block: "end" }));
      await p.screenshot({ path: `${SORTIE}/${nom}-bas.png` });
      out[nom] = await p.evaluate(() => ({
        suggestion: !!document.querySelector(".suggestion-handy"),
        liens: [...document.querySelectorAll(".reglages-page:not([hidden]) a.lien-projet")].map((a) => a.textContent),
        debord: document.documentElement.scrollWidth > window.innerWidth,
      }));
      if (etat === "absent") {
        await pageVoix.evaluate((n) => n.querySelector(".suggestion-handy").scrollIntoView({ block: "start" }));
        await p.click(".suggestion-handy button.principal");
        await p.waitForSelector(".suggestion-handy .confirmation");
        await p.screenshot({ path: `${SORTIE}/${nom}-confirmation.png` });
        out[nom].confirmation = await p.$eval(".suggestion-handy .confirmation .intitule", (n) => n.textContent);
      }
      out[nom].posts = posts; // attendu : [] (rien n'a été lancé ni écrit)
      await ctx.close();
    }
  }
  await b.close();
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.message || e) + "\n"); process.exit(1); });
