// Captures du panneau de réglages refondu (onglets latéraux, brief design-reglages, 2026-11-15), sur un BANC
// LOCAL : la page du worktree servie par chromium lui-même (page.route) sur une origine fictive, toute l'API
// simulée. Aucun appel au service vivant ni au réseau : toute requête hors de l'origine fictive est refusée et
// comptée (« horsBanc », attendu 0).
// Usage : NODE_PATH=<node_modules avec playwright-core> node tests/navigateur/reglages-design.js <chromium> <dossier>
const { chromium } = require("playwright-core");
const fs = require("fs"), path = require("path");
const STATIC = path.join(__dirname, "..", "..", "static");
const ORIGINE = "https://herdr.banc";
const SORTIE = process.argv[3];
const REGLAGES = {
  handy: { active: true, bin: "/nix/store/8lg52q2ma2bgpc2dh45046sss9av4k-handy-0.9.8/bin/.handy-wrapped", version: "0.9.8",
    modele: "handy-computer/Qwen3-ASR-1.7B-gguf/Qwen3-ASR-1.7B-Q8_0.gguf", langue: "fr", delai_s: 90,
    ffmpeg: "/run/current-system/sw/bin/ffmpeg", bin_erreur: null },
  version: { commit: "364421e0982c", cache: "herdr-coquille-v27" },
  session: { cookie_max_age_s: 31536000, appareils_push: ["fcm.googleapis.com", "updates.push.services.mozilla.com"],
    modele: "Un jeton partagé ; aucune liste de sessions côté serveur." },
};
const AGENTS = [
  { address: "t1", name: "my-project", label: "~ · Comparatif", state: "working", program: "claude-code", pid: 5, space: "sp1",
    tab: "t", cwd: "/home/user/Claude/my-project", dir: "my-project", subagents: [] },
  { address: "t2", name: "zsh", state: null, program: "zsh", role: "terminal", pid: 6, space: "sp1", tab: "t",
    cwd: "/home/user", dir: "user", subagents: [] },
];
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".html": "text/html",
  ".webmanifest": "application/manifest+json" };
const horsBanc = [];

function api(route) {
  const r = route.request(), u = new URL(r.url()), m = r.method();
  const json = (o, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(o) });
  if (u.pathname === "/api/stream") return route.fulfill({ contentType: "text/event-stream", body: ": banc\n\n" });
  if (m === "GET") {
    if (u.pathname === "/api/agents") return json(AGENTS);
    if (u.pathname === "/api/spaces") return json([{ id: "sp1", name: "~", folder: "/home/user" }]);
    if (u.pathname === "/api/reglages") return json(REGLAGES);
    if (u.pathname === "/api/known-agents") return json([]);
    return json({ code: "introuvable" }, 404);
  }
  if (m === "POST" && u.pathname === "/api/appairage") {
    return json({ lien: ORIGINE + "/appairer/Ab-_9xYz0123456789abcdefghijKLMN", expire_s: 300 });
  }
  return json({ code: "banc" }, 403); // aucune autre écriture
}

async function page(b, largeur) {
  const tel = largeur <= 600;
  const ctx = await b.newContext({ viewport: { width: largeur, height: tel ? 800 : 860 }, deviceScaleFactor: tel ? 2 : 1,
    isMobile: tel, hasTouch: tel, serviceWorkers: "block" });
  await ctx.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGINE) { horsBanc.push(u.href); return route.abort(); }
    if (u.pathname.startsWith("/api/")) return api(route);
    const f = u.pathname === "/" ? path.join(STATIC, "index.html")
      : u.pathname === "/manifest.webmanifest" ? path.join(STATIC, "manifest.webmanifest")
        : u.pathname.startsWith("/static/") ? path.join(STATIC, u.pathname.slice(8)) : null;
    if (!f || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    return route.fulfill({ contentType: TYPES[path.extname(f)] || "application/octet-stream", body: fs.readFileSync(f) });
  });
  const p = await ctx.newPage();
  await p.goto(ORIGINE + "/");
  await p.waitForSelector(".cote-tete .bouton-reglages", { timeout: 15000 });
  await p.waitForTimeout(400);
  return { ctx, p };
}

// débordements : la page, et tout élément du panneau dont le contenu dépasse sa boîte en largeur
const mesure = (p) => p.evaluate(() => {
  const page = document.documentElement.scrollWidth > window.innerWidth;
  const boite = document.querySelector(".boite.reglages");
  const debords = boite ? [...boite.querySelectorAll("*")].filter((n) => { // hors de sa carte, sinon du panneau
    const cadre = (n.parentElement && n.parentElement.closest(".carte-reglages, .details-reglages, .onglet-reglages")) || boite;
    const r = n.getBoundingClientRect(), b = cadre.getBoundingClientRect();
    const deborde = n.clientWidth > 0 && n.scrollWidth > n.clientWidth + 1; // texte plus large que sa propre boîte
    return r.width > 0 && (deborde || r.right > b.right + 0.5 || r.left < b.left - 0.5);
  }).map((n) => n.className && n.className.baseVal === undefined ? n.className : n.tagName) : [];
  return { page, debords };
});

(async () => {
  const b = await chromium.launch({ executablePath: process.argv[2] });
  const out = {};
  // bureau : chaque rubrique, plus « Détails » ouvert, le QR code et la confirmation de déconnexion
  {
    const { ctx, p } = await page(b, 1280);
    await p.evaluate(() => { try { localStorage.removeItem("herdr.reglages-rubrique"); } catch (_) { /* rien */ } });
    await p.click(".cote-tete .bouton-reglages");
    await p.waitForSelector(".onglet-reglages");
    for (const k of ["voix", "notif", "affichage", "appareils", "apropos"]) {
      await p.click(`.onglet-reglages[data-rubrique="${k}"]`);
      await p.waitForTimeout(250);
      await p.screenshot({ path: `${SORTIE}/reglages-1280-${k}.png` });
      out["1280-" + k] = await mesure(p);
    }
    await p.click('.onglet-reglages[data-rubrique="voix"]');
    await p.click(".details-reglages > summary");
    await p.focus(".reglage-bascule .interrupteur"); // focus visible au clavier
    await p.keyboard.press("Tab"); await p.keyboard.press("Shift+Tab");
    await p.waitForTimeout(250);
    await p.screenshot({ path: `${SORTIE}/reglages-1280-voix-details.png` });
    out["1280-voix-details"] = await mesure(p);
    await p.click('.onglet-reglages[data-rubrique="appareils"]');
    await p.click(".vedette .action.principal");
    await p.waitForSelector(".appairage .qr");
    await p.screenshot({ path: `${SORTIE}/reglages-1280-appareils-qr.png` });
    out.contrastes = await p.evaluate(() => { // rapport de contraste des textes secondaires sur leur fond réel
      const rgb = (c) => { const x = document.createElement("canvas").getContext("2d"); x.fillStyle = c; x.fillRect(0, 0, 1, 1);
        return [...x.getImageData(0, 0, 1, 1).data].slice(0, 3); };
      const lum = (c) => { const [r, g, b] = rgb(c).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const fond = (n) => { for (let e = n; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor;
        if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") return c; } return "rgb(0,0,0)"; };
      const ratio = (sel) => { const n = document.querySelector(sel); if (!n) return null;
        const a = lum(getComputedStyle(n).color), f = lum(fond(n));
        return Math.round(((Math.max(a, f) + 0.05) / (Math.min(a, f) + 0.05)) * 100) / 100; };
      return Object.fromEntries([".reglage-ligne .description", ".carte-titre", ".reglages-version", ".onglet-reglages",
        ".valeur", ".discret-danger", ".segment"].map((s) => [s, ratio(s)]));
    });
    await p.click(".discret-danger");
    await p.locator(".confirmation").scrollIntoViewIfNeeded();
    await p.waitForTimeout(200);
    await p.screenshot({ path: `${SORTIE}/reglages-1280-appareils-confirmation.png` });
    out["1280-confirmation"] = await mesure(p);
    await ctx.close();
  }
  // téléphone : la liste, puis deux rubriques (avec « ‹ Retour »)
  {
    const { ctx, p } = await page(b, 390);
    await p.click(".cote-tete .bouton-reglages");
    await p.waitForSelector(".onglet-reglages");
    await p.waitForTimeout(250);
    await p.screenshot({ path: `${SORTIE}/reglages-390-liste.png` });
    out["390-liste"] = await mesure(p);
    for (const k of ["voix", "appareils", "affichage"]) {
      await p.click(`.onglet-reglages[data-rubrique="${k}"]`);
      await p.waitForTimeout(250);
      await p.screenshot({ path: `${SORTIE}/reglages-390-${k}.png` });
      out["390-" + k] = await mesure(p);
      await p.click(`#${await p.getAttribute(`.onglet-reglages[data-rubrique="${k}"]`, "aria-controls")} .retour-reglages`);
      out["390-retour-" + k] = await p.isVisible(".reglages-nav");
    }
    await ctx.close();
  }
  await b.close();
  out.horsBanc = horsBanc;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
