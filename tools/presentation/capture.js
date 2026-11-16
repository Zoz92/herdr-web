// Images du README, depuis la page de ce dépôt (static/) et le faux core (données synthétiques) :
//   frames <dossier> : images PNG successives de la liste Agents, animations CSS pilotées image par image
//                      (chaque animation fait un nombre ENTIER de cycles sur la boucle : boucle propre) ;
//   phone <fichier>  : le haut de la page en largeur téléphone (390 px : en-tête, Spaces, Agents),
//                      posé dans un cadre de téléphone.
// Tout reste dans chromium : la page est servie par page.route sur une origine fictive ; toute requête
// ailleurs est refusée et comptée (« horsBanc », attendu 0).
// Usage : NODE_PATH=<node_modules avec playwright-core> node capture.js <chromium> frames|phone <sortie>
"use strict";
const { chromium } = require("playwright-core");
const fs = require("fs"), path = require("path");
const { repondre } = require("./faux-core");

const STATIC = path.join(__dirname, "..", "..", "static");
const ORIGINE = "https://herdr.demo";
const [, , CHROMIUM, MODE, SORTIE] = process.argv;
const NOW = Date.parse("2026-11-15T10:04:00Z");
const BOUCLE_MS = 8400; // durée de la boucle du GIF
const IPS = 12.5;       // images par seconde
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
  ".html": "text/html", ".webmanifest": "application/manifest+json" };
const horsBanc = [];

async function ouvrir(b, opts) {
  const ctx = await b.newContext({ ...opts, serviceWorkers: "block", reducedMotion: "no-preference", locale: "en-US", timezoneId: "UTC" });
  await ctx.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGINE) { horsBanc.push(u.href); return route.abort(); }
    if (u.pathname.startsWith("/api/")) return repondre(route, NOW);
    if (u.pathname === "/") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(STATIC, "index.html")) });
    const f = u.pathname === "/manifest.webmanifest" ? path.join(STATIC, "manifest.webmanifest")
      : u.pathname.startsWith("/static/") ? path.join(STATIC, u.pathname.slice(8)) : null;
    if (!f || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    return route.fulfill({ contentType: TYPES[path.extname(f)] || "application/octet-stream", body: fs.readFileSync(f) });
  });
  const p = await ctx.newPage();
  await p.clock.install({ time: NOW });
  await p.goto(ORIGINE + "/");
  await p.waitForSelector('[data-address="t5"], .agent', { timeout: 15000 });
  await p.clock.runFor(1500); // premiers rendus et minuteries de la page
  await p.clock.pauseAt(NOW + 2000); // l'heure de la page ne bouge plus : les durées affichées restent fixes
  return { ctx, p };
}

// chaque animation CSS : en pause, puis placée au temps t de la boucle, vitesse ajustée pour qu'elle fasse
// un nombre entier de cycles en BOUCLE_MS (écart de quelques % au plus, invisible)
function placer({ t, boucle }) {
  for (const a of document.getAnimations()) {
    const eff = a.effect;
    if (!eff) continue;
    const tim = eff.getComputedTiming();
    const periode = Number(tim.duration) * (String(eff.getTiming().direction).includes("alternate") ? 2 : 1);
    if (!isFinite(periode) || periode <= 0 || tim.iterations !== Infinity) continue;
    const k = Math.max(1, Math.round(boucle / periode));
    a.pause();
    a.currentTime = (t / boucle) * k * periode;
  }
}

(async () => {
  const b = await chromium.launch({ executablePath: CHROMIUM, args: ["--force-color-profile=srgb"] });
  if (MODE === "frames") {
    const { p } = await ouvrir(b, { viewport: { width: 1120, height: 1080 }, deviceScaleFactor: 1 });
    await p.evaluate(() => { location.hash = "#/a/t5"; }); // à droite : l'agent qui attend, sa question
    await p.clock.runFor(800);
    await p.waitForSelector("#choix:not([hidden])", { timeout: 15000 });
    const zone = null;
    const n = process.env.N ? Number(process.env.N) : Math.round(BOUCLE_MS / 1000 * IPS);
    fs.mkdirSync(SORTIE, { recursive: true });
    for (let i = 0; i < n; i++) {
      await p.evaluate(placer, { t: i * 1000 / IPS, boucle: BOUCLE_MS });
      const nom = path.join(SORTIE, `f${String(i).padStart(4, "0")}.png`);
      await (zone ? zone.screenshot({ path: nom, animations: "allow" }) : p.screenshot({ path: nom, animations: "allow" }));
    }
  } else if (MODE === "phone") {
    const { p } = await ouvrir(b, { viewport: { width: 390, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    await p.evaluate(placer, { t: 300, boucle: BOUCLE_MS });
    const ecran = await p.screenshot({ animations: "allow" });
    // cadre de téléphone dessiné en CSS autour de la capture, fond transparent
    const cadre = await b.newPage({ viewport: { width: 470, height: 930 }, deviceScaleFactor: 2 });
    await cadre.setContent(`<!doctype html><html><body style="margin:0;background:transparent">
      <div id="tel" style="display:inline-block;margin:20px;padding:14px;border-radius:58px;background:#11111b;
        box-shadow:0 0 0 2px #45475a,0 18px 40px rgba(0,0,0,.35)">
        <div style="position:relative;width:390px;height:844px;border-radius:44px;overflow:hidden;background:#11111b">
          <div style="height:44px;display:flex;align-items:center;padding:0 34px;color:#cdd6f4;font:600 15px system-ui,sans-serif">9:41</div>
          <img src="data:image/png;base64,${ecran.toString("base64")}" style="width:390px;height:800px;display:block">
          <div style="position:absolute;top:10px;left:50%;transform:translateX(-50%);width:110px;height:30px;
            border-radius:15px;background:#11111b"></div>
        </div></div></body></html>`);
    await (await cadre.$("#tel")).screenshot({ path: SORTIE, omitBackground: true });
  } else {
    throw new Error("mode inconnu : frames | phone");
  }
  await b.close();
  process.stdout.write(JSON.stringify({ horsBanc }) + "\n");
  if (horsBanc.length) process.exit(3);
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
