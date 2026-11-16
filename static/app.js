"use strict";
// herdr — page de pilotage. Tout texte venu du serveur passe par textContent (jamais de HTML) ;
// le Markdown de l'agent est rendu par md.js, qui construit des nœuds DOM.
// Le jeton n'est jamais conservé ici : seul le cookie HttpOnly posé par le serveur le porte.
// Un champ absent ou nul cache son élément : la page n'affiche jamais « null », « undefined », « NaN ».

const ETATS = ["blocked", "working", "done", "idle", "error"];
// textes : static/i18n/{en,fr}.js ; la langue est fixée au chargement (un changement recharge la page)
const { t, tn } = I18N;
const LIBELLES = Object.fromEntries(["blocked", "working", "done", "idle", "error", "terminal"].map((k) => [k, t("etat." + k)]));
const GROUPES = ["blocked", "working", "done", "idle", "error", "terminal"].map((k) => [k, t("groupe." + k)]);
const POIDS = { blocked: 0, working: 1, error: 2, done: 3, idle: 4, terminal: 5 };
const TEINTES = ["#94e2d5", "#89b4fa", "#cba6f7", "#fab387", "#a6e3a1", "#f5c2e7", "#f9e2af", "#89dceb"];
const MAX_PHOTO = 8 * 1024 * 1024;
const MAX_VOIX = 12 * 1024 * 1024 - 64 * 1024; // MAX_BODY serveur moins l'enveloppe multipart
const VOCAL_MIN_MS = 500;
const BAS_PX = 40;
const SOUS_POLL_MS = 3000;
const NOUVEL_AGENT_MS = 30000;
const APPUI_LONG_MS = 550;
const APPUI_TREMBLEMENT = 10; // px : au-delà, le doigt défile, ce n'est plus un appui long
const POLICE_MIN = 9;
const POLICE_MAX = 14;
const CLE_COTE = "herdr.cote";
const CLE_TAILLE = "herdr.taille-fil";
const TAILLE_MIN = 11;
const TAILLE_MAX = 26;
const TAILLE_DEFAUT = 15; // celle du corps de page
const TIRET = "—";
// codes d'erreur rendus par le serveur, traduits ici ; un « motif » (erreur de saisie) a sa propre clé
const ERREURS = Object.fromEntries(["perime", "occupe", "vide", "introuvable", "herdr-app", "hc kill", "hc rm", "confirmation",
  "transcription", "voix_desactivee", "handy_config", "texte_attendu"].map((k) => [k, t("erreur." + k)]));
const TITRE = "herdr";
const MODELES_MENU = ["opus", "sonnet"]; // menu « + » : noms de modèles, jamais traduits
const SVG_NS = "http://www.w3.org/2000/svg";

const app = document.getElementById("app");
const S = {
  agents: [], spaces: [], connecte: true, premier: true,
  vue: null, // vue ouverte : { addr, sous, chat, flux, ... }
  es: null, occupe: false,
  cote: null, panneau: null, barre: null, valsBarre: null,
  attente: null, // ouverture d'un agent demandée : { addr, avant, space, jusqua }
  formSpace: null, // formulaire « nouveau space » ouvert dans la barre latérale
  cotes: null, // conteneurs stables de la barre latérale (I2)
  accueil: null, // écran d'accueil stable : { racine, texte }
  connus: null, // agents lançables (point 34) : [{ id, name, installed }]
  confirmation: null, // boîte de confirmation de fermeture (point 33)
  nom: t("nom.defaut"), // nom affiché sur les bulles de l'utilisateur (réglage serveur nom_affiche)
  voix: true, // Handy activé (réglage serveur, partagé entre appareils) : sinon pas de micro
  reglages: null, // panneau de la roue crantée ouvert : { racine, corps }
};

// ---------- utilitaires ----------
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n[k] = v;
  }
  for (const c of kids) if (c != null && c !== "") n.append(c);
  return n;
}
function remplacer(n, ...kids) {
  n.replaceChildren(...kids.filter((k) => k != null && k !== ""));
  // chaque rafraîchissement recrée les icônes : calées sur l'horloge du document, leurs animations
  // reprennent où en étaient les précédentes au lieu de repartir de zéro (et de paraître figées)
  if (n.getAnimations) for (const an of n.getAnimations({ subtree: true })) an.startTime = 0;
}
const texte = (v) => (v == null || (typeof v === "number" && !isFinite(v)) ? "" : String(v));
const etat = (s) => (ETATS.includes(s) ? s : "terminal");
const libelle = (s) => LIBELLES[etat(s)];
// label : « space · détail » calculé par le serveur comme la liste d'agents de herdr-app (le space
// suit le worktree) ; un ancien serveur n'en envoie pas
const nomAgent = (a) => texte(a.label) || texte(a.name) || texte(a.title) || texte(a.address);
const trouver = (addr) => S.agents.find((a) => a.address === addr);
const estClaude = (a) => a.program === "claude-code" || a.program === "claude";
const urlAgent = (addr, fin) => "/api/agents/" + encodeURIComponent(addr) + "/" + fin;
const hashAgent = (addr, sous) => "#/a/" + encodeURIComponent(addr)
  + (sous ? "/s/" + encodeURIComponent(sous) : "");
const lireStockage = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
const ecrireStockage = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* privé */ } };
const effacerStockage = (k) => { try { localStorage.removeItem(k); } catch (_) { /* privé */ } };

function svg(vb, classe, ...formes) { // icônes en ligne, dessinées à la main
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", vb);
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("class", classe);
  for (const [tag, attrs] of formes) {
    const f = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) f.setAttribute(k, v);
    s.append(f);
  }
  return s;
}

// infobulles des icônes de rôle : [repos, travail], texte exact de la table du brief (2026-11-15)
// (identique dans le mod agents-panel de herdr-app) ; « travail » = working, l'icône animée
const INFOBULLES = Object.fromEntries(["maker", "concepteur", "checker", "checker-conception", "reviewer", "claude"]
  .map((k) => [k, [t("bulle." + k + ".repos"), t("bulle." + k + ".travail")]]));
INFOBULLES.shell = [t("bulle.shell"), t("bulle.shell")]; // activité : working seulement
function infobulle(role, a) {
  if (INFOBULLES[role]) return INFOBULLES[role][etat(a.state) === "working" ? 1 : 0];
  // terminal : sans agent (aucun statut rapporté), sinon un autre programme sans rôle, par son nom
  return etat(a.state) === "terminal" ? t("bulle.terminal") : texte(a.program) || t("bulle.terminal");
}

const ETOILE = "M12 2.5l1.6 6.1 5.3-3.4-3.4 5.3 6.1 1.6-6.1 1.6 3.4 5.3-5.3-3.4L12 21.5l-1.6-6.1-5.3 3.4 3.4-5.3L2.5 12l6.1-1.6-3.4-5.3 5.3 3.4z";
// un agent idle dont un membre de la famille (enfants hc, récursivement, et sous-agents de chacun) travaille
// ou attend n'est pas au repos : même règle que le classement en section (famille + sousActifs)
function familleTravaille(a, fam) {
  if (!fam || etat(a.state) !== "idle") return false;
  const f = fam.famille(a);
  return [...f, ...f.flatMap(sousActifs)].some((x) => ["working", "blocked"].includes(etat(x.state)));
}
function iconeRole(a, fam) {
  // rôle (template hc) ou activité en cours ; animée seulement pendant « working »
  if (familleTravaille(a, fam)) a = { ...a, state: "working" }; // sa famille bosse : il ne dort pas
  const role = a.activity === "shell" && etat(a.state) === "working" ? "shell"
    : texte(a.role) || (estClaude(a) ? "claude" : "terminal");
  const anime = etat(a.state) === "working" ? " anime" : "";
  let i;
  if (role === "maker" || role === "concepteur") {
    i = svg("0 0 24 24", "role marteau" + anime,
      ["path", { d: "M14 4l6 6-2.5 2.5-6-6z", fill: "currentColor" }],
      ["path", { d: "M12.6 8.4L4 17l3 3 8.6-8.6", fill: "none", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round" }]);
  } else if (role === "checker" || role === "checker-conception") {
    i = svg("0 0 24 24", "role loupe" + anime,
      ["circle", { cx: "10", cy: "10", r: "6", fill: "none", stroke: "currentColor", "stroke-width": "2.4" }],
      ["path", { d: "M14.5 14.5L20 20", stroke: "currentColor", "stroke-width": "2.6", "stroke-linecap": "round" }]);
  } else if (role === "reviewer") { // point 39 : une feuille lue ligne à ligne, puis cochée
    const trait = { stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" };
    i = svg("0 0 24 24", "role revue" + anime,
      ["path", { class: "feuille", d: "M5.5 2.8h8.7l4.3 4.4v14H5.5z", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }],
      ["path", { class: "ligne l1", d: "M8.5 8.2h6.8", ...trait }],
      ["path", { class: "ligne l2", d: "M8.5 11.6h6.8", ...trait }],
      ["path", { class: "ligne l3", d: "M8.5 15h4", ...trait }],
      ["path", { class: "coche", d: "M13.4 18.2l1.8 1.8 3.6-3.8", pathLength: "1", fill: "none", stroke: "currentColor", "stroke-width": "2.1", "stroke-linecap": "round", "stroke-linejoin": "round" }]);
  } else if (role === "shell") {
    i = svg("0 0 24 24", "role shell" + anime,
      ["path", { d: "M4 7l5 5-5 5", fill: "none", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" }],
      ["rect", { class: "curseur", x: "11", y: "16", width: "9", height: "2.6", rx: "1", fill: "currentColor" }]);
  } else if (role === "claude") {
    // point 8 : au repos, le personnage de Claude (table des programmes de herdr-app) ; en working,
    // l'étoile orange qui tourne
    if (!anime) {
      const src = "/static/prog-claude.svg";
      if (etat(a.state) !== "idle") return el("img", { class: "role claude", src, alt: "", title: infobulle(role, a) });
      // idle : il dort (z qui montent, respiration) ; la structure n'existe qu'en idle
      const z = (n) => el("span", { class: "z z" + n, ariaHidden: "true" }, "z");
      return el("span", { class: "role claude dort", title: infobulle(role, a) },
        el("img", { class: "perso", src, alt: "" }), z(1), z(2), z(3));
    }
    i = svg("0 0 24 24", "role claude" + anime, ["path", { d: ETOILE, fill: "#D97757" }]);
  } else if (role === "terminal") { // terminal sans agent : l'icône « shell » de la table des programmes
    const bulle = infobulle(role, a);
    return el("span", { class: "role terminal", title: bulle, role: "img", ariaLabel: bulle });
  } else return null;
  const t = document.createElementNS(SVG_NS, "title");
  t.textContent = infobulle(role, a);
  i.prepend(t);
  return i;
}

function iconeSous(s) { // rôle déduit du titre ; sans rôle, la règle « Claude sans rôle » (point 8)
  if (!s) return null;
  return iconeRole({ role: ["maker", "checker", "reviewer"].includes(s.role) ? s.role : "claude", state: s.state });
}

function iconeAgentConnu(id, nom) { // menu « + » : l'étoile de Claude, sinon une pastille à l'initiale
  if (id === "claude") return svg("0 0 24 24", "ico-agent", ["path", { d: ETOILE, fill: "#D97757" }]);
  const t = el("span", { class: "ico-agent lettre" }, (texte(nom) || texte(id) || "?").slice(0, 1).toUpperCase());
  t.style.background = teinte(texte(id));
  return t;
}

function iconeCroix() {
  return svg("0 0 24 24", "ico", ["path", { d: "M6 6l12 12M18 6L6 18", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round" }]);
}

function iconeCrayon() {
  return svg("0 0 24 24", "ico", ["path", { d: "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]);
}

function rond(s) {
  const e = etat(s);
  return el("span", { class: "rond " + e, title: LIBELLES[e], role: "img", ariaLabel: LIBELLES[e] });
}

function badgeEtat(s) { return el("span", { class: "etat " + etat(s) }, libelle(s)); }

function familleModele(m) {
  const f = /(opus|sonnet|haiku|fable)[-_ ]?(\d+)?(?:[-.](\d+))?/i.exec(texte(m));
  return f ? { famille: f[1].toLowerCase(), version: f[2] ? f[2] + (f[3] ? "." + f[3] : "") : "" } : null;
}

const FMT_HEURE = (() => {
  try { return new Intl.DateTimeFormat(I18N.locale(), { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" }); }
  catch (_) { return null; }
})();
function heureParis(ts) { // « HH:MM » à Paris ; rien si l'horodatage manque ou ne se lit pas
  if (!texte(ts) || !FMT_HEURE) return "";
  const d = new Date(ts);
  return isFinite(d.getTime()) ? FMT_HEURE.format(d) : "";
}

function badgeModele(m) {
  if (!texte(m)) return null;
  const f = familleModele(m);
  const t = f ? f.famille + (f.version ? " " + f.version : "") : texte(m).slice(0, 18);
  return el("span", { class: "modele " + (f ? f.famille : ""), title: texte(m) }, t);
}

// contexte : tokens reçus au dernier tour, jauge remplie vers le seuil de compaction (context_max)
function kTokens(n) { return n >= 999500 ? (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M" : Math.round(n / 1000) + "k"; }
function badgeContexte(o) {
  const n = o && o.context, max = o && o.context_max;
  if (typeof n !== "number" || !isFinite(n) || n < 0) return null;
  const r = typeof max === "number" && max > 0 ? n / max : null;
  const niveau = r == null ? "" : r >= 0.92 ? " critique" : r >= 0.8 ? " haut" : "";
  const titre = t("ctx.titre", { n: n.toLocaleString(I18N.locale()) })
    + (r == null ? "" : t("ctx.compaction", { max: kTokens(max), p: Math.round(r * 100) }));
  return el("span", { class: "ctx" + niveau, title: titre,
    style: r == null ? null : "--p:" + Math.min(100, Math.round(r * 100)) + "%" }, kTokens(n));
}

function depuis(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms <= 0) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return t("depuis.s", { n: Math.round(s) });
  if (s < 3600) return t("depuis.min", { n: Math.floor(s / 60) });
  if (s < 86400) return t("depuis.h", { n: Math.floor(s / 3600) });
  return t("depuis.j", { n: Math.floor(s / 86400) });
}

function branche(b) { return texte(b) ? el("span", { class: "branche", title: t("branche") }, "⎇ " + b) : null; }

function teinte(nom) {
  let h = 0;
  for (const c of nom) h = (h * 31 + c.codePointAt(0)) >>> 0;
  return TEINTES[h % TEINTES.length];
}

function pire(liste) { // statut agrégé : le plus urgent
  return liste.map((a) => etat(a.state)).sort((x, y) => POIDS[x] - POIDS[y])[0] || "terminal";
}

const joindre = (...parts) => parts.map(texte).filter(Boolean).join(" · ");
// a.machine est toujours l'hôte du serveur : son logo est celui de son système (NixOS, Debian…)
// logo du système de la machine : clé résolue côté serveur (herdr_web/osid.py), icône static/os/<clé>.svg ;
// « glfos » garde son PNG, "" = ordinateur générique ; sous WSL, petit badge Windows sur la distribution
const logoOs = (a) => {
  const cle = /^[a-z]*$/.test(a.os || "") ? a.os || "" : "";
  const img = el("img", { class: "os-logo" + (cle === "glfos" ? " os-glfos" : ""), alt: "",
    src: cle === "glfos" ? "/static/os-logo.png" : "/static/os/" + (cle || "generic") + ".svg" });
  return a.os_wsl ? el("span", { class: "os-wsl" }, img, el("img", { class: "os-badge", src: "/static/os/windows.svg", alt: "" })) : img;
};

async function api(method, path, body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  let r;
  try { r = await fetch(path, init); } catch (_) { throw { status: 0, code: "reseau", detail: "" }; }
  let data = null;
  try { data = await r.json(); } catch (_) { data = null; }
  if (r.status === 401) { afficherConnexion(); throw { status: 401, code: "jeton", detail: "" }; }
  if (!r.ok) {
    throw { status: r.status, code: (data && data.erreur) || "inconnue", detail: (data && data.detail) || "",
      motif: (data && typeof data.motif === "string" && data.motif) || null,
      params: (data && data.params && typeof data.params === "object" && data.params) || null };
  }
  return data;
}

function messageErreur(e) {
  if (e.status === 404 && !e.motif) return t("erreur.agentDisparu");
  if (e.status === 503) return t("erreur.deconnecte");
  if (e.status === 504) return t("erreur.delai");
  // 502 : la sortie du programme (hc, herdr-app) suit telle quelle, elle ne se traduit pas
  if (e.status === 502) {
    const msg = ERREURS[e.code] || t("erreur.herdr");
    return e.detail ? t("erreur.avecDetail", { msg, detail: e.detail }) : msg;
  }
  if (e.status === 413) return t("erreur.tropGros");
  if (e.status === 415) return t("erreur.type");
  if (e.status === 0) return t("erreur.reseau");
  if (e.motif && I18N.existe("motif." + e.motif)) return t("motif." + e.motif, e.params || {});
  return ERREURS[e.code] || texte(e.detail) || t("erreur.statut", { status: texte(e.status) });
}

// ---------- connexion (jeton) ----------
function afficherConnexion() {
  if (S.es) { S.es.close(); S.es = null; }
  fermerVue();
  const champ = el("input", { type: "password", placeholder: t("connexion.jeton"), autocomplete: "current-password" });
  const msg = el("div", { class: "note" });
  const f = el("form", { class: "connexion" },
    el("h1", {}, el("img", { class: "logo-img", src: "/static/herdr.svg", alt: "" }), TITRE),
    champ, el("button", { type: "submit" }, t("connexion.entrer")), msg);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg.textContent = "";
    try {
      const r = await fetch("/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: champ.value }),
      });
      champ.value = "";
      if (r.ok) { location.reload(); return; }
      msg.textContent = r.status === 401 ? t("erreur.jetonFaux") : t("erreur.statut", { status: r.status });
    } catch (_) { msg.textContent = t("erreur.reseau"); }
  });
  app.replaceChildren(f);
  S.vue = null;
}

function squelette() { // barre latérale + panneau du fil, recréés après un écran plein
  if (!S.cote) {
    S.liste = el("div", { class: "cote-defile" });
    S.barre = el("div", { class: "barre-etat" });
    S.poignee = el("div", { class: "poignee-cote", title: t("poignee"),
      role: "separator", ariaOrientation: "vertical" });
    S.cote = el("aside", { id: "cote" }, S.liste, S.barre, S.poignee);
    S.panneau = el("section", { id: "vue" });
    appliquerCote(lireStockage(CLE_COTE) === "replie");
    appliquerLargeur(Number(lireStockage(CLE_LARGEUR)) || null);
    brancherPoignee(S.poignee);
  }
  if (S.cote.parentNode !== app) app.replaceChildren(S.cote, S.panneau);
}

// ---------- largeur du panneau de gauche (PC) : poignée à glisser (demandé le 2026-11-14) ----------
const CLE_LARGEUR = "herdr-web.largeur-cote";
const LARGEUR_MIN = 220, LARGEUR_MAX = 640;
function bornerLargeur(px) { return Math.round(Math.min(LARGEUR_MAX, Math.max(LARGEUR_MIN, px))); }
function appliquerLargeur(px) { // null : la largeur par défaut du CSS
  if (px == null || !isFinite(px)) app.style.removeProperty("--largeur-cote");
  else app.style.setProperty("--largeur-cote", bornerLargeur(px) + "px");
}
function brancherPoignee(p) {
  let depart = null;
  p.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    depart = { x: ev.clientX, l: S.cote.getBoundingClientRect().width };
    if (p.setPointerCapture) p.setPointerCapture(ev.pointerId);
    app.classList.add("redim");
  });
  p.addEventListener("pointermove", (ev) => {
    if (depart) appliquerLargeur(depart.l + ev.clientX - depart.x);
  });
  const fin = () => {
    if (!depart) return;
    depart = null;
    app.classList.remove("redim");
    ecrireStockage(CLE_LARGEUR, String(Math.round(S.cote.getBoundingClientRect().width)));
  };
  p.addEventListener("pointerup", fin);
  p.addEventListener("pointercancel", fin);
  p.addEventListener("dblclick", () => { appliquerLargeur(null); ecrireStockage(CLE_LARGEUR, ""); });
}

// ---------- panneau de gauche repliable (PC) ----------
function appliquerCote(replie) { app.classList.toggle("replie", !!replie); }
function basculerCote() {
  const replie = !app.classList.contains("replie");
  appliquerCote(replie);
  ecrireStockage(CLE_COTE, replie ? "replie" : "ouvert");
}
function boutonCote() {
  const b = el("button", { type: "button", class: "bascule-cote", title: t("cote.bascule"),
    ariaLabel: t("cote.bascule"), onclick: basculerCote });
  b.append(svg("0 0 24 24", "ico",
    ["rect", { x: "3", y: "4", width: "18", height: "16", rx: "2.5", fill: "none", stroke: "currentColor", "stroke-width": "2" }],
    ["path", { d: "M9 4v16", stroke: "currentColor", "stroke-width": "2" }]));
  return b;
}

// ---------- bandeaux, titre, badge ----------
function majBandeaux() {
  document.getElementById("bandeau-deco").hidden = S.connecte;
  const n = S.agents.filter((a) => a.state === "blocked").length;
  document.title = n ? "(" + n + ") " + TITRE : TITRE;
  try {
    if (n && navigator.setAppBadge) navigator.setAppBadge(n).catch(() => {});
    else if (!n && navigator.clearAppBadge) navigator.clearAppBadge().catch(() => {});
  } catch (_) { /* badge indisponible */ }
}

function annoncer(a, sansVibrer) {
  const b = document.getElementById("bandeau-alerte");
  b.textContent = t("bandeau.needsYou", { nom: nomAgent(a) }) + (texte(a.msg) ? " — " + a.msg : "");
  b.dataset.addr = a.address;
  b.hidden = false;
  if (!sansVibrer && navigator.vibrate) navigator.vibrate(200);
}

// à chaque rafraîchissement : un bandeau dont l'agent n'attend plus (répondu dans herdr-app, fermé)
// cède la place au prochain agent encore bloqué, sinon disparaît ; avant, seul un clic le masquait
function suivreBandeau() {
  const b = document.getElementById("bandeau-alerte");
  if (b.hidden) return;
  const regarde = (x) => S.vue && S.vue.addr === x.address && !S.vue.sous;
  const a = trouver(b.dataset.addr);
  // son message a pu changer ; l'agent ouvert ne s'annonce pas (on y répond déjà), comme dans surAgents
  if (a && a.state === "blocked" && !regarde(a)) { annoncer(a, true); return; }
  const autre = S.agents.find((x) => x.state === "blocked" && !regarde(x));
  if (autre) annoncer(autre, true); // déjà annoncé quand il s'est bloqué : pas de nouvelle vibration
  else { b.hidden = true; b.textContent = ""; delete b.dataset.addr; }
}
document.getElementById("bandeau-alerte").addEventListener("click", (ev) => {
  const b = ev.currentTarget;
  b.hidden = true;
  location.hash = hashAgent(b.dataset.addr);
});

// ---------- barre d'état : cpu, mémoire, quota, modèles (point 37 : plus d'horloge) ----------
function decompteModeles(m) {
  if (!m || typeof m !== "object") return "";
  return Object.entries(m).filter(([, n]) => typeof n === "number" && n > 0)
    .sort((a, b) => b[1] - a[1]).map(([f, n]) => f + " " + n).join(" · ");
}

function rendreBarre() {
  if (!S.barre) return;
  const v = S.valsBarre || {};
  const seg = (classe, val) => el("span", { class: "seg " + classe }, texte(val) || TIRET);
  const modeles = decompteModeles(v.models);
  // la ligne des modèles reste là même vide : sinon la barre perd une ligne et la liste remonte
  // quota null : aucun script de quota configuré sur le serveur, le segment n'existe pas
  remplacer(S.barre, seg("cpu", v.cpu), seg("mem", v.mem), ...(v.quota === null ? [] : [seg("quota", v.quota)]),
    el("span", { class: "seg modeles", title: t("barre.modeles") }, modeles || t("barre.aucun")));
}

async function chargerBarre() {
  try { S.valsBarre = await api("GET", "/api/statusbar"); } catch (_) { S.valsBarre = null; }
  rendreBarre();
}

// ---------- barre latérale : spaces et agents ----------
// fermeture d'un agent : bouton au survol, clic droit (PC) ou appui long (téléphone), la même confirmation
function brancherFermeture(bloc, lien, a) {
  const fermer = el("button", { type: "button", class: "fermer-carte", title: t("carte.fermer"),
    ariaLabel: t("carte.fermerNom", { nom: nomAgent(a) }), onclick: (ev) => { ev.preventDefault(); demanderFermeture(a.address); } });
  fermer.append(iconeCroix());
  bloc.append(fermer);
  lien.addEventListener("contextmenu", (ev) => { ev.preventDefault(); demanderFermeture(a.address); });
  let appui = 0, depart = null;
  lien.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType !== "touch") return;
    depart = { x: ev.clientX, y: ev.clientY };
    appui = setTimeout(() => {
      // carte remplacée par un rafraîchissement pendant l'appui : son pointerup est allé à la
      // nouvelle carte, et un tap court ouvrait la confirmation (réserve de la revue UI v2)
      if (!lien.isConnected) { appui = 0; return; }
      appui = -1; demanderFermeture(a.address);
    }, APPUI_LONG_MS);
  });
  const lacher = () => { if (appui > 0) clearTimeout(appui); if (appui !== -1) appui = 0; depart = null; };
  for (const t of ["pointerup", "pointercancel", "pointerleave"]) lien.addEventListener(t, lacher);
  lien.addEventListener("pointermove", (ev) => { // un tremblement du doigt n'annule pas, un défilement si
    if (depart && Math.hypot(ev.clientX - depart.x, ev.clientY - depart.y) > APPUI_TREMBLEMENT) lacher();
  });
  lien.addEventListener("click", (ev) => { if (appui === -1) { ev.preventDefault(); appui = 0; } });
}

// ---------- zone Agents : même expérience que la liste de herdr-app (spec commune du 2026-11-15) ----------
// une famille va dans la section de son membre le plus urgent (point 4), urgence dans l'ordre POIDS
// (blocked > working > error > done > idle), comme le mod de herdr-app ; les sections s'affichent
// dans l'ordre de la spec (GROUPES : Needs you, Working, Done, Idle, Error, Terminaux)
const URGENCE = POIDS;
const plusUrgent = (etats) => etats.map(etat).sort((x, y) => URGENCE[x] - URGENCE[y])[0] || "terminal";
const CLE_TRI = "herdr.tri-agents";
const CLE_PLIS = "herdr.plis";
const TRIS = [["priority", t("tri.priority")], ["grouped", t("tri.grouped")]];
const modeTri = () => (lireStockage(CLE_TRI) === "grouped" ? "grouped" : "priority");
function plis() { // groupes (« sec-… », « sp-… », « pinned ») et agents (« ag-<adresse> ») pliés
  if (!S.plis) {
    let l = null;
    try { l = JSON.parse(lireStockage(CLE_PLIS) || "[]"); } catch (_) { l = null; }
    S.plis = new Set(Array.isArray(l) ? l.filter((x) => typeof x === "string") : []);
  }
  return S.plis;
}
const estPlie = (cle) => plis().has(cle);
function basculerPli(cle) {
  const p = plis();
  if (p.has(cle)) p.delete(cle); else p.add(cle);
  // un agent fermé ne laisse pas son pli : son adresse resservira à un autre
  for (const k of [...p]) if (k.startsWith("ag-") && !trouver(k.slice(3))) p.delete(k);
  ecrireStockage(CLE_PLIS, JSON.stringify([...p]));
  rendreCote();
}

function statutAgent(s, compact) { // point 7 : à gauche ; « ! » en mode Priority, hors Pinned (comme le mod)
  const e = etat(s);
  const court = e === "blocked" && compact;
  return el("span", { class: "statut " + e + (court ? " compact" : ""), title: LIBELLES[e], role: "img",
    ariaLabel: LIBELLES[e] }, e === "blocked" ? (court ? "!" : LIBELLES.blocked) : "");
}

function iconeChevron(ouvert) {
  return svg("0 0 24 24", "ico chevron", ["path", { d: ouvert ? "M6 9l6 6 6-6" : "M9 6l6 6-6 6", fill: "none",
    stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" }]);
}

const sousActifs = (a) => (Array.isArray(a.subagents) ? a.subagents : []).filter((s) => s && !["done", "idle"].includes(s.state));
// ce qu'un pli d'agent cache : ses enfants hc (et les leurs) et ses sous-agents qui tournent
const descendants = (a, fam) => [...fam.enfantsDe(a).flatMap((k) => [k, ...descendants(k, fam)]), ...sousActifs(a)];

function boutonPli(a, fam) { // point 6 : plier un agent ; plié, le compte dans la couleur du plus urgent
  const tout = descendants(a, fam);
  if (!tout.length) return null;
  const cle = "ag-" + a.address, plie = estPlie(cle);
  return el("span", { class: "plier" + (plie ? " plie " + plusUrgent(tout.map((x) => x.state)) : ""), role: "button",
    tabIndex: 0, title: plie ? t("carte.deplier") : t("carte.plier"), ariaExpanded: String(!plie),
    onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); basculerPli(cle); } },
  iconeChevron(!plie), plie ? String(tout.length) : null);
}

async function epingler(a) { // section Pinned : label herdr.pinned du terminal, partagé avec herdr-app
  try {
    await api("POST", urlAgent(a.address, "pin"), { pinned: !a.pinned });
    S.erreurCote = null;
  } catch (e) {
    if (e.status !== 401) S.erreurCote = messageErreur(e);
  }
  rendreCote();
}

function boutonEpingle(a) {
  const b = el("button", { type: "button", class: "epingler" + (a.pinned ? " epingle" : ""),
    title: a.pinned ? t("carte.desepingler") : t("carte.epinglerHaut"),
    ariaLabel: t(a.pinned ? "carte.desepinglerNom" : "carte.epinglerNom", { nom: nomAgent(a) }),
    onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); epingler(a); } });
  b.append(svg("0 0 24 24", "ico", ["path", { d: "M9 4h6l-1 6 3 3H7l3-3zM12 13v7", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]));
  return b;
}

// agents lancés par `hc run` depuis un autre agent : rangés sous leur parent (champ `parent`, adresse),
// à profondeur illimitée (point 15). Un agent n'est « enfant » que si, en remontant ses parents présents,
// on atteint une racine sans repasser par le même agent : un cycle de données laisse l'agent à la
// racine, listé dans son groupe, jamais perdu ni bouclé. Un agent épinglé est toujours une racine.
function famillesHc(agents) {
  const par = new Map(agents.map((a) => [a.address, a]));
  const parent = (a) => (texte(a.parent) ? par.get(a.parent) || null : null);
  const estEnfant = (a) => {
    if (a.pinned) return false;
    const vus = new Set([a.address]);
    for (let c = a, i = 0; ; i++) {
      const p = parent(c);
      if (!p) return i > 0;
      if (vus.has(p.address)) return false;
      vus.add(p.address);
      c = p;
    }
  };
  const rang = new Map(agents.map((a) => [a, estEnfant(a)]));
  const enfantsDe = (a) => agents.filter((x) => rang.get(x) && x.parent === a.address);
  const famille = (a) => [a, ...enfantsDe(a).flatMap(famille)]; // finie : le rang exclut tout cycle
  return { estEnfant: (a) => rang.get(a), enfantsDe, famille };
}

function blocEnfants(a, fam, compact) { // ├ / └ dessinés en CSS (.enfants, .enfant-hc), comme herdr-app
  const kids = fam.enfantsDe(a);
  if (!kids.length) return null;
  const c = el("div", { class: "enfants" });
  kids.forEach((k, i) => {
    const l = ligneAgent(k, fam, { enfant: true, compact });
    if (i === kids.length - 1) l.classList.add("dernier");
    c.append(l);
  });
  return c;
}

// une ligne (points 7 à 15) : statut à gauche, icône, nom, badge du modèle, capsule de contexte, durée ;
// puis dossier + branche ; puis machine (racine seulement) ; message d'un agent bloqué ; enfants
function ligneAgent(a, fam, o = {}) {
  const v = S.vue;
  const actif = v && v.addr === a.address;
  // le libellé porte déjà le space (et l'onglet s'il est nommé) : ne pas le répéter
  const lieu = texte(a.label) ? "" : [texte(a.space), texte(a.tab)].filter(Boolean).join(" › ");
  const lien = el("a", { class: "agent " + (o.enfant ? "enfant " : "") + etat(a.state) + (actif && !v.sous ? " actif" : ""),
    href: hashAgent(a.address) },
    el("div", { class: "l1" }, statutAgent(a.state, o.compact), iconeRole(a, fam), el("span", { class: "nom" }, nomAgent(a)),
      badgeModele(a.model), badgeContexte(a), el("span", { class: "depuis" }, depuis(a.since_ms)), boutonPli(a, fam)));
  if (texte(a.dir) || texte(a.branch)) {
    lien.append(el("div", { class: "l2" }, texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null,
      branche(a.branch)));
  }
  const l3 = o.enfant ? "" : joindre(a.machine, lieu); // un enfant hc n'a pas de ligne machine
  if (l3) lien.append(el("div", { class: "l3" }, texte(a.machine) ? logoOs(a) : null, l3));
  if (a.state === "blocked" && texte(a.msg)) lien.append(el("div", { class: "msg" }, a.msg));
  const bloc = el("div", { class: "bloc-agent" + (o.enfant ? " enfant-hc" : "") }, lien);
  if (!o.enfant) bloc.append(boutonEpingle(a)); // l'épingle ne vaut que pour une racine : un épinglé est toujours racine
  brancherFermeture(bloc, lien, a);
  if (estPlie("ag-" + a.address)) return bloc;
  // sous-agents : seulement ceux qui tournent, comme le mod agents de herdr-app
  const subs = sousActifs(a);
  subs.forEach((s, i) => {
    bloc.append(el("a", { class: "sous-agent" + (actif && v.sous === s.id ? " actif" : ""),
      href: hashAgent(a.address, s.id) },
      el("span", { class: "arbre" }, i === subs.length - 1 && !fam.enfantsDe(a).length ? "└" : "├"),
      statutAgent(s.state, o.compact), iconeSous(s),
      el("span", { class: "nom" }, texte(s.title) || t("sousAgent", { id: texte(s.id) })), badgeModele(s.model),
      badgeContexte(s)));
  });
  const enfants = blocEnfants(a, fam, o.compact);
  if (enfants) bloc.append(enfants);
  return bloc;
}

function teteGroupe(cle, titre, classe, membres, etats) { // un clic plie le groupe (point 6)
  const plie = estPlie(cle);
  const t = el("div", { class: "groupe-tete " + classe + (plie ? " plie" : ""), role: "button", tabIndex: 0,
    ariaExpanded: String(!plie), onclick: () => basculerPli(cle) },
  titre, el("span", { class: "n" }, String(membres)),
  plie && etats.length ? statutAgent(plusUrgent(etats), true) : null, plie ? iconeChevron(false) : null);
  t.dataset.cle = cle;
  return t;
}

function boutonTri() { // point 5 : menu de tri ; son état ouvert survit aux rafraîchissements
  const mode = modeTri();
  const b = el("button", { type: "button", class: "bouton-tri", title: t("tri.bouton", { nom: TRIS.find(([k]) => k === mode)[1] }),
    ariaHasPopup: "menu", ariaExpanded: String(!!S.menuTri), onclick: () => { S.menuTri = !S.menuTri; rendreCote(); } });
  b.append(svg("0 0 24 24", "ico", ["path", { d: mode === "grouped" ? "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5" : "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
    fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]));
  const racine = el("div", { class: "tri" }, b);
  if (S.menuTri) {
    racine.append(el("div", { class: "menu-tri", role: "menu" }, el("div", { class: "titre-menu" }, t("tri.titre")),
      ...TRIS.map(([k, nom]) => ligneMenu(null, nom, "", () => {
        ecrireStockage(CLE_TRI, k);
        S.menuTri = false;
        rendreCote();
      }))));
    racine.querySelectorAll(".ligne-menu").forEach((l, i) => { if (TRIS[i][0] === mode) l.append(iconeCoche()); });
  }
  return racine;
}
function iconeCoche() {
  return svg("0 0 24 24", "ico coche", ["path", { d: "M5 12.5l4.5 4.5L19 7.5", fill: "none", stroke: "currentColor",
    "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" }]);
}
// un clic hors du menu de tri le referme
document.addEventListener("click", (ev) => {
  if (!S.menuTri) return;
  const chemin = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  if (!chemin.some((n) => n && n.classList && n.classList.contains("tri"))) { S.menuTri = false; rendreCote(); }
});

function sectionAgents() { // Pinned, puis sections (Priority) ou spaces (Grouped by space), Terminaux
  const kids = [el("div", { class: "section-titre" }, t("section.agents"), boutonTri())];
  if (!S.agents.length) kids.push(el("div", { class: "vide" }, t("liste.aucunAgent")));
  const fam = famillesHc(S.agents);
  const ordre = new Map(S.agents.map((a, i) => [a.address, i])); // ordre du serveur : départage stable
  const urgence = (a) => { // la famille ET les sous-agents qui tournent, y compris chez les enfants hc
    const f = fam.famille(a);
    return URGENCE[plusUrgent([...f, ...f.flatMap(sousActifs)].map((x) => x.state))];
  };
  const recent = (a) => (typeof a.since_ms === "number" && isFinite(a.since_ms) ? a.since_ms : 0);
  // point 5 : urgence de la famille, puis la sienne, puis dernier changement (récent d'abord) — Priority comme Grouped
  const trier = (l) => [...l].sort((x, y) => urgence(x) - urgence(y) || URGENCE[etat(x.state)] - URGENCE[etat(y.state)]
    || recent(y) - recent(x)
    || ordre.get(x.address) - ordre.get(y.address));
  const racines = trier(S.agents.filter((a) => !fam.estEnfant(a)));
  const priorite = modeTri() === "priority";
  const groupe = (cle, titre, classe, liste, compact) => {
    if (!liste.length) return;
    kids.push(teteGroupe(cle, titre, classe, liste.length, liste.map((a) => TETES[urgence(a)])));
    if (!estPlie(cle)) for (const a of liste) kids.push(ligneAgent(a, fam, { compact }));
  };
  groupe("pinned", t("groupe.pinned"), "", racines.filter((a) => a.pinned));
  const reste = racines.filter((a) => !a.pinned);
  const terminaux = reste.filter((a) => TETES[urgence(a)] === "terminal");
  const agents = reste.filter((a) => TETES[urgence(a)] !== "terminal");
  if (priorite) {
    for (const [cle, titre] of GROUPES) {
      if (cle !== "terminal") groupe("sec-" + cle, titre, cle, agents.filter((a) => TETES[urgence(a)] === cle), true);
    }
  } else {
    const noms = [...new Set([...listeSpaces().map((sp) => sp.name), ...agents.map((a) => texte(a.space))])];
    for (const n of noms) groupe("sp-" + n, n || t("groupe.sansSpace"), "", agents.filter((a) => texte(a.space) === n));
  }
  if (!masque(CLE_MASQUE_TERMINAUX)) groupe("sec-terminal", t("groupe.terminal"), "terminal", terminaux);
  return kids;
}
const TETES = Object.keys(URGENCE); // rang d'urgence -> état

function listeSpaces() { // spaces de herdr-app (avec id) complétés par ceux que portent les agents
  const out = new Map();
  for (const sp of S.spaces) if (texte(sp.name)) out.set(sp.name, { ...sp, agents: [] });
  for (const a of S.agents) {
    if (!texte(a.space)) continue;
    if (!out.has(a.space)) out.set(a.space, { id: null, name: a.space, folder: null, agents: [] });
    out.get(a.space).agents.push(a);
  }
  return [...out.values()];
}

function boutonPlus(titre, onclick) {
  const b = el("button", { type: "button", class: "mini-plus", title: titre, ariaLabel: titre, onclick });
  b.append(svg("0 0 24 24", "ico", ["path", { d: "M12 5v14M5 12h14", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round" }]));
  return b;
}

function formulaireSpace(fini) { // fini(nomCree | null) : annulé ou créé
  const dossiers = [...new Set(S.agents.map((a) => texte(a.cwd)).filter(Boolean))].sort();
  const liste = el("datalist", { id: "dossiers-connus" }, ...dossiers.map((d) => el("option", { value: d })));
  const nom = el("input", { type: "text", placeholder: t("space.nom"), maxLength: 64, required: true });
  const dossier = el("input", { type: "text", placeholder: t("space.dossier"), required: true });
  dossier.setAttribute("list", "dossiers-connus");
  if (dossiers.length) dossier.value = dossiers[0];
  const msg = el("div", { class: "note-cote" });
  const f = el("form", { class: "form-space" }, nom, dossier, liste,
    el("div", { class: "rangee" }, el("button", { type: "submit", class: "action principal" }, t("space.creer")),
      el("button", { type: "button", class: "action", onclick: () => fini(null) }, t("commun.annuler"))),
    msg);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg.textContent = t("space.creation");
    try {
      await api("POST", "/api/spaces", { name: nom.value.trim(), folder: dossier.value.trim() });
      const cree = nom.value.trim();
      await chargerSpaces();
      fini(cree);
    } catch (e) { if (e.status !== 401) msg.textContent = messageErreur(e); }
  });
  return f;
}

function terminalRendu(x, prof = 0) { // l'adresse du terminal que `herdr-app do tab.new` a créé
  if (!x || typeof x !== "object" || prof > 4) return null;
  if (typeof x.terminal === "string" && x.terminal) return x.terminal;
  for (const v of Array.isArray(x) ? x : Object.values(x)) {
    const t = terminalRendu(v, prof + 1);
    if (t) return t;
  }
  return null;
}

async function ouvrirDansSpace(sp, kind, model, dossier, tache) { // rejette avec l'erreur du serveur
  if (!sp || !sp.id) throw { status: 400, code: "", detail: "", motif: "aucun_space" };
  const corps = kind === "claude" ? { kind, model: model || "default" } : { kind };
  if (tache) corps.tache = tache; // clé de liste blanche : le serveur fixe le message et le dossier personnel
  else if (texte(dossier).startsWith("/")) corps.folder = dossier;
  const avant = new Set(S.agents.map((a) => a.address));
  const d = await api("POST", "/api/spaces/" + encodeURIComponent(sp.id) + "/open", corps);
  S.attente = { addr: terminalRendu(d && d.result), avant, space: sp.name, jusqua: Date.now() + NOUVEL_AGENT_MS };
  suivreNouvelAgent();
}

function suivreNouvelAgent() { // après une ouverture : aller au fil du nouvel agent dès qu'il paraît
  const w = S.attente;
  if (!w) return;
  if (Date.now() > w.jusqua) { S.attente = null; return; }
  const neuf = w.addr ? trouver(w.addr) // l'adresse rendue par herdr-app : sans ambiguïté
    : S.agents.find((a) => !w.avant.has(a.address) && (!w.space || !a.space || a.space === w.space));
  if (neuf) { S.attente = null; location.hash = hashAgent(neuf.address); }
}

// ---------- menu « + » : Terminal, Start an agent, New space (points 32 et 34) ----------
async function chargerConnus() {
  try {
    const d = await api("GET", "/api/known-agents");
    S.connus = Array.isArray(d) ? d.filter((x) => x && texte(x.id) && texte(x.name)) : [];
  } catch (_) { S.connus = S.connus || []; }
}

function ligneMenu(icone, nom, droite, onclick, desactive) {
  const b = el("button", { type: "button", class: "ligne-menu", onclick, disabled: !!desactive },
    el("span", { class: "ico-ligne" }, icone), el("span", { class: "nom" }, nom),
    texte(droite) ? el("span", { class: "droite" }, droite) : null);
  return b;
}

function menuPlus(cible) { // cible() : l'agent ouvert, ou null (accueil)
  const bouton = el("button", { type: "button", class: "bouton-plus", title: t("menu.nouveau"),
    ariaLabel: t("menu.nouveau"), ariaHasPopup: "menu" });
  bouton.append(svg("0 0 24 24", "ico", ["path", { d: "M12 5v14M5 12h14", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round" }]));
  const menu = el("div", { class: "menu-nouveau", role: "menu", hidden: true });
  const choisi = { space: null, modele: null }; // survit à une reconstruction du menu (agents connus arrivés après)
  const fermer = () => { menu.hidden = true; menu.replaceChildren(); choisi.space = choisi.modele = null; };
  const remplir = () => { // construit UNE fois par ouverture : aucune mise à jour ne le refait (I2)
    const a = cible();
    const spA = a && texte(a.space) ? S.spaces.find((x) => x.name === a.space && texte(x.id)) : null;
    const avecId = S.spaces.filter((x) => texte(x.id));
    const msg = el("div", { class: "note-cote" });
    let choix = null;
    const kids = [];
    if (spA) kids.push(el("div", { class: "titre-menu" }, t("menu.dans", { nom: spA.name })));
    else {
      choix = el("select", { class: "choix-space", ariaLabel: t("menu.space"), onchange: () => { choisi.space = choix.value; } },
        ...avecId.map((x) => el("option", { value: x.id }, x.name)));
      // le menu se reconstruit quand la liste des agents connus arrive : garder le space déjà choisi
      if (choisi.space && avecId.some((x) => x.id === choisi.space)) choix.value = choisi.space;
      kids.push(el("div", { class: "titre-menu" }, t("menu.space")), avecId.length ? choix
        : el("div", { class: "note-cote" }, t("menu.aucunSpace")));
    }
    const vise = () => spA || avecId.find((x) => x.id === (choix && choix.value)) || avecId[0] || null;
    const lancer = async (kind, model) => {
      msg.textContent = t("menu.ouverture");
      // dossier : celui de l'agent d'où part le clic, sinon celui d'un agent du space visé
      const sp = vise();
      const ici = a && texte(a.cwd) ? a : S.agents.find((x) => sp && x.space === sp.name && texte(x.cwd));
      try { await ouvrirDansSpace(sp, kind, model, ici ? ici.cwd : null); fermer(); } catch (e) {
        if (e.status !== 401) { msg.className = "note-cote erreur"; msg.textContent = messageErreur(e); }
      }
    };
    kids.push(ligneMenu(el("span", { class: "ico-texte" }, ">_"), t("menu.terminal"), "", () => lancer("terminal")));
    kids.push(el("div", { class: "titre-menu" }, t("menu.demarrer")));
    const connus = Array.isArray(S.connus) ? S.connus : [];
    if (!connus.length) kids.push(el("div", { class: "note-cote" }, S.connus ? t("menu.aucunConnu") : t("menu.recherche")));
    let rang = 0;
    for (const c of connus) {
      const ok = !!c.installed;
      if (ok) rang++;
      const modele = c.id === "claude" && ok ? el("select", { class: "choix-modele", ariaLabel: t("menu.modele"),
        onchange: (ev) => { choisi.modele = ev.target.value; } },
        el("option", { value: "default" }, t("menu.defaut")),
        ...MODELES_MENU.map((m) => el("option", { value: m }, m))) : null;
      if (modele && choisi.modele) modele.value = choisi.modele;
      const ligne = ligneMenu(iconeAgentConnu(c.id, c.name), c.name, ok ? String(rang) : t("menu.nonInstalle"),
        ok ? () => lancer(c.id, modele ? modele.value : undefined) : null, !ok);
      if (!ok) ligne.classList.add("absent");
      kids.push(ligne);
      if (modele) kids.push(el("div", { class: "sous-ligne" }, el("span", {}, t("menu.modele")), modele));
    }
    kids.push(el("div", { class: "sep" }));
    kids.push(ligneMenu(el("span", { class: "ico-texte" }, "+"), t("menu.newSpace"), "", () => {
      remplacer(menu, el("div", { class: "titre-menu" }, t("menu.newSpace")), formulaireSpace((cree) => {
        if (!cree) { remplir(); return; }
        rendreCote(true);
        remplir();
      }));
    }));
    kids.push(msg);
    remplacer(menu, ...kids);
  };
  bouton.addEventListener("click", async () => {
    if (!menu.hidden) { fermer(); return; }
    menu.hidden = false;
    remplir();
    if (!S.connus) { await chargerConnus(); if (!menu.hidden) remplir(); }
    else chargerConnus(); // rafraîchi pour la prochaine ouverture, sans refaire celle-ci
  });
  menu.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { fermer(); bouton.focus(); }
  });
  const racine = el("div", { class: "nouveau" }, bouton, menu);
  racine.fermerMenu = fermer;
  return racine;
}

// un clic ailleurs referme le menu « + », comme dans herdr-app (chemin pris au clic : un nœud
// remplacé pendant le clic, « New space » par exemple, compte encore comme intérieur)
document.addEventListener("click", (ev) => {
  const chemin = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  for (const r of document.querySelectorAll(".nouveau")) {
    const m = r.querySelector(".menu-nouveau");
    if (m && !m.hidden && !chemin.includes(r) && typeof r.fermerMenu === "function") r.fermerMenu();
  }
});

// ---------- fermer un agent ou un terminal (point 33) ----------
function fermerConfirmation() {
  if (S.confirmation) S.confirmation.remove();
  S.confirmation = null;
}

function demanderFermeture(addr) {
  const a = trouver(addr);
  if (!a) return;
  fermerConfirmation();
  const msg = el("div", { class: "note-cote" });
  const detail = el("div", { class: "boite-detail" });
  const alerte = el("div", {});
  const maj = () => { // l'état suit l'agent tant que la boîte est ouverte (avant : figé à l'ouverture)
    const x = trouver(addr);
    if (!x) { fermerConfirmation(); return; } // fermé ailleurs : plus rien à fermer
    const e = etat(x.state);
    remplacer(detail, joindre(x.dir, libelle(x.state)) || libelle(x.state));
    remplacer(alerte, e === "working" || e === "blocked"
      ? el("div", { class: "alerte-rouge" }, e === "working" ? t("fermer.travaille") : t("fermer.attend")) : null);
  };
  const annuler = el("button", { type: "button", class: "action", onclick: fermerConfirmation }, t("commun.annuler"));
  const ok = el("button", { type: "button", class: "action danger" }, t("commun.fermer"));
  const racine = !famillesHc(S.agents).estEnfant(a); // pas d'épingle sur un enfant hc
  const epingle = racine ? el("button", { type: "button", class: "action", onclick: () => { fermerConfirmation(); epingler(a); } },
    a.pinned ? t("carte.desepingler") : t("carte.epingler")) : null;
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    msg.className = "note-cote";
    msg.textContent = t("fermer.encours");
    try {
      await api("POST", urlAgent(addr, "close"), { confirm: addr });
      fermerConfirmation();
      if (S.vue && S.vue.addr === addr) location.hash = "#/"; // retour à la liste ; il disparaît au refresh
    } catch (err) {
      ok.disabled = false;
      if (err.status !== 401) { msg.className = "note-cote erreur"; msg.textContent = messageErreur(err); }
    }
  });
  const boite = el("div", { class: "boite", role: "alertdialog", ariaModal: "true",
    ariaLabel: t("carte.fermerNom", { nom: nomAgent(a) }) },
  el("div", { class: "boite-titre" }, t("fermer.question", { nom: nomAgent(a) })),
  detail, alerte,
  el("div", { class: "note-cote" }, t("fermer.explication")),
  el("div", { class: "rangee" }, epingle, annuler, ok), msg);
  S.confirmation = el("div", { class: "voile" }, boite);
  S.confirmation.maj = maj;
  maj();
  S.confirmation.addEventListener("click", (ev) => { if (ev.target === S.confirmation) fermerConfirmation(); });
  S.confirmation.addEventListener("keydown", (ev) => { if (ev.key === "Escape") fermerConfirmation(); });
  document.body.append(S.confirmation);
  annuler.focus(); // le geste par défaut est l'annulation
}

function demanderRenommage(tab) { // tab : { id, name } ; nom vide = l'onglet reprend son numéro
  fermerConfirmation();
  const champ = el("input", { type: "text", maxLength: 64, ariaLabel: t("onglet.nom"),
    placeholder: t("onglet.placeholder") });
  champ.value = tab.name;
  const msg = el("div", { class: "note-cote" });
  const annuler = el("button", { type: "button", class: "action", onclick: fermerConfirmation }, t("commun.annuler"));
  const ok = el("button", { type: "submit", class: "action principal" }, t("onglet.renommer"));
  const f = el("form", { class: "boite", role: "dialog", ariaModal: "true", ariaLabel: t("onglet.renommerTitre") },
    el("div", { class: "boite-titre" }, t("onglet.renommerNom", { nom: tab.name })),
    champ, el("div", { class: "rangee" }, annuler, ok), msg);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    ok.disabled = true;
    msg.className = "note-cote";
    msg.textContent = t("onglet.renommage");
    try {
      await api("POST", "/api/tabs/" + encodeURIComponent(tab.id) + "/rename", { name: champ.value.trim() });
      fermerConfirmation();
      await chargerSpaces();
      rendreCote(true);
    } catch (err) {
      ok.disabled = false;
      if (err.status !== 401) { msg.className = "note-cote erreur"; msg.textContent = messageErreur(err); }
    }
  });
  S.confirmation = el("div", { class: "voile" }, f);
  S.confirmation.addEventListener("click", (ev) => { if (ev.target === S.confirmation) fermerConfirmation(); });
  S.confirmation.addEventListener("keydown", (ev) => { if (ev.key === "Escape") fermerConfirmation(); });
  document.body.append(S.confirmation);
  champ.focus();
  if (champ.select) champ.select();
}

function sectionSpaces() { // une ligne par space, statut agrégé, ses onglets avec leur compte
  const kids = [el("div", { class: "section-titre" }, t("section.spaces"),
    boutonPlus(t("space.nouveau"), () => { S.formSpace = !S.formSpace; S.erreurCote = null; rendreCote(true); }))];
  if (S.formSpace) kids.push(formulaireSpace(() => { S.formSpace = null; rendreCote(true); }));
  for (const sp of listeSpaces()) {
    const past = el("span", { class: "pastille" }, sp.name.slice(0, 1).toUpperCase());
    past.style.background = teinte(sp.name);
    kids.push(el("div", { class: "space" }, past, el("span", { class: "nom" }, sp.name),
      sp.agents.length ? rond(pire(sp.agents)) : null,
      el("span", { class: "n" }, String(sp.agents.length))));
    const tabs = new Map();
    for (const a of sp.agents) {
      if (!texte(a.tab)) continue;
      if (!tabs.has(a.tab)) tabs.set(a.tab, []);
      tabs.get(a.tab).push(a);
    }
    for (const [tb, liste] of tabs) {
      const connu = (sp.tabs || []).find((t) => t && texte(t.id) && t.name === tb); // sans id : pas de crayon
      const crayon = connu ? el("button", { type: "button", class: "renommer-onglet", title: t("onglet.renommerPoints"),
        ariaLabel: t("onglet.renommerAria", { nom: tb }), onclick: () => demanderRenommage(connu) }) : null;
      if (crayon) crayon.append(iconeCrayon());
      kids.push(el("div", { class: "onglet" }, el("span", { class: "nom" }, "› " + tb), crayon,
        rond(pire(liste)), el("span", { class: "n" }, String(liste.length))));
    }
  }
  return kids;
}

function rendreCote(force) {
  // I2 : chaque refresh (SSE, minuterie de 15 s) refait la tête et les agents, jamais la section
  // Spaces pendant que son formulaire est ouvert — la saisie, le choix et le focus y restent
  if (!S.liste) return;
  let c = S.cotes;
  if (!c || c.tete.parentNode !== S.liste) {
    const compte = el("span", { class: "compte" });
    c = S.cotes = { compte, // « + » de la liste (téléphone, sans agent ouvert) : posé une fois, jamais refait
      tete: el("div", { class: "cote-tete" }, el("img", { class: "logo-img", src: "/static/herdr.svg", alt: "" }),
        el("span", { class: "logo" }, TITRE), compte, boutonPalette(), boutonReglages(), boutonCote(), menuPlus(() => null)),
      err: el("div", { class: "note-cote erreur" }),
      spaces: el("div", { class: "section" }), agents: el("div", { class: "section" }) };
    S.liste.replaceChildren(c.tete, c.err, c.spaces, c.agents);
    force = true;
  }
  c.compte.textContent = tn("cote.terminaux", S.agents.length);
  c.err.textContent = texte(S.erreurCote);
  c.err.hidden = !texte(S.erreurCote);
  if (force === true || !S.formSpace) remplacer(c.spaces, ...sectionSpaces());
  remplacer(c.agents, ...sectionAgents());
}

async function chargerSpaces() {
  try {
    const d = await api("GET", "/api/spaces");
    S.spaces = Array.isArray(d) ? d.filter((x) => x && texte(x.id) && texte(x.name)) : [];
  } catch (_) { S.spaces = []; } // ancien serveur : spaces tirés des agents seulement
}

function rendreAccueil() {
  if (S.vue) fermerEcran(S.vue);
  const msg = S.agents.length ? t("liste.choisis") : t("liste.aucunAgent");
  if (S.accueil && S.accueil.racine.parentNode === S.panneau) { S.accueil.texte.textContent = msg; return; }
  const texteAccueil = el("div", { class: "accueil-texte" }, msg);
  const racine = el("div", { class: "accueil" },
    el("div", { class: "accueil-haut" }, boutonCote(), menuPlus(() => null)), texteAccueil);
  S.accueil = { racine, texte: texteAccueil };
  S.panneau.replaceChildren(racine);
}

// ---------- vue d'un agent ou d'un sous-agent ----------
function fermerVue() {
  const v = S.vue;
  if (!v) return;
  if (v.timer) clearInterval(v.timer);
  if (v.suggTimer) clearTimeout(v.suggTimer);
  if (v.ro) v.ro.disconnect();
  if (v.rec) finVocal(v, false);
  S.vue = null;
}

function ouvrirAgent(addr, sous) {
  if (S.vue) fermerEcran(S.vue);
  const a = trouver(addr);
  const v = { addr, sous, mode: null, pid: a ? a.pid : null, msgCarte: null,
    suivi: true, nouveaux: 0, offset: null, commandes: [], selCmd: 0 };
  v.rond = el("span", { class: "rond-hote" });
  v.nom = el("div", { class: "nom" }, a ? nomAgent(a) : addr);
  v.meta = el("div", { class: "meta" });
  v.subs = el("div", { class: "subs-entete" });
  const retour = sous
    ? el("a", { class: "retour-parent", href: hashAgent(addr), title: t("vue.retourParent") },
      "‹ " + (a ? nomAgent(a) : addr))
    : el("a", { class: "retour", href: "#/", title: t("commun.retour") }, "‹");
  const actions = el("div", { class: "actions-entete" }, boutonPalette(), menuPlus(() => trouver(addr)));
  if (!sous) { // un sous-agent in-process n'a pas de terminal à fermer
    const fermer = el("button", { type: "button", class: "bouton-fermer", title: t("carte.fermer"),
      ariaLabel: t("carte.fermerNom", { nom: a ? nomAgent(a) : addr }), onclick: () => demanderFermeture(addr) });
    fermer.append(iconeCroix());
    actions.append(fermer);
  }
  const entete = el("header", { class: "entete" }, boutonCote(), retour, v.rond,
    el("div", { class: "titres" }, v.nom, v.meta, v.subs), actions);
  v.chat = el("div", { id: "chat" });
  v.taille = tailleMemorisee() || TAILLE_DEFAUT;
  v.chat.style.setProperty("--taille-fil", v.taille + "px");
  v.live = el("button", { type: "button", class: "live actif", onclick: () => allerEnBas(v) });
  v.carte = el("div", { id: "choix", hidden: true });
  v.statut = el("div", { id: "statut" });
  v.cmds = el("div", { class: "commandes", role: "listbox", hidden: true });
  const kids = [entete, el("div", { class: "cadre-chat" }, v.chat), el("div", { class: "barre-live" }, v.live),
    v.carte, v.statut, v.cmds];
  if (sous) kids.push(el("div", { class: "lecture-seule" }, t("vue.lectureSeule")));
  else {
    const pied = document.getElementById("pied").content.cloneNode(true);
    I18N.traduireDom(pied); // textes du gabarit d'index.html (data-i18n)
    kids.push(pied);
  }
  S.vue = v;
  S.panneau.replaceChildren(...kids);
  nouveauFil(v);
  v.chat.addEventListener("scroll", () => {
    const bas = auBas(v);
    if (bas !== v.suivi) { v.suivi = bas; if (bas) v.nouveaux = 0; majLive(v); }
  });
  // un dépliage change la hauteur : réévaluer le suivi APRÈS la mise en page, sans sauter
  v.chat.addEventListener("click", (ev) => {
    if (!ev.target.closest("summary, .run-tete")) return;
    v.suivi = false;
    requestAnimationFrame(() => { v.suivi = auBas(v); if (v.suivi) v.nouveaux = 0; majLive(v); });
  }, true);
  if ("ResizeObserver" in window) { // images chargées, clavier, carte de choix : rester en bas
    v.ro = new ResizeObserver(() => {
      if (S.vue !== v) return;
      if (v.mode === "brut") ajusterPolice(v);
      if (v.suivi) basDuChat(v);
    });
    v.ro.observe(v.chat);
  }
  if (!sous) brancherSaisie(v);
  brancherPincement(v);
  majVueAgent();
  majLive(v);
  if (sous) {
    chargerSous(v);
    v.timer = setInterval(() => { if (!document.hidden) chargerSous(v); }, SOUS_POLL_MS);
  } else {
    chargerChat();
    if (a && estClaude(a)) chargerCommandes(v);
  }
}

function majVueAgent() {
  const v = S.vue;
  const a = v && trouver(v.addr);
  if (!a) return;
  if (v.sous) {
    const s = (Array.isArray(a.subagents) ? a.subagents : []).find((x) => x.id === v.sous);
    v.nom.textContent = (s && texte(s.title)) || t("sousAgent", { id: texte(v.sous) });
    remplacer(v.rond, rond(s ? s.state : "done"), iconeSous(s));
    remplacer(v.meta, s ? badgeEtat(s.state) : el("span", {}, t("vue.termine")), s ? badgeModele(s.model) : null, badgeContexte(s),
      el("span", {}, t("vue.de", { nom: nomAgent(a) })), texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null);
    return;
  }
  v.nom.textContent = nomAgent(a);
  marquerVu(v, a);
  majSuggestion(v, a);
  if (["idle", "done"].includes(a.state)) viderAttente(v);
  remplacer(v.rond, rond(a.state), iconeRole(a, famillesHc(S.agents)));
  const d = depuis(a.since_ms);
  remplacer(v.meta, badgeEtat(a.state), badgeModele(a.model), badgeContexte(a),
    d ? el("span", { class: "depuis" }, d) : null,
    texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null, branche(a.branch),
    texte(a.machine) ? el("span", { class: "machine" }, logoOs(a), a.machine) : null);
  // sous-agents actifs : pastilles cliquables vers leur fil (lecture seule)
  const actifs = (Array.isArray(a.subagents) ? a.subagents : []).filter((s) => !["done", "idle"].includes(s.state));
  remplacer(v.subs, ...actifs.map((s) => el("a", { class: "pastille-sous", href: hashAgent(a.address, s.id) },
    rond(s.state), iconeSous(s), el("span", {}, texte(s.title) || t("sousAgent", { id: texte(s.id) })), badgeModele(s.model))));
  v.subs.hidden = !actifs.length;
  if (a.state === "blocked") chargerChoix();
  else { v.suite = null; v.carte.hidden = true; v.carte.replaceChildren(); }
}

function sortieAgent(e) { // 404 : l'agent a disparu, retour à la liste
  if (e.status === 404) { location.hash = "#/"; return true; }
  return false;
}

function statut(txt, erreur) {
  if (!S.vue) return;
  S.vue.statut.textContent = texte(txt);
  S.vue.statut.className = erreur ? "erreur" : "";
}

function figer(oui) {
  S.occupe = oui;
  if (!S.panneau) return;
  for (const b of S.panneau.querySelectorAll("#saisie button, #saisie input, #choix button")) b.disabled = oui;
}

// ---------- taille du texte du fil : pincer (téléphone), Ctrl + molette (PC) — point 38 ----------
const bornerTaille = (px) => Math.min(TAILLE_MAX, Math.max(TAILLE_MIN, Math.round(px * 2) / 2));
function tailleMemorisee() {
  const t = parseFloat(lireStockage(CLE_TAILLE));
  return isFinite(t) ? bornerTaille(t) : null;
}

function tailleCourante(v) {
  if (v.mode !== "brut") return v.taille || TAILLE_DEFAUT;
  if (v.ecran && v.ecran.term) return v.tailleBrut || v.ecran.term.options.fontSize;
  const pre = v.flux && v.flux.querySelector("pre.brut");
  return v.tailleBrut || (pre && parseFloat(pre.style.fontSize)) || 12;
}

function ancreSous(v, x, y) { // l'élément du fil sous les doigts (à défaut le premier visible) et son haut
  let n = typeof document.elementFromPoint === "function" ? document.elementFromPoint(x, y) : null;
  if (!n || !v.flux.contains || !v.flux.contains(n)) {
    const haut = v.chat.getBoundingClientRect().top;
    n = v.flux.children.find ? v.flux.children.find((c) => c.getBoundingClientRect().bottom > haut)
      : [...v.flux.children].find((c) => c.getBoundingClientRect().bottom > haut);
  }
  return n ? { el: n, haut: n.getBoundingClientRect().top } : null;
}

function appliquerTaille(v, px, ancre) {
  const t = bornerTaille(px);
  if (v.mode === "brut") {
    v.tailleBrut = t;
    if (v.ecran && v.ecran.term) v.ecran.term.options.fontSize = t;
    const pre = v.flux && v.flux.querySelector("pre.brut");
    if (pre) pre.style.fontSize = t + "px";
  } else {
    v.taille = t;
    v.chat.style.setProperty("--taille-fil", t + "px");
  }
  if (v.suivi) basDuChat(v); // le suivi Live tient pendant le pincement
  else if (ancre && ancre.el && ancre.el.isConnected !== false) {
    v.chat.scrollTop += ancre.el.getBoundingClientRect().top - ancre.haut; // le texte reste sous les doigts
  }
  return t;
}

function memoriserTaille(v) { if (v.mode !== "brut") ecrireStockage(CLE_TAILLE, String(v.taille)); }

function tailleParDefaut(v) {
  if (v.mode === "brut") { v.tailleBrut = null; ajusterPolice(v); return; }
  effacerStockage(CLE_TAILLE);
  v.cible = null;
  appliquerTaille(v, TAILLE_DEFAUT, null);
}

function brancherPincement(v) {
  const doigts = new Map();
  let geste = null; // { d0, t0, ancre, debut, bouge }
  let dernierTap = 0;
  const deux = () => [...doigts.values()].slice(0, 2);
  v.chat.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType !== "touch") return;
    // premier doigt d'un nouveau contact : oublier les doigts dont le pointerup s'est perdu (nœud
    // remplacé pendant le geste, pre.brut refait à chaque événement agents) — sinon un seul doigt
    // compterait pour deux et un défilement changerait la taille
    if (ev.isPrimary) { doigts.clear(); geste = null; }
    doigts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (doigts.size === 2) {
      const [a, b] = deux();
      geste = { d0: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), t0: tailleCourante(v),
        ancre: ancreSous(v, (a.x + b.x) / 2, (a.y + b.y) / 2), debut: Date.now(), bouge: false };
    }
  });
  v.chat.addEventListener("pointermove", (ev) => {
    if (!doigts.has(ev.pointerId)) return;
    doigts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (!geste || doigts.size !== 2) return;
    const [a, b] = deux();
    const r = Math.hypot(a.x - b.x, a.y - b.y) / geste.d0;
    if (Math.abs(r - 1) > 0.04) geste.bouge = true;
    if (geste.bouge) appliquerTaille(v, geste.t0 * r, geste.ancre);
  });
  const lever = (ev) => {
    if (!doigts.delete(ev.pointerId) || !geste || doigts.size >= 2) return;
    if (geste.bouge) memoriserTaille(v);
    else if (Date.now() - geste.debut < 300) { // tape à deux doigts ; deux de suite : taille par défaut
      if (Date.now() - dernierTap < 450) { tailleParDefaut(v); dernierTap = 0; } else dernierTap = Date.now();
    }
    geste = null;
  };
  const purger = () => { // geste interrompu par le navigateur : rien ne reste en mémoire
    if (geste && geste.bouge) memoriserTaille(v);
    doigts.clear();
    geste = null;
  };
  v.chat.addEventListener("pointerup", lever);
  v.chat.addEventListener("pointercancel", purger);
  v.chat.addEventListener("lostpointercapture", purger);
  v.chat.addEventListener("wheel", (ev) => { // PC : Ctrl + molette, dans le fil seulement
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    // cible non arrondie : un pavé tactile envoie de petits pas qui, arrondis un à un, ne bougeraient rien
    v.cible = Math.min(TAILLE_MAX, Math.max(TAILLE_MIN, (v.cible || tailleCourante(v)) * Math.exp(-ev.deltaY * 0.0015)));
    appliquerTaille(v, v.cible, ancreSous(v, ev.clientX, ev.clientY));
    memoriserTaille(v);
  }, { passive: false });
}

// ---------- fil : suivi en bas et bouton « Live » ----------
function auBas(v) { return v.chat.scrollHeight - v.chat.scrollTop - v.chat.clientHeight < BAS_PX; }
function basDuChat(v) { v.chat.scrollTop = v.chat.scrollHeight; }

function majLive(v) {
  v.live.className = "live" + (v.suivi ? " actif" : "");
  v.live.textContent = v.suivi ? t("live") : t("live") + " ↓" + (v.nouveaux ? " " + v.nouveaux : "");
  v.live.title = v.suivi ? t("live.suivi") : t("live.revenir");
}

function allerEnBas(v) {
  v.suivi = true;
  v.nouveaux = 0;
  basDuChat(v);
  majLive(v);
}

// ---------- fil : rendu des éléments ----------
function nouveauFil(v) {
  v.flux = el("div", { class: "flux" });
  // messages tapés pendant un tour, pas encore pris par l'agent : grisés sous le fil, comme Claude Code
  v.attente = el("div", { class: "file-attente", hidden: true });
  v.chat.replaceChildren(v.flux, v.attente);
  if (v.ro) v.ro.observe(v.flux);
  v.run = null; // suite d'outils consécutifs en cours
  v.outils = new Map(); // ref (id du tool_use) -> ligne d'outil, pour y accrocher son résultat
  v.dernierOutil = null;
  v.derniereCle = null; // clé du dernier élément rendu : point d'accroche d'une fusion
  v.groupe = null; // groupe de messages en cours : { auteur, corps }
}

function auteurGroupe(v, auteur) { // { nom, classe } de la tête de groupe
  const a = trouver(v.addr);
  if (auteur === "moi") return { nom: v.sous ? (a ? nomAgent(a) : t("auteur.parent")) : texte(S.nom) || t("nom.defaut"), classe: "user" };
  let nom = a ? nomAgent(a) : texte(v.addr);
  let modele = a ? a.model : null;
  if (v.sous) {
    const s = (a && Array.isArray(a.subagents) ? a.subagents : []).find((x) => x && x.id === v.sous);
    nom = (s && texte(s.title)) || t("sousAgent", { id: texte(v.sous) });
    modele = s ? s.model : null;
  }
  const f = familleModele(modele);
  return { nom: nom || t("auteur.agent"), classe: "ia" + (f ? " " + f.famille : "") }; // pas « agent » : classe des cartes
}

function corpsPour(v, auteur, ts) { // le corps du groupe en cours, ou un nouveau groupe si l'auteur change
  if (v.groupe && v.groupe.auteur === auteur) return v.groupe.corps;
  const qui = auteurGroupe(v, auteur);
  const heure = heureParis(ts);
  const corps = el("div", { class: "groupe-corps" },
    el("div", { class: "groupe-tete" }, el("span", { class: "auteur " + qui.classe }, qui.nom),
      heure ? el("span", { class: "heure" }, "· " + heure) : null));
  v.flux.append(el("div", { class: "groupe-msg " + (auteur === "moi" ? "de-user" : "de-agent") }, corps));
  v.groupe = { auteur, corps };
  v.run = null;
  return corps;
}

function cleItem(it) {
  return [it.role, it.ts, it.ref, it.tool, texte(it.text).slice(0, 160), it.image, it.error, it.collee, it.de, it.nom]
    .map((x) => texte(x)).join("\u0001");
}

function bulle(it) {
  if (it.role === "moi") {
    const b = el("div", { class: "bulle moi" });
    if (typeof it.image === "string" && it.image.startsWith("/") && !it.image.startsWith("//")) { // chemin absolu, jamais une URL
      const src = "/api/image?p=" + encodeURIComponent(it.image);
      b.append(el("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, el("img", { src, alt: t("fil.imageEnvoyee") })));
    }
    if (typeof it.collee === "string" && /^[0-9a-f]{64}$/.test(it.collee)) { // collée dans Claude Code
      const src = "/api/collee/" + it.collee;
      b.append(el("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, el("img", { src, alt: t("fil.imageCollee") })));
    }
    if (texte(it.text)) b.append(it.text);
    return b;
  }
  const m = el("div", { class: "msg-agent md" });
  for (const n of MD.rendre(texte(it.text), document)) m.append(n);
  return m;
}

// commande `!` / `/` de l'utilisateur et fin de tâche de fond, comme Claude Code : « ! cmd », sa sortie en
// dessous préfixée « └ » (stderr en rouge), une sortie longue repliée sur sa première ligne
const SHELL_PLIE = 6; // lignes de sortie affichées avant repli
function ligneShell(it) {
  const tx = texte(it.text);
  if (it.role === "commande") return el("div", { class: "shell shell-commande" }, el("span", { class: "invite" }, "❯ "), tx);
  if (it.role === "notif") return el("div", { class: "shell shell-notif" }, "● " + tx);
  if (it.tool === "input") return el("div", { class: "shell shell-entree" }, el("span", { class: "invite" }, "! "), tx);
  const cls = "shell shell-sortie" + (it.tool === "stderr" ? " erreur" : "");
  const lignes = tx.split("\n");
  if (lignes.length <= SHELL_PLIE) return el("div", { class: cls }, el("span", { class: "coude" }, "└ "), tx);
  return el("details", { class: cls },
    el("summary", {}, el("span", { class: "coude" }, "└ "), lignes[0], el("span", { class: "reste" }, t("fil.lignes", { n: lignes.length - 1 }))),
    el("pre", {}, tx));
}

function ligneOutil(it) {
  const nom = texte(it.tool) || t("fil.outil");
  let arg = texte(it.text);
  if (arg.startsWith(nom + ": ")) arg = arg.slice(nom.length + 2);
  else if (arg === nom) arg = "";
  const d = el("details", { class: "outil" },
    el("summary", {}, el("span", { class: "nom-outil" }, nom),
      arg ? el("span", { class: "arg" }, " · " + arg.split("\n")[0]) : null));
  d.sortie = el("pre", { class: "sortie attente" }, t("fil.enCours"));
  d.append(el("div", { class: "corps" }, el("pre", { class: "entree" }, texte(it.text) || nom), d.sortie));
  d.resolu = false;
  return d;
}

function majRun(run) {
  const n = run.lignes.children.length;
  run.el.classList.toggle("groupe", n >= 2);
  const der = run.lignes.lastElementChild;
  const sommaire = der && der.querySelector("summary");
  const resume = sommaire ? texte(sommaire.textContent) : "";
  remplacer(run.tete, el("span", {}, tn("fil.outils", n)),
    run.erreurs ? el("span", { class: "err" }, t("fil.enErreur", { n: run.erreurs })) : null,
    resume ? el("span", { class: "dernier" }, " · " + resume) : null);
}

function ajouterOutil(v, ligne, ts) {
  if (!v.run) {
    const corps = corpsPour(v, "agent", ts); // les outils appartiennent au groupe de l'agent
    const run = { el: el("div", { class: "run" }), lignes: el("div", { class: "run-lignes" }), erreurs: 0 };
    run.tete = el("button", { type: "button", class: "run-tete",
      onclick: () => run.el.classList.toggle("ouvert") });
    run.el.append(run.tete, run.lignes);
    corps.append(run.el);
    v.run = run;
  }
  ligne.run = v.run;
  v.run.lignes.append(ligne);
  majRun(v.run);
}

function attacherResultat(v, it) {
  let ligne = it.ref ? v.outils.get(it.ref) : null;
  if (!ligne && v.dernierOutil && !v.dernierOutil.resolu) ligne = v.dernierOutil; // sans ref : le précédent
  if (!ligne) { // résultat orphelin (son outil est avant la fenêtre lue) : une ligne à lui
    ligne = ligneOutil({ tool: t("fil.resultat"), text: t("fil.resultat") });
    ajouterOutil(v, ligne);
  }
  ligne.resolu = true;
  ligne.sortie.className = "sortie";
  ligne.sortie.textContent = texte(it.text) || t("fil.vide");
  if (it.error) {
    ligne.classList.add("erreur");
    ligne.sortie.classList.add("erreur");
    ligne.run.erreurs++;
    majRun(ligne.run);
  }
}

const memeTexte = (a, b) => texte(a).trim() === texte(b).trim();
function retirerAttente(v, t) { // le premier message en attente de ce texte ; rend true s'il y en avait un
  const n = v.attente && [...v.attente.children].find((x) => memeTexte(x.texteAttente, t));
  if (n) n.remove();
  if (v.attente) v.attente.hidden = !v.attente.children.length;
  return !!n;
}
function viderAttente(v) { // l'agent est au repos : sa file est vide
  if (!v || !v.attente || v.attente.hidden) return;
  v.attente.replaceChildren();
  v.attente.hidden = true;
}

function ajouterItems(v, items) { // rend le nombre d'éléments visibles ajoutés
  let n = 0;
  for (const it of Array.isArray(items) ? items : []) {
    if (!it) continue;
    if (it.role === "attente") {
      if (v.attente && ![...v.attente.children].some((x) => memeTexte(x.texteAttente, it.text))) {
        const b = el("div", { class: "bulle moi en-attente", title: t("fil.pasPris") }, texte(it.text));
        b.texteAttente = texte(it.text);
        v.attente.append(b);
        v.attente.hidden = false;
      }
      continue;
    }
    if (it.role === "retrait") { retirerAttente(v, it.text); continue; }
    if (it.role === "moi" || it.role === "commande") retirerAttente(v, it.text);
    v.derniereCle = cleItem(it);
    if (it.role === "resultat") { attacherResultat(v, it); continue; }
    n++;
    if (it.role === "hc") { // tapé par hc, jamais par l'utilisateur : à gauche, signé par l'agent émetteur
      v.groupe = null;
      v.run = null;
      v.flux.append(carteHc(it));
    } else if (it.role === "shell" || it.role === "commande" || it.role === "notif") {
      const corps = corpsPour(v, it.role === "notif" ? "agent" : "moi", it.ts); // `!` et `/` sont de l'utilisateur
      v.run = null;
      corps.append(ligneShell(it));
    } else if (it.role === "outil") {
      const ligne = ligneOutil(it);
      ajouterOutil(v, ligne, it.ts);
      if (it.ref) v.outils.set(it.ref, ligne);
      v.dernierOutil = ligne;
    } else {
      const corps = corpsPour(v, it.role === "moi" ? "moi" : "agent", it.ts);
      v.run = null;
      corps.append(bulle(it));
    }
  }
  return n;
}

// Message tapé par hc dans le prompt d'un agent (compte rendu d'un enfant, envoi d'un autre
// terminal, alerte) : une notification d'agent, à gauche, avec l'icône et le nom de l'émetteur.
function carteHc(it) {
  const a = it.de ? trouver(it.de) : null;
  const nom = a ? nomAgent(a) : texte(it.nom) || texte(it.de) || TITRE;
  const tete = [a ? iconeRole(a) : null, el("span", { class: "auteur ia" }, nom),
    heureParis(it.ts) ? el("span", { class: "heure" }, "· " + heureParis(it.ts)) : null];
  const entete = a ? el("a", { class: "hc-de", href: hashAgent(a.address) }, ...tete) : el("span", { class: "hc-de" }, ...tete);
  const m = el("div", { class: "msg-agent md" });
  for (const n of MD.rendre(texte(it.text), document)) m.append(n);
  return el("div", { class: "groupe-msg de-agent msg-hc" }, el("div", { class: "groupe-corps" }, entete, m));
}

function remplacerFil(v, items) {
  nouveauFil(v);
  ajouterItems(v, items);
  const a = trouver(v.addr); // relecture d'un agent au repos : une mise en file sans suite est périmée
  if (a && ["idle", "done"].includes(a.state)) viderAttente(v);
  v.suivi = true;
  v.nouveaux = 0;
  basDuChat(v); // même tâche que le rendu : la vue s'affiche déjà en bas, sans défilement visible
  majLive(v);
}

function fusionnerFil(v, items) {
  // m1 — reconnexion SSE : ajouter ce qui manque après le dernier élément connu, sans toucher aux
  // nœuds déjà rendus (outils dépliés), ni au défilement, ni au suivi si on lisait plus haut
  const liste = (Array.isArray(items) ? items : []).filter(Boolean);
  let i = -1;
  if (v.derniereCle !== null) {
    for (let j = liste.length - 1; j >= 0; j--) if (cleItem(liste[j]) === v.derniereCle) { i = j; break; }
  }
  if (i >= 0) { suite(v, liste.slice(i + 1)); return; }
  if (v.derniereCle === null) { remplacerFil(v, liste); return; } // fil vide : rien à garder
  // aucun recouvrement (coupure plus longue que la fenêtre lue) : tout refaire, suivi et position gardés
  const suivi = v.suivi;
  const haut = v.chat.scrollTop;
  nouveauFil(v);
  const n = ajouterItems(v, liste);
  if (suivi) basDuChat(v);
  else { v.chat.scrollTop = haut; v.nouveaux += n; majLive(v); }
}

function suite(v, items) {
  const n = ajouterItems(v, items);
  if (v.suivi) basDuChat(v);
  else if (n) { v.nouveaux += n; majLive(v); }
}

// vue brute d'un terminal : toute la largeur, grille respectée, police ajustée aux colonnes
let LARGEUR_CAR = 0; // largeur d'un caractère monospace à 1 px de police
function ajusterPolice(v) {
  const term = v.ecran && v.ecran.term;
  if (term) { // émulateur : la police qui fait tenir toutes les colonnes dans la largeur
    if (v.tailleBrut) { term.options.fontSize = v.tailleBrut; return; }
    const ecr = term.element && term.element.querySelector(".xterm-screen");
    const w = ecr ? ecr.getBoundingClientRect().width : 0;
    const dispo = v.chat.clientWidth - 16;
    if (w > 0 && dispo > 0) {
      const t = Math.max(POLICE_MIN, Math.min(POLICE_MAX, Math.floor(term.options.fontSize * dispo / w * 10) / 10));
      if (Math.abs(t - term.options.fontSize) >= 0.1) term.options.fontSize = t;
    }
    return;
  }
  const pre = v.flux && v.flux.querySelector("pre.brut");
  if (!pre) return;
  if (v.tailleBrut) { pre.style.fontSize = v.tailleBrut + "px"; return; } // pincée : jusqu'à réouverture
  if (!LARGEUR_CAR) {
    const t = el("span", { class: "mesure-mono" }, "M".repeat(100));
    document.body.append(t);
    LARGEUR_CAR = t.getBoundingClientRect().width / 100 / 100; // mesuré à 100 px
    t.remove();
  }
  const a = trouver(v.addr);
  const lignes = texte(pre.textContent).split("\n");
  const cols = (a && typeof a.cols === "number" && a.cols > 0) ? a.cols
    : Math.max(20, ...lignes.map((l) => l.length));
  const dispo = v.chat.clientWidth - 16;
  const taille = LARGEUR_CAR > 0 ? dispo / (cols * LARGEUR_CAR) : POLICE_MAX;
  pre.style.fontSize = Math.max(POLICE_MIN, Math.min(POLICE_MAX, Math.floor(taille * 10) / 10)) + "px";
}

// ---------- vue terminal en couleurs : xterm.js nourri par `hc attach --observe` (2026-11-15) ----------
// Le texte de terminal.read perdait couleurs, invite et alignement ; ici les octets bruts du terminal
// passent dans un émulateur, comme dans herdr-app. Repli : le texte brut, si xterm.js manque.
const THEME_ECRAN = { background: "#1e1e2e", foreground: "#cdd6f4", cursor: "#f5e0dc", selectionBackground: "#585b70",
  black: "#45475a", red: "#f38ba8", green: "#a6e3a1", yellow: "#f9e2af", blue: "#89b4fa", magenta: "#f5c2e7",
  cyan: "#94e2d5", white: "#bac2de", brightBlack: "#585b70", brightRed: "#f38ba8", brightGreen: "#a6e3a1",
  brightYellow: "#f9e2af", brightBlue: "#89b4fa", brightMagenta: "#f5c2e7", brightCyan: "#94e2d5", brightWhite: "#a6adc8" };
const ecranPossible = () => typeof Terminal === "function" && typeof EventSource === "function";

function fermerEcran(v) {
  const e = v && v.ecran;
  if (!e) return;
  v.ecran = null;
  try { e.es.close(); } catch (_) { /* déjà fermé */ }
  try { if (e.term) e.term.dispose(); } catch (_) { /* déjà libéré */ }
}

function brancherEcran(v, note) {
  const a = trouver(v.addr);
  if (v.ecran && v.ecran.addr === v.addr && !(a && a.cols && v.ecran.cols && a.cols !== v.ecran.cols)) {
    if (v.ecran.note) v.ecran.note.textContent = texte(note);
    return; // flux déjà ouvert : il se met à jour seul
  }
  fermerEcran(v);
  nouveauFil(v);
  v.flux.classList.add("terminal", "avec-ecran");
  const noteEl = el("div", { class: "note" }, texte(note));
  const hote = el("div", { class: "ecran" });
  v.flux.append(noteEl, hote);
  const e = v.ecran = { addr: v.addr, es: new EventSource(urlAgent(v.addr, "ecran")), term: null, hote, note: noteEl, cols: null };
  e.es.addEventListener("taille", (ev) => {
    if (v.ecran !== e) return;
    let t = {};
    try { t = JSON.parse(ev.data); } catch (_) { /* taille par défaut */ }
    e.cols = t.cols || 120;
    if (e.term) { e.term.reset(); e.term.resize(e.cols, t.rows || 40); return; } // reconnexion : repeint de zéro
    e.term = new Terminal({ cols: e.cols, rows: t.rows || 40, scrollback: 3000, disableStdin: true,
      cursorBlink: false, fontFamily: getComputedStyle(document.body).getPropertyValue("--mono") || "monospace",
      fontSize: v.tailleBrut || 12, theme: THEME_ECRAN, allowTransparency: false });
    e.term.open(hote);
    ajusterPolice(v);
    if (v.suivi) basDuChat(v);
  });
  e.es.onmessage = (ev) => {
    if (v.ecran !== e || !e.term) return;
    const bin = atob(ev.data);
    const octets = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) octets[i] = bin.charCodeAt(i);
    e.term.write(octets);
  };
  e.es.addEventListener("fin", () => {
    if (v.ecran !== e) return;
    e.es.close();
    noteEl.textContent = t("fil.terminalTermine");
  });
}

async function chargerChat(fusion) {
  const v = S.vue;
  if (!v || v.sous) return;
  try {
    const d = await api("GET", urlAgent(v.addr, "chat?n=200"));
    if (S.vue !== v) return;
    const etaitChat = v.mode === "chat";
    v.mode = d.mode;
    if (d.mode === "brut") chargerChoix(); // un dialogue à curseur s'y répond par la carte de choix
    if (d.mode === "brut" && ecranPossible()) brancherEcran(v, d.note);
    else if (d.mode === "brut") {
      fermerEcran(v);
      const garder = !v.suivi && v.flux.querySelector("pre.brut") ? v.chat.scrollTop : null;
      nouveauFil(v);
      v.flux.classList.add("terminal");
      if (texte(d.note)) v.flux.append(el("div", { class: "note" }, d.note));
      v.flux.append(el("pre", { class: "brut" }, texte(d.screen)));
      ajusterPolice(v);
      if (garder === null) basDuChat(v); else v.chat.scrollTop = garder;
    } else if (fusion && etaitChat) fusionnerFil(v, d.items);
    else remplacerFil(v, d.items);
  } catch (e) { if (e.status !== 401 && !sortieAgent(e)) statut(messageErreur(e), true); }
}

async function chargerSous(v) {
  if (v.charge) return;
  v.charge = true;
  try {
    const q = "subagent?id=" + encodeURIComponent(v.sous) + (v.offset !== null ? "&depuis=" + v.offset : "");
    const d = await api("GET", urlAgent(v.addr, q));
    if (S.vue !== v) return;
    if (v.offset === null || d.reset) remplacerFil(v, d.items);
    else suite(v, d.items);
    v.offset = typeof d.offset === "number" ? d.offset : null;
    statut("");
  } catch (e) {
    if (e.status === 404 && S.vue === v && trouver(v.addr)) statut(t("fil.sousIntrouvable"), true);
    else if (e.status !== 401 && !sortieAgent(e)) statut(messageErreur(e), true);
  } finally { v.charge = false; }
}

function ajouterChat(p) {
  const v = S.vue;
  if (!v || v.sous || p.addr !== v.addr || v.mode !== "chat") return;
  if (p.reset) remplacerFil(v, p.items); // reset : la liste reçue remplace tout le fil
  else suite(v, p.items);
}

async function repondreMenu(option, options) {
  await envoi(t("choix.reponse"), async (v) => {
    await api("POST", urlAgent(v.addr, "answer"), { option, options });
    v.carte.hidden = true;
    v.carte.replaceChildren();
    statut("");
    attendreSuite(v);
  });
}

// AskUserQuestion à plusieurs questions (2026-11-15) : d'une question à la suivante, puis à
// l'écran « Submit », l'agent reste « blocked » avec le même msg, le core n'émet rien et rien ne
// relançait la carte. Après une réponse, la page relit donc l'écran toutes les SUITE_PAS ms ; la
// carte reste cachée tant que l'écran porte encore la demande répondue (même signature), pour
// qu'un second appui ne réponde pas à l'aveugle à la question suivante.
const SUITE_PAS = 300, SUITE_ESSAIS = 10;
const signatureChoix = (d) => JSON.stringify([texte(d.contexte), d.choices || [], d.menu || null]);
function attendreSuite(v) {
  v.suite = { apres: v.sigCarte, essai: 0 };
  const relire = () => setTimeout(async () => {
    if (S.vue !== v || !v.suite) return;
    if (trouver(v.addr)?.state !== "blocked") { v.suite = null; return; } // reparti : rien à choisir
    // plafond testé ici aussi : une lecture en échec (core indisponible) ne remet pas v.suite à null
    if (v.suite.essai >= SUITE_ESSAIS) { v.suite = null; return; }
    v.suite.essai++;
    await chargerChoix();
    if (S.vue === v && v.suite) relire(); // l'écran n'a pas encore tourné
  }, SUITE_PAS);
  relire();
}

async function chargerChoix() {
  const v = S.vue;
  if (!v) return;
  try {
    const d = await api("GET", urlAgent(v.addr, "choices"));
    if (S.vue !== v) return;
    const sig = signatureChoix(d);
    const rien = !(d.choices || []).length && !(d.menu && Array.isArray(d.menu.options));
    if (v.suite) { // après une réponse : attendre que l'écran tourne, au plus SUITE_ESSAIS relectures
      if ((sig === v.suite.apres || rien) && v.suite.essai < SUITE_ESSAIS) return;
      v.suite = null;
      if (rien) { v.carte.hidden = true; v.carte.replaceChildren(); return; }
    }
    v.msgCarte = d.msg;
    v.sigCarte = sig;
    v.vueCarte = { contexte: texte(d.contexte), libelles: (d.choices || []).map((c) => texte(c.label)) };
    const kids = [el("div", { class: "question" }, texte(d.msg) || t("choix.demande"))];
    if (texte(d.contexte)) kids.push(el("div", { class: "contexte" }, d.contexte));
    // aperçus (AskUserQuestion à `preview`) : sur téléphone sous chaque option ; au pointeur, un
    // panneau sous la liste qui suit l'option survolée ou focalisée, comme la colonne du TUI
    const apercus = (d.choices || []).filter((c) => texte(c.preview));
    const panneau = apercus.length ? el("pre", { class: "apercu-panneau", ariaLive: "polite" }, texte(apercus[0].preview)) : null;
    for (const c of d.choices || []) {
      // « Type something. » : son chiffre seul ne répond rien dans le TUI (le champ prend le curseur) ;
      // la page ouvre un champ, et le texte part avec le chiffre (le serveur le colle, puis Entrée)
      const libre = SAISIE_LIBRE.includes(texte(c.label));
      const b = el("button", { type: "button", class: libre ? "choix-libre" : null,
        onclick: () => (libre ? ouvrirSaisieLibre(v, c.digit, sig) : repondre(c.digit)) }, texte(c.digit) + "  " + texte(c.label));
      if (texte(c.detail)) b.append(el("small", {}, c.detail));
      b.disabled = S.occupe;
      kids.push(b);
      if (texte(c.preview)) {
        const montrer = () => { if (panneau) panneau.textContent = texte(c.preview); };
        b.addEventListener("mouseenter", montrer);
        b.addEventListener("focus", montrer);
        kids.push(el("pre", { class: "apercu" }, texte(c.preview)));
      }
    }
    if (panneau) kids.push(panneau);
    const menu = d.menu && Array.isArray(d.menu.options) ? d.menu : null;
    if (menu) { // menu à curseur (« ❯ No, exit / Yes, I trust this folder ») : un bouton par option
      menu.options.forEach((libelleOpt, i) => {
        const b = el("button", { type: "button", class: i === menu.courant ? "sous-curseur" : null,
          onclick: () => repondreMenu(i, menu.options) }, (i === menu.courant ? "❯ " : "") + texte(libelleOpt));
        b.disabled = S.occupe;
        kids.push(b);
      });
    }
    if (!(d.choices || []).length && !menu) {
      if (v.mode === "brut") { v.carte.hidden = true; v.carte.replaceChildren(); return; } // rien à choisir
      kids.push(el("div", { class: "note" }, t("choix.aucune")));
    }
    v.carte.replaceChildren(...kids);
    v.carte.hidden = false;
    // carte reconstruite (événement, relecture) pendant la saisie : le champ et son texte restent
    if (v.libre && v.libre.sig === sig) ouvrirSaisieLibre(v, v.libre.digit, sig);
    else v.libre = null;
  } catch (e) { if (e.status !== 401 && !sortieAgent(e)) statut(messageErreur(e), true); }
}

// choix unique : « Type something. » ; question à cases : « [ ] Type something » (sans point)
const SAISIE_LIBRE = ["Type something.", "[ ] Type something"];
function ouvrirSaisieLibre(v, digit, sig) {
  const deja = v.carte.querySelector(".saisie-libre input");
  if (deja) { deja.focus(); return; }
  v.libre = { sig, digit, valeur: v.libre && v.libre.sig === sig ? v.libre.valeur : "" };
  const champ = el("input", { type: "text", class: "champ-libre", value: v.libre.valeur, placeholder: t("choix.taReponse"),
    ariaLabel: t("choix.libre"), enterKeyHint: "send", maxLength: 4000,
    oninput: () => { if (v.libre) v.libre.valeur = champ.value; } });
  const ok = el("button", { type: "submit", class: "action principal" }, t("choix.repondre"));
  ok.disabled = S.occupe;
  const f = el("form", { class: "saisie-libre", onsubmit: (ev) => {
    ev.preventDefault();
    const t = champ.value.trim();
    if (t) repondre(digit, t);
  } }, champ, ok);
  v.carte.append(f);
  champ.focus();
}

// ---------- commandes « / » de Claude Code ----------
async function chargerCommandes(v) {
  try {
    const d = await api("GET", urlAgent(v.addr, "commands"));
    if (S.vue === v) v.commandes = Array.isArray(d) ? d : [];
  } catch (_) { v.commandes = []; } // ancien serveur ou terminal : pas d'autocomplétion
}

function majCommandes(v) {
  const trouves = v.commandes.length ? COMMANDES.filtrer(v.commandes, v.champ.value, 40) : [];
  v.trouves = trouves;
  if (!trouves.length) { v.cmds.hidden = true; v.cmds.replaceChildren(); return; }
  v.selCmd = Math.min(v.selCmd, trouves.length - 1);
  v.cmds.replaceChildren(...trouves.map((c, i) => {
    const ligne = el("div", { class: "commande" + (i === v.selCmd ? " sel" : ""), role: "option" },
      el("span", { class: "cmd-nom" }, "/" + texte(c.name)),
      texte(c.args) ? el("span", { class: "cmd-args" }, " " + c.args) : null,
      el("span", { class: "cmd-desc" }, texte(c.description)),
      c.interactive ? el("span", { class: "cmd-inter" }, t("cmd.terminal")) : null,
      c.source && c.source !== "builtin" ? el("span", { class: "cmd-source" }, texte(c.source)) : null);
    ligne.addEventListener("pointerdown", (ev) => { ev.preventDefault(); choisirCommande(v, c); });
    return ligne;
  }));
  v.cmds.hidden = false;
  const sel = v.cmds.children[v.selCmd];
  if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
}

function choisirCommande(v, c) {
  v.champ.value = "/" + texte(c.name) + " ";
  ajusterChamp(v);
  majEnvoi(v);
  majPlaceholder(v);
  v.cmds.hidden = true;
  v.selCmd = 0;
  statut(c.interactive ? t("cmd.terminalNote", { nom: c.name }) : "");
  v.champ.focus();
}

function clavierCommandes(v, ev) {
  if (v.cmds.hidden || !v.trouves || !v.trouves.length) return;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    const n = v.trouves.length;
    v.selCmd = (v.selCmd + (ev.key === "ArrowDown" ? 1 : n - 1)) % n;
    majCommandes(v);
  } else if (ev.key === "Enter" || ev.key === "Tab") {
    const c = v.trouves[v.selCmd];
    if (ev.key === "Enter" && c && v.champ.value.trim() === "/" + c.name) return; // déjà complète : envoi
    ev.preventDefault();
    if (c) choisirCommande(v, c);
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    v.cmds.hidden = true;
  }
}

// ---------- envois ----------
// Exécute un envoi : boutons figés, statut, erreurs mappées (409 bloque → carte de choix).
async function envoi(attente, faire) {
  const v = S.vue;
  if (!v || v.sous || S.occupe) return;
  figer(true);
  statut(attente);
  try {
    await faire(v);
  } catch (e) {
    if (e.status !== 401 && !sortieAgent(e)) {
      if (e.code === "bloque") { statut(t("erreur.attendChoix"), true); chargerChoix(); }
      else if (e.code === "perime") { statut(t("erreur.perime"), true); chargerChoix(); }
      else statut(messageErreur(e), true);
    }
  }
  figer(false);
}

function repondre(digit, libre) {
  return envoi(t("envoi.attente"), async (v) => {
    // ce que la carte montre : si l'écran a tourné (réponse donnée dans le terminal), 409 perime
    // et envoi() réaffiche la carte de l'écran réel au lieu de répondre à l'aveugle
    const corps = { digit, msg: v.msgCarte || "",
      contexte: v.vueCarte ? v.vueCarte.contexte : "", libelles: v.vueCarte ? v.vueCarte.libelles : [] };
    if (libre != null) corps.texte = libre; // « Type something. » : la réponse saisie
    await api("POST", urlAgent(v.addr, "answer"), corps);
    v.libre = null;
    v.carte.hidden = true; // un second appui renverrait le chiffre
    v.carte.replaceChildren();
    statut("");
    attendreSuite(v); // AskUserQuestion à plusieurs questions : la suivante, puis Submit
  });
}

// saisie façon Discord : sur téléphone la touche Entrée va à la ligne (l'envoi passe par la
// flèche) ; au clavier physique Entrée envoie, Maj+Entrée va à la ligne
const TACTILE = () => !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
function ajusterChamp(v) { // le champ grandit avec le texte, jusqu'à son max-height (CSS)
  const c = v && v.champ;
  if (!c || !c.style) return;
  c.style.height = "auto";
  if (c.value && c.scrollHeight) c.style.height = c.scrollHeight + "px";
  else c.style.height = "";
}
function clavierEnvoi(v, ev) {
  if (ev.defaultPrevented || ev.key !== "Enter" || ev.shiftKey || ev.isComposing || TACTILE()) return;
  ev.preventDefault();
  const f = v.champ.form;
  if (f && f.requestSubmit) f.requestSubmit(); else envoyerTexte();
}

async function envoyerTexte() {
  if (S.vue && S.vue.pieces && S.vue.pieces.length) return envoyerPhotos(S.vue.pieces.map((x) => x.f));
  const tx = S.vue && S.vue.champ.value.trim();
  if (!tx) return;
  S.vue.cmds.hidden = true;
  await envoi(t("envoi.attente"), async (v) => {
    await api("POST", urlAgent(v.addr, "send"), { text: tx });
    v.sugg = null; // l'agent repart : l'ancienne suggestion ne doit pas revenir en placeholder
    v.champ.value = "";
    ajusterChamp(v);
    majEnvoi(v);
    majPlaceholder(v);
    statut("");
  });
  if (S.rechargement) { location.reload(); return; } // nouvelle version arrivée pendant la frappe
  if (S.vue && S.vue.champ) S.vue.champ.focus();
}

async function envoyerFichier(f, nom, route, max) {
  if (!S.vue || !f || S.occupe) return;
  if (f.size > max) { statut(t("envoi.tropGros", { n: max / 1048576 }), true); return; }
  await envoi(route === "voice" ? t("envoi.transcription") : t("envoi.attente"), async (v) => {
    const fd = new FormData();
    fd.append("file", f, nom);
    if (route === "photo" && v.champ.value.trim()) fd.append("caption", v.champ.value.trim());
    const r = await api("POST", urlAgent(v.addr, route), fd);
    // le texte transcrit arrive dans le fil comme un message envoyé ; un repli de modèle se dit
    statut(route === "voice" && r ? avertissementVoix(r) : "");
    if (route === "photo") { v.champ.value = ""; ajusterChamp(v); majEnvoi(v); }
  });
}

// plusieurs photos (galerie) : chacune déposée chez l'agent, puis UN message avec la légende et
// toutes les références — un seul tour de l'agent, pas un par photo
const MAX_PHOTOS = 10;
async function envoyerPhotos(fichiers) {
  if (!S.vue || S.occupe || !fichiers.length) return;
  if (fichiers.length > MAX_PHOTOS) { statut(t("envoi.maxPhotos", { n: MAX_PHOTOS }), true); return; }
  const trop = fichiers.find((f) => f.size > MAX_PHOTO);
  if (trop) { statut(t("envoi.photoTropGrosse", { nom: texte(trop.name), n: MAX_PHOTO / 1048576 }), true); return; }
  await envoi(t("envoi.attente"), async (v) => {
    const refs = [];
    for (let i = 0; i < fichiers.length; i++) {
      statut(t("envoi.photo", { i: i + 1, n: fichiers.length }));
      const fd = new FormData();
      fd.append("file", fichiers[i], fichiers[i].name);
      const r = await api("POST", urlAgent(v.addr, "put"), fd);
      refs.push(r.ref);
    }
    const legende = v.champ.value.trim();
    statut(t("envoi.attente"));
    await api("POST", urlAgent(v.addr, "send"), { text: (legende ? legende + " " : "") + refs.join(" ") });
    v.sugg = null;
    v.champ.value = "";
    ajusterChamp(v);
    viderPieces(v);
    majEnvoi(v);
    statut("");
  });
}

function depuisInput(ev, route, max) {
  const input = ev.target;
  const fichiers = input.files ? [...input.files] : [];
  input.value = "";
  fermerMenu();
  if (route === "photo") ajouterPieces(S.vue, fichiers);
  else if (fichiers[0]) envoyerFichier(fichiers[0], fichiers[0].name, route, max);
}

// photos préparées (demandé le 2026-11-14) : on choisit une ou plusieurs photos, elles s'affichent
// en vignettes au-dessus de la saisie, on écrit son commentaire, puis la flèche envoie le tout
function ajouterPieces(v, fichiers) {
  if (!v || !v.zonePieces) return;
  for (const f of fichiers) {
    if (v.pieces.length >= MAX_PHOTOS) { statut(t("envoi.maxPhotos", { n: MAX_PHOTOS }), true); break; }
    if (f.size > MAX_PHOTO) { statut(t("envoi.photoTropGrosse", { nom: texte(f.name), n: MAX_PHOTO / 1048576 }), true); continue; }
    const url = typeof URL !== "undefined" && URL.createObjectURL ? URL.createObjectURL(f) : "";
    const piece = { f, url };
    const retirer = el("button", { type: "button", class: "retirer-piece", title: t("envoi.retirer"), ariaLabel: t("envoi.retirerPhoto"),
      onclick: () => {
        v.pieces = v.pieces.filter((x) => x !== piece);
        if (piece.url && URL.revokeObjectURL) URL.revokeObjectURL(piece.url);
        piece.noeud.remove();
        majPieces(v);
      } }, "×");
    piece.noeud = el("div", { class: "piece" }, url ? el("img", { src: url, alt: texte(f.name) }) : el("span", {}, texte(f.name)), retirer);
    v.pieces.push(piece);
    v.zonePieces.append(piece.noeud);
  }
  majPieces(v);
  if (v.champ) v.champ.focus(); // le commentaire s'écrit tout de suite
}
function viderPieces(v) {
  if (!v || !v.pieces) return;
  for (const x of v.pieces) if (x.url && URL.revokeObjectURL) URL.revokeObjectURL(x.url);
  v.pieces = [];
  if (v.zonePieces) v.zonePieces.replaceChildren();
  majPieces(v);
}
function majPieces(v) {
  if (v.zonePieces) v.zonePieces.hidden = !v.pieces.length;
  majPlaceholder(v);
  majEnvoi(v);
}

// ---------- suggestion grisée de Claude Code ----------
// À la fin d'un tour, Claude Code propose une suite en gris dans son prompt (Tab ou → l'accepte).
// Le serveur la lit dans le dessin du terminal (herdr_web/suggestion.py) : jamais un brouillon.
// Elle arrive quelques secondes après la fin du tour, d'où les relectures rapprochées puis espacées.
const SUGG_DELAIS = [800, 2000, 4000, 8000];
const SUGG_POLL_MS = 15000;
function apresSaisie(v) { v.selCmd = 0; ajusterChamp(v); majCommandes(v); majEnvoi(v); majPlaceholder(v); }
function suggestionVisible(v) { // champ vide : la suggestion est le placeholder
  return !!(v && v.sugg && v.champ && texte(v.champ.value) === "" && !(v.pieces && v.pieces.length));
}
// champ qui commence la suggestion (« lance » pour « lance le checker ») : → ou Tab la complète, comme
// dans Claude Code ; le placeholder ne peut plus la montrer, la puce porte alors le texte entier
function suggestionPrefixe(v) {
  const t = v && v.champ ? texte(v.champ.value) : "";
  return !!(v.sugg && t !== "" && t !== v.sugg && v.sugg.startsWith(t) && !(v.pieces && v.pieces.length));
}
function curseurEnFin(c) {
  const n = texte(c.value).length;
  return c.selectionStart === n && c.selectionEnd === n;
}
function majPlaceholder(v) {
  if (!v || !v.champ) return;
  const s = suggestionVisible(v), pre = !s && suggestionPrefixe(v);
  v.champ.placeholder = s ? v.sugg : v.pieces && v.pieces.length ? t("envoi.commentaire") : t("envoi.message");
  v.champ.classList.toggle("avec-suggestion", s);
  if (!v.puceSugg) return;
  v.puceSugg.hidden = !s && !pre;
  v.puceSugg.textContent = "↹ " + (pre ? v.sugg : t("sugg.puce"));
}
function poserSuggestion(v, t) { v.sugg = t || null; majPlaceholder(v); }
function majSuggestion(v, a) {
  if (v.sous || !v.champ) return;
  const repos = estClaude(a) && ["idle", "done"].includes(a.state) && a.turn > 0;
  if (!repos) { // l'agent repart (ou n'a jamais parlé) : plus de suggestion
    if (v.suggTimer) clearTimeout(v.suggTimer);
    v.suggTimer = null; v.suggCle = null;
    if (v.sugg) poserSuggestion(v, null);
    return;
  }
  const cle = a.pid + ":" + a.turn;
  if (v.suggCle === cle) return; // done -> idle (vu) : même tour, rien à relire
  v.suggCle = cle; v.suggN = 0;
  if (v.sugg) poserSuggestion(v, null); // nouveau tour : l'ancienne suggestion n'a plus cours
  planifierSuggestion(v, SUGG_DELAIS[0]);
}
function planifierSuggestion(v, ms) {
  if (v.suggTimer) clearTimeout(v.suggTimer);
  v.suggTimer = setTimeout(() => chargerSuggestion(v), ms);
}
async function chargerSuggestion(v) {
  v.suggTimer = null;
  const cle = v.suggCle;
  if (S.vue !== v || !cle) return;
  if (!document.hidden) {
    try {
      const r = await api("GET", urlAgent(v.addr, "suggestion"));
      // réponse d'un autre tour (l'agent est reparti entre-temps) : ignorée
      if (S.vue === v && v.suggCle === cle && r && cle.endsWith(":" + r.turn)) poserSuggestion(v, texte(r.suggestion));
    } catch (_) { /* pas de suggestion : le champ reste comme il est */ }
  }
  if (S.vue !== v || v.suggCle !== cle) return;
  v.suggN += 1;
  planifierSuggestion(v, SUGG_DELAIS[v.suggN] || SUGG_POLL_MS);
}
function accepterSuggestion(v) { // recopiée, modifiable, jamais envoyée
  if (!suggestionVisible(v) && !suggestionPrefixe(v)) return;
  v.champ.value = v.sugg;
  v.champ.focus();
  if (v.champ.setSelectionRange) v.champ.setSelectionRange(v.sugg.length, v.sugg.length);
  apresSaisie(v);
}
// au clavier physique, champ vide ou début de la suggestion avec le curseur en fin : Tab ou →, comme
// dans Claude Code ; ailleurs (curseur au milieu, autre texte) les deux touches gardent leur rôle
function clavierSuggestion(v, ev) {
  if (ev.defaultPrevented || ev.isComposing || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey || TACTILE()) return;
  if (ev.key !== "Tab" && ev.key !== "ArrowRight") return;
  if (!suggestionVisible(v) && !(suggestionPrefixe(v) && curseurEnFin(v.champ))) return;
  ev.preventDefault();
  accepterSuggestion(v);
}

function fermerMenu() {
  const m = document.getElementById("menu-plus");
  const b = document.getElementById("plus");
  if (m) m.hidden = true;
  if (b) b.setAttribute("aria-expanded", "false");
}

function majEnvoi(v) { // façon Discord : flèche d'envoi seulement avec du texte, sinon le micro
  if (!v || !v.champ || !v.btnEnvoyer) return;
  const plein = texte(v.champ.value).trim() !== "" || !!(v.pieces && v.pieces.length);
  v.btnEnvoyer.hidden = !plein;
  if (v.btnMicro) v.btnMicro.hidden = !S.voix || (plein && !v.rec); // jamais pendant un enregistrement
}

function brancherSaisie(v) {
  const p = S.panneau;
  v.champ = p.querySelector("#champ");
  v.btnEnvoyer = p.querySelector("#envoyer");
  v.btnMicro = p.querySelector("#micro");
  v.pieces = [];
  v.zonePieces = el("div", { class: "pieces", hidden: true });
  const form = p.querySelector("#saisie");
  form.parentNode.insertBefore(v.zonePieces, form);
  v.puceSugg = el("button", { type: "button", class: "puce-suggestion", hidden: true,
    title: t("sugg.titre"), onclick: () => accepterSuggestion(v) }, "↹ " + t("sugg.puce"));
  p.querySelector(".barre-live").prepend(v.puceSugg); // même ligne que « Live » : puce à gauche, Live à droite
  p.querySelector("#saisie").addEventListener("submit", (ev) => { ev.preventDefault(); envoyerTexte(); });
  v.champ.addEventListener("input", () => apresSaisie(v));
  v.champ.addEventListener("keydown", (ev) => { clavierSuggestion(v, ev); clavierCommandes(v, ev); clavierEnvoi(v, ev); });
  // coller une capture d'écran (Ctrl+V) ou déposer une image sur le fil : elle rejoint les vignettes
  // (demandé le 2026-11-15) ; un collage de texte seul garde son comportement normal
  const images = (liste) => [...(liste || [])].filter((f) => f && /^image\//.test(f.type));
  v.champ.addEventListener("paste", (ev) => {
    const cd = ev.clipboardData;
    const fichiers = images(cd && cd.files && cd.files.length ? cd.files
      : [...((cd && cd.items) || [])].filter((i) => i.kind === "file").map((i) => i.getAsFile()));
    if (!fichiers.length) return;
    ev.preventDefault();
    ajouterPieces(v, fichiers.map((f, i) => f.name && f.name !== "image.png" ? f
      : new File([f], "capture-" + Date.now() + (i ? "-" + i : "") + "." + (f.type.split("/")[1] || "png"), { type: f.type })));
  });
  v.chat.addEventListener("dragover", (ev) => {
    if ([...((ev.dataTransfer && ev.dataTransfer.items) || [])].some((i) => i.kind === "file")) ev.preventDefault();
  });
  v.chat.addEventListener("drop", (ev) => {
    const fichiers = images(ev.dataTransfer && ev.dataTransfer.files);
    if (!fichiers.length) return;
    ev.preventDefault();
    ajouterPieces(v, fichiers);
  });
  v.champ.addEventListener("blur", () => { setTimeout(() => { if (S.vue === v) v.cmds.hidden = true; }, 150); });
  const plus = p.querySelector("#plus");
  const menu = p.querySelector("#menu-plus");
  plus.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
    plus.setAttribute("aria-expanded", String(!menu.hidden));
  });
  p.querySelector("#in-galerie").addEventListener("change", (ev) => depuisInput(ev, "photo", MAX_PHOTO));
  p.querySelector("#in-photo").addEventListener("change", (ev) => depuisInput(ev, "photo", MAX_PHOTO));
  p.querySelector("#in-voix").addEventListener("change", (ev) => depuisInput(ev, "voice", MAX_VOIX));
  brancherMicro(v, p.querySelector("#micro"), p.querySelector("#in-voix"));
  majEnvoi(v);
}
document.addEventListener("click", (ev) => { if (!ev.target.closest(".plus")) fermerMenu(); });

// ---------- micro : appui maintenu = enregistrement, relâcher = envoi ----------
function brancherMicro(v, bouton, repli) {
  const compteur = bouton.querySelector(".compteur");
  const capable = !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  bouton.addEventListener("contextmenu", (ev) => ev.preventDefault());
  if (!capable) { // pas d'enregistrement dans ce navigateur : choisir un fichier audio
    bouton.addEventListener("click", () => repli.click());
    return;
  }
  bouton.addEventListener("pointerdown", (ev) => {
    if (S.occupe || v.rec || ev.button > 0) return;
    ev.preventDefault();
    try { bouton.setPointerCapture(ev.pointerId); } catch (_) { /* pointeur déjà relâché */ }
    const rec = v.rec = { debut: performance.now(), dehors: false, fini: false, envoyer: false,
      morceaux: [], bouton, compteur, mr: null, flux: null };
    bouton.classList.add("enreg");
    compteur.hidden = false;
    const tic = () => {
      const s = Math.floor((performance.now() - rec.debut) / 1000);
      compteur.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0")
        + (rec.dehors ? t("micro.relacher") : t("micro.glisser"));
    };
    tic();
    rec.timer = setInterval(tic, 250);
    navigator.mediaDevices.getUserMedia({ audio: true }).then((flux) => {
      rec.flux = flux;
      if (rec.fini) { couperFlux(rec); return; } // relâché avant que le micro soit prêt
      const mr = rec.mr = new MediaRecorder(flux);
      mr.ondataavailable = (e) => { if (e.data && e.data.size) rec.morceaux.push(e.data); };
      mr.onstop = () => {
        couperFlux(rec);
        if (!rec.envoyer) return;
        const type = mr.mimeType || "audio/webm";
        const blob = new Blob(rec.morceaux, { type });
        const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        envoyerFichier(blob, "vocal." + ext, "voice", MAX_VOIX);
      };
      mr.start();
    }).catch(() => { finVocal(v, false); statut(t("micro.refuse"), true); });
  });
  bouton.addEventListener("pointermove", (ev) => {
    const rec = v.rec;
    if (!rec) return;
    const r = bouton.getBoundingClientRect();
    const marge = 24;
    rec.dehors = ev.clientX < r.left - marge || ev.clientX > r.right + marge
      || ev.clientY < r.top - marge || ev.clientY > r.bottom + marge;
    bouton.classList.toggle("dehors", rec.dehors);
  });
  bouton.addEventListener("pointerup", () => { if (v.rec) finVocal(v, !v.rec.dehors); });
  bouton.addEventListener("pointercancel", () => { if (v.rec) finVocal(v, false); });
  bouton.addEventListener("lostpointercapture", () => { if (v.rec) finVocal(v, false); });
}

function couperFlux(rec) { if (rec.flux) for (const t of rec.flux.getTracks()) t.stop(); }

function finVocal(v, envoyer) {
  const rec = v.rec;
  if (!rec) return;
  v.rec = null;
  rec.fini = true;
  clearInterval(rec.timer);
  rec.bouton.classList.remove("enreg", "dehors");
  rec.compteur.hidden = true;
  const court = performance.now() - rec.debut < VOCAL_MIN_MS;
  rec.envoyer = envoyer && !court;
  if (envoyer && court) statut(t("micro.maintenir"));
  else if (!envoyer) statut(t("micro.annule"));
  if (rec.mr && rec.mr.state !== "inactive") rec.mr.stop(); // onstop envoie si rec.envoyer
  else couperFlux(rec);
}

// ---------- flux SSE ----------
function surAgents(liste) {
  const avant = new Map(S.agents.map((a) => [a.address, a]));
  S.agents = Array.isArray(liste) ? liste : [];
  if (!S.premier) {
    for (const a of S.agents) {
      const p = avant.get(a.address);
      if (a.state !== "blocked" || (p && p.state === "blocked")) continue;
      const regarde = S.vue && S.vue.addr === a.address && !S.vue.sous;
      if (!regarde) annoncer(a, masque(CLE_NOTIF)); // la notification vibre déjà : une seule vibration
      if (!regarde || document.hidden) notifierNeedsYou(a);
    }
  }
  suivreBandeau();
  // space ET onglet : un onglet renommé ailleurs (herdr-app) doit recharger S.spaces, sinon le
  // crayon, qui exige le même nom des deux côtés, disparaît jusqu'au prochain rechargement
  const cle = (a) => texte(a.space) + "\u0000" + texte(a.tab);
  const nomsAvant = [...new Set([...avant.values()].map(cle))].sort().join("|");
  const nomsApres = [...new Set(S.agents.map(cle))].sort().join("|");
  if (!S.premier && nomsAvant !== nomsApres) chargerSpaces().then(rendreCote);
  S.premier = false;
  majBandeaux();
  if (S.confirmation && S.confirmation.maj) S.confirmation.maj();
  if (document.querySelector(".connexion")) return;
  rendreCote();
  suivreNouvelAgent();
  if (!S.vue) { rendreAccueil(); return; }
  const a = trouver(S.vue.addr);
  if (!a) { location.hash = "#/"; return; }
  majVueAgent();
  if (S.vue.sous) return;
  if (a.pid !== S.vue.pid) { S.vue.pid = a.pid; chargerChat(); } // nouvelle session : le flux se tait
  else if (S.vue.mode === "brut") chargerChat();
}

function connecterFlux() {
  if (S.es) S.es.close();
  const es = S.es = new EventSource("/api/stream");
  let ouvert = false;
  es.onopen = () => {
    // reconnexion : le serveur ne rejoue rien, relire ce qui a pu arriver pendant la coupure
    if (ouvert && S.vue && !S.vue.sous) {
      chargerChat(true); // fusion : ni saut en bas, ni outil replié (m1)
      if (trouver(S.vue.addr)?.state === "blocked") chargerChoix();
    }
    ouvert = true;
  };
  es.addEventListener("agents", (e) => surAgents(JSON.parse(e.data)));
  es.addEventListener("chat", (e) => ajouterChat(JSON.parse(e.data)));
  es.addEventListener("barre", (e) => { S.valsBarre = JSON.parse(e.data); rendreBarre(); });
  es.addEventListener("etat", (e) => { S.connecte = !!JSON.parse(e.data).connecte; majBandeaux(); });
  es.addEventListener("reglages", (e) => appliquerReglagesServeur(JSON.parse(e.data)));
  es.addEventListener("ping", () => {});
  es.onerror = async () => {
    // EventSource ne dit pas pourquoi : sonder pour distinguer un jeton refusé d'une coupure
    try {
      const r = await fetch("/api/agents", { credentials: "same-origin" });
      if (r.status === 401) afficherConnexion();
    } catch (_) { /* hors ligne : EventSource retente seul */ }
  };
}

// ---------- « vu » partagé avec herdr-app (done -> idle) ----------
// herdr-app pose le label `herdr.seen` (tour regardé) quand on regarde un terminal, et un done dont
// le tour est vu s'affiche idle. Regarder un agent sur la page pose le même label (POST /seen) : le
// serveur le lit à son tour, et l'agent passe idle des deux côtés.
function marquerVu(v, a) {
  if (!v || v.sous || !a || document.hidden) return;
  const turn = typeof a.turn === "number" && isFinite(a.turn) ? a.turn : null;
  if (turn == null || (typeof a.seen === "number" && a.seen >= turn) || v.vuEnvoye === turn) return;
  v.vuEnvoye = turn;
  // échec : le tour reste marqué envoyé, nouvel essai au tour suivant ou au retour sur la page
  // (avant : à chaque rafraîchissement des agents, sans pause)
  api("POST", urlAgent(a.address, "seen"), {}).catch(() => { v.vuEchoue = true; });
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !S.vue) return;
  if (S.vue.vuEchoue) { S.vue.vuEchoue = false; S.vue.vuEnvoye = null; }
  marquerVu(S.vue, trouver(S.vue.addr));
});

// ---------- réglages (roue crantée) ----------
const CLE_NOTIF = "herdr.notif-needs-you";
const CLE_MASQUE_TERMINAUX = "herdr.masque-terminaux";
// préférences de l'appareil : seulement ces deux clés, jamais une valeur venue d'ailleurs
const masque = (cle) => (cle === CLE_NOTIF ? lireStockage(CLE_NOTIF)
  : cle === CLE_MASQUE_TERMINAUX ? lireStockage(CLE_MASQUE_TERMINAUX) : null) === "1";

// Langue choisie (réglage serveur, partagé) : la page en cours la reçoit (événement « reglages ») et se
// recharge — tout de suite si le champ est vide, sinon après le prochain envoi, comme une mise à jour.
function suivreLangue(l) {
  if (typeof l !== "string" || !I18N.LANGUES.includes(l) || l === I18N.courante() || S.rechargement) return;
  S.rechargement = true;
  const brouillon = S.vue && S.vue.champ && S.vue.champ.value.trim();
  if (!brouillon) location.reload();
}
function appliquerReglagesServeur(d) { // { handy, nom_affiche, langue } : suivent le serveur, sur chaque appareil
  if (!d) return;
  suivreLangue(d.langue);
  if (typeof d.nom_affiche === "string" && d.nom_affiche.trim()) {
    S.nom = d.nom_affiche.trim();
    if (S.vue && !S.vue.sous && S.vue.flux) // les bulles déjà à l'écran prennent le nom reçu
      for (const n of S.vue.flux.querySelectorAll(".groupe-tete .auteur.user")) n.textContent = S.nom;
  }
  if (d.voix && typeof d.voix === "object" && S.reglages && S.reglages.majVoix) S.reglages.majVoix(d.voix);
  if (typeof d.handy !== "boolean") return;
  S.voix = d.handy;
  if (S.vue) majEnvoi(S.vue);
}
async function chargerReglages() {
  try {
    const d = await api("GET", "/api/reglages");
    if (d && d.handy) appliquerReglagesServeur({ handy: d.handy.active, nom_affiche: d.nom_affiche, langue: d.langue });
    return d;
  } catch (_) { return null; } // ancien serveur : la voix reste comme avant
}

async function notifierNeedsYou(a) { // réglage par appareil ; la permission a été donnée au clic sur la coche
  if (!masque(CLE_NOTIF)) return;
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const titre = t("bandeau.needsYou", { nom: nomAgent(a) });
  const opts = { body: texte(a.msg), icon: "/static/icon-192.png", tag: a.address, data: { addr: a.address } };
  try { // Android refuse `new Notification` : passer par le service worker quand il y en a un
    const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : null;
    if (reg) await reg.showNotification(titre, opts);
    else new Notification(titre, opts);
  } catch (_) { /* notification refusée par le navigateur : le bandeau reste */ }
}

function boutonReglages() {
  const b = el("button", { type: "button", class: "bouton-reglages", title: t("reglages.titre"), ariaLabel: t("reglages.titre"),
    onclick: ouvrirReglages });
  b.append(svg("0 0 24 24", "ico",
    ["path", { d: "M10.3 3h3.4l.5 2.4 1.9 1.1 2.3-.8 1.7 2.9-1.8 1.7v2.2l1.8 1.6-1.7 3-2.3-.8-1.9 1.1-.5 2.4h-3.4l-.5-2.4-1.9-1.1-2.3.8-1.7-3 1.8-1.6V10.3L3.9 8.6l1.7-2.9 2.3.8 1.9-1.1z",
      fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }],
    ["circle", { cx: "12", cy: "12", r: "3", fill: "none", stroke: "currentColor", "stroke-width": "1.8" }]));
  return b;
}

const RUBRIQUES = ["voix", "notif", "affichage", "appareils", "apropos"];
// Textes du panneau de réglages : mêmes clés qu'avant, valeurs lues dans static/i18n/{en,fr}.js (« reglages.… »).
const TXT_REGLAGES = (() => {
  const sous = (pre, cles) => Object.fromEntries(cles.map((k) => [k, t(pre + k)]));
  const L = sous("reglages.", ["titre", "fermer", "retour", "rubriques", "lecture", "illisibles", "inconnu", "nonLisible",
    "versionInconnue", "copier", "copie", "copieImpossible"]);
  L.rub = sous("reglages.rub.", RUBRIQUES);
  L.voix = sous("reglages.voix.", ["activee", "aide", "enregistrement", "onTous", "offTous", "moteur", "handy", "pret",
    "indispo", "coupee", "modele", "langue", "delai", "ffmpeg", "trouve", "introuvable", "details", "detailsAide", "binaire",
    "modeleChemin", "ffmpegChemin", "binIntrouvable", "modeleIllisible", "suggestion", "suggestion1", "suggestion2",
    "siteProjet", "installer", "confirmerInstall", "confirmerInstallAide", "lancer", "annuler", "lancement", "aucunSpace",
    "transcription", "depuisHandy", "importer", "importerAide", "importe", "enregistre", "modeleHw", "modeleAide", "langueHw",
    "langueAide", "vocabulaire", "vocabulaireAide", "reference", "deHandy"]);
  L.voix.ecart = (h, a) => t("reglages.voix.ecart", { h, a });
  L.notif = sous("reglages.notif.", ["needsYou", "nom", "aide", "nonPrises", "refusee", "activee"]);
  L.affichage = sous("reglages.affichage.", ["sections", "terminaux", "terminauxAide", "tri",
    "triAide", "raccourcis", "palette", "paletteAide", "nettoyer", "nettoyerAide", "nettoyerCombo", "nettoyerComboAide",
    "modifier", "capture", "defaut", "captureAide", "annule", "nom", "nomAide", "nomEnregistre", "langue", "langueAide",
    "langueEnregistree"]);
  L.affichage.tris = sous("reglages.affichage.tris.", ["priority", "grouped"]);
  L.affichage.enregistre = (combo) => t("reglages.affichage.enregistre", { combo });
  L.appareils = sous("reglages.appareils.", ["nouvel", "connecter", "connecterAide", "acces", "push", "pushAucun", "pushOff",
    "cookie", "modeleSession", "session", "fermerSession", "deconnexion", "deconnexionAide", "confirmer", "oui", "annuler",
    "creation", "copierLien", "copie", "copieImpossible", "nouveau", "qrIndispo", "qrNom", "expire"]);
  L.appareils.jours = (n) => t("reglages.appareils.jours", { n });
  L.appareils.scanner = (heure) => t("reglages.appareils.scanner", { heure });
  L.apropos = sous("reglages.apropos.", ["version", "commit", "cache", "horsLigne", "recharger", "rechargerAide"]);
  return L;
})();
const CLE_RUBRIQUE = "herdr.reglages-rubrique";
const ICONES_RUBRIQUES = { // lucide, à main levée : micro, cloche, mise en page, téléphone, info
  voix: ["M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM19 11a7 7 0 0 1-14 0M12 18v3"],
  notif: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  affichage: ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM3 9h18M9 21V9"],
  appareils: ["M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM12 18h.01"],
  apropos: ["M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 16v-4M12 8h.01"],
};
const iconeRubrique = (k) => svg("0 0 24 24", "ico", ...ICONES_RUBRIQUES[k].map((d) => ["path", { d, fill: "none",
  stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]));
let nIdReglage = 0;
const idReglage = () => "rg-" + (++nIdReglage);

// Ligne d'une carte : intitulé et description à gauche, contrôle ou valeur à droite.
function ligneReglage(intitule, description, droite) {
  const t = el("div", { class: "intitule", id: idReglage() }, intitule);
  const d = texte(description) ? el("div", { class: "description", id: idReglage() }, description) : null;
  return el("div", { class: "reglage-ligne" }, el("div", { class: "reglage-texte" }, t, d),
    droite != null ? el("div", { class: "reglage-droite" }, droite) : null);
}
function valeurReglage(val, manque, classe) {
  return el("span", { class: "valeur" + (texte(val) ? (classe ? " " + classe : "") : " manque") },
    texte(val) || manque || TXT_REGLAGES.nonLisible);
}
const carteReglages = (...kids) => el("div", { class: "carte-reglages" }, ...kids);
const titreCarte = (t) => el("div", { class: "carte-titre" }, t);

// Champ « nom affiché » : enregistré à la sortie du champ (ou Entrée), vide = nom du compte.
function champNom(msg, L) {
  const c = el("input", { type: "text", class: "champ-reglage", maxLength: 60, value: S.nom, ariaLabel: L.affichage.nom });
  const enregistrer = async () => {
    try {
      const r = await api("POST", "/api/reglages", { nom_affiche: c.value.trim() });
      appliquerReglagesServeur({ nom_affiche: r.nom_affiche });
      c.value = S.nom;
      msg.textContent = L.affichage.nomEnregistre;
    } catch (e) { msg.textContent = e.status === 401 ? "" : messageErreur(e); }
  };
  c.addEventListener("change", enregistrer);
  c.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); c.blur(); } });
  return c;
}

// Interrupteur accessible : role="switch", aria-checked, Espace et Entrée au clavier. La propriété
// `checked` garde la forme d'une case, pour que les rappels puissent revenir en arrière en cas d'échec.
function interrupteur(nom, aide, vrai, onchange) {
  const sw = el("span", { class: "interrupteur", role: "switch", tabIndex: 0 }, el("span", { class: "bouton-glissant" }));
  let etat = !!vrai;
  Object.defineProperty(sw, "checked", { get: () => etat,
    set: (v) => { etat = !!v; sw.setAttribute("aria-checked", String(etat)); } });
  sw.checked = etat;
  const basculer = () => { sw.checked = !sw.checked; onchange(sw); };
  sw.addEventListener("click", basculer);
  sw.addEventListener("keydown", (ev) => { if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); basculer(); } });
  const ligne = ligneReglage(nom, aide, sw);
  ligne.classList.add("reglage-bascule");
  const [t, d] = ligne.querySelector(".reglage-texte").children;
  sw.setAttribute("aria-labelledby", t.id);
  if (d) sw.setAttribute("aria-describedby", d.id);
  t.addEventListener("click", basculer); // l'intitulé bascule aussi, comme le libellé d'une case
  return ligne;
}

// Contrôle segmenté (boutons radio) : un choix parmi quelques-uns, flèches gauche/droite au clavier.
function segmente(nom, choix, valeur, onchange) {
  const g = el("div", { class: "segmente", role: "radiogroup", ariaLabel: nom });
  const boutons = choix.map(([k, libelle]) => el("button", { type: "button", role: "radio", class: "segment",
    onclick: () => choisir(k, true) }, libelle));
  boutons.forEach((b, i) => { b.dataset.valeur = choix[i][0]; });
  function choisir(k, signaler) {
    boutons.forEach((b) => { const on = b.dataset.valeur === k; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
    if (signaler) onchange(k);
  }
  g.addEventListener("keydown", (ev) => {
    const d = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    ev.preventDefault();
    const i = boutons.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const b = boutons[(i + d + boutons.length) % boutons.length];
    choisir(b.dataset.valeur, true);
    b.focus();
  });
  g.append(...boutons);
  choisir(valeur, false);
  return g;
}

// Chemin complet : monospace, coupé de préférence après un « / » (<wbr>), bouton copier.
// Le serveur décrit ses erreurs par un motif et ses paramètres ; son texte (français) ne sert qu'en repli
// d'un serveur d'avant, qui n'envoie pas de motif.
const motifTraduit = (motif, params) => (typeof motif === "string" && I18N.existe("motif." + motif)
  ? t("motif." + motif, params && typeof params === "object" ? params : {}) : "");
const erreurBinaire = (h) => motifTraduit(h.bin_motif, h.bin_params) || texte(h.bin_erreur);
const avertissementVoix = (r) => motifTraduit(r.motif, r.params) || texte(r.avertissement);

function ligneChemin(nom, val, manque) {
  const v = texte(val);
  const copier = v ? el("button", { type: "button", class: "copier", title: t("reglages.copierNom", { nom }),
    onclick: async () => {
      try { await navigator.clipboard.writeText(v); copier.textContent = TXT_REGLAGES.copie; }
      catch (_) { copier.textContent = TXT_REGLAGES.copieImpossible; }
    } }, TXT_REGLAGES.copier) : null;
  return el("div", { class: "chemin" }, el("div", { class: "nom" }, nom),
    el("code", { class: "val" + (v ? "" : " manque") },
      ...(v ? v.split(/(?<=\/)/).flatMap((p, i) => (i ? [el("wbr"), p] : [p])) : [manque || TXT_REGLAGES.nonLisible])), copier);
}

// « handy-computer/Qwen3-ASR-1.7B-gguf/Qwen3-ASR-1.7B-Q8_0.gguf » -> « Qwen3-ASR 1.7B · Q8 »
function resumeModele(m) {
  let n = texte(m).split(/[\\/]/).pop().replace(/\.(gguf|bin|onnx|safetensors|pt)$/i, "");
  if (!n) return "";
  let quant = "", taille = "";
  const q = n.match(/[-_.]((?:Q|IQ)\d+(?:_[A-Z0-9]+)*|F16|F32|BF16)$/i);
  if (q) { quant = q[1].toUpperCase().replace(/_0$/, ""); n = n.slice(0, q.index); }
  const t = n.match(/[-_](\d+(?:\.\d+)?[BM])$/i);
  if (t) { taille = t[1].toUpperCase(); n = n.slice(0, t.index); }
  return n + (taille ? " " + taille : "") + (quant ? " · " + quant : "");
}
const versionCourte = (v) => { // « v26 · abc123d » : numéro du cache du service worker et commit servi
  const n = texte(v && v.cache).match(/-v(\d+)$/);
  const c = texte(v && v.commit).slice(0, 7);
  return [n ? "v" + n[1] : "", c].filter(Boolean).join(" · ") || TXT_REGLAGES.versionInconnue;
};

function fermerReglages() {
  if (S.reglages) S.reglages.racine.remove();
  S.reglages = null;
  S.captureRaccourci = false; // le bouton en capture part avec le panneau, sans blur
}

async function ouvrirReglages() {
  fermerReglages();
  const L = TXT_REGLAGES;
  const corps = el("div", { class: "reglages-corps" }, el("div", { class: "note-cote" }, L.lecture));
  const fermer = el("button", { type: "button", class: "bouton-reglages fermer-reglages", title: L.fermer,
    ariaLabel: L.fermer, onclick: fermerReglages });
  fermer.append(iconeCroix());
  const boite = el("div", { class: "boite reglages", role: "dialog", ariaModal: "true", ariaLabel: L.titre },
    el("div", { class: "reglages-tete" }, el("div", { class: "boite-titre" }, L.titre), fermer), corps);
  const racine = el("div", { class: "voile voile-reglages" }, boite);
  racine.addEventListener("click", (ev) => { if (ev.target === racine) fermerReglages(); });
  racine.addEventListener("keydown", (ev) => { if (ev.key === "Escape") fermerReglages(); });
  S.reglages = { racine, corps, boite };
  document.body.append(racine);
  fermer.focus();
  const d = await chargerReglages();
  if (!S.reglages || S.reglages.corps !== corps) return; // refermé pendant la lecture
  const pages = sectionsReglages(d);
  const lu = lireStockage(CLE_RUBRIQUE);
  const onglets = {};
  const nav = el("div", { class: "reglages-nav", role: "tablist", ariaLabel: L.rubriques, ariaOrientation: "vertical" });
  function montrer(k, ouvrirPage) {
    for (const r of RUBRIQUES) {
      const on = r === k;
      onglets[r].setAttribute("aria-selected", String(on));
      onglets[r].tabIndex = on ? 0 : -1;
      pages[r].hidden = !on;
    }
    ecrireStockage(CLE_RUBRIQUE, k);
    // téléphone : la liste des rubriques, puis la page choisie (classe sans effet sur un écran large)
    boite.classList.toggle("sur-page", !!ouvrirPage);
    if (ouvrirPage && window.matchMedia && window.matchMedia("(max-width: 600px)").matches) {
      pages[k].querySelector(".retour-reglages").focus();
    }
  }
  for (const k of RUBRIQUES) {
    const id = idReglage();
    const b = el("button", { type: "button", role: "tab", class: "onglet-reglages", id, onclick: () => montrer(k, true) },
      iconeRubrique(k), el("span", { class: "nom" }, L.rub[k]), el("span", { class: "chevron", ariaHidden: "true" }, "›"));
    b.dataset.rubrique = k;
    b.setAttribute("aria-controls", id + "-page");
    pages[k].id = id + "-page";
    pages[k].setAttribute("aria-labelledby", id);
    const retour = el("button", { type: "button", class: "retour-reglages", onclick: () => {
      boite.classList.remove("sur-page"); b.focus();
    } }, "‹ " + L.retour);
    pages[k].prepend(el("div", { class: "page-tete" }, retour, el("h2", { class: "reglages-titre" }, L.rub[k])));
    onglets[k] = b;
    nav.append(b);
  }
  nav.addEventListener("keydown", (ev) => {
    const d = ev.key === "ArrowDown" ? 1 : ev.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    ev.preventDefault();
    const i = RUBRIQUES.indexOf(RUBRIQUES.find((r) => onglets[r].getAttribute("aria-selected") === "true"));
    const k = RUBRIQUES[(i + d + RUBRIQUES.length) % RUBRIQUES.length];
    montrer(k, boite.classList.contains("sur-page"));
    onglets[k].focus();
  });
  nav.append(el("div", { class: "reglages-version", title: texte(d && d.version && d.version.commit) || null },
    versionCourte(d && d.version)));
  remplacer(corps, nav, el("div", { class: "reglages-pages" }, ...RUBRIQUES.map((k) => pages[k])));
  montrer(RUBRIQUES.includes(lu) ? lu : RUBRIQUES[0], false);
}

// Une page par rubrique, { voix, notif, affichage, appareils, apropos } : cartes de lignes.
// Handy absent : deux lignes, le dépôt officiel, et un agent Claude Code qui l'installe — après confirmation.
const HANDY_DEPOT = "https://github.com/cjpais/Handy"; // identifiant com.pais.handy (src-tauri/tauri.conf.json)
function suggestionHandy(h, L, lienProjet) {
  const zone = el("div", { class: "zone-install" });
  const msg = el("div", { class: "note-cote", role: "status" });
  const ligne = () => ligneReglage(L.voix.suggestion, "", el("button", { type: "button", class: "action principal",
    onclick: demander }, L.voix.installer));
  function demander() {
    const non = el("button", { type: "button", class: "action", onclick: () => {
      remplacer(zone, ligne()); zone.querySelector("button").focus(); } }, L.voix.annuler);
    remplacer(zone, el("div", { class: "confirmation", role: "alertdialog", ariaLabel: L.voix.confirmerInstall },
      el("div", { class: "intitule" }, L.voix.confirmerInstall),
      el("div", { class: "description" }, L.voix.confirmerInstallAide),
      el("div", { class: "rangee" }, non, el("button", { type: "button", class: "action principal", onclick: lancer }, L.voix.lancer))));
    non.focus();
  }
  async function lancer(ev) {
    const ok = ev && ev.target;
    if (ok) ok.disabled = true; // un double appui ne lance pas deux agents
    msg.textContent = L.voix.lancement;
    try {
      if (!S.spaces.some((x) => texte(x.id))) S.spaces = await api("GET", "/api/spaces") || [];
      const avecId = S.spaces.filter((x) => texte(x.id));
      const sp = avecId.find((x) => texte(x.folder) && x.folder === h.dossier_perso) || avecId[0];
      if (!sp) { msg.textContent = L.voix.aucunSpace; if (ok) ok.disabled = false; return; }
      await ouvrirDansSpace(sp, "claude", "default", null, "installer-handy");
      fermerReglages(); // la page ouvre l'agent dès qu'il paraît (suivreNouvelAgent)
    } catch (e) {
      msg.textContent = e.status === 401 ? "" : messageErreur(e);
      remplacer(zone, ligne());
    }
  }
  remplacer(zone, ligne());
  return el("div", { class: "carte-reglages vedette suggestion-handy" }, zone,
    el("div", { class: "suggestion-texte" }, el("div", null, L.voix.suggestion1), el("div", null, L.voix.suggestion2),
      el("div", null, lienProjet(HANDY_DEPOT.replace(/^https:\/\//, "")))),
    msg);
}

// Paramètres de transcription de herdr-web : importés de Handy (sens unique), puis modifiés ici.
// Seul le modèle est passé à `handy --transcribe-file` ; langue et vocabulaire servent de référence.
function carteTranscription(v0, L, modeleHandy) {
  const v = v0 && typeof v0 === "object" ? v0 : {};
  const msg = el("div", { class: "msg-reglage", role: "status" });
  const ecart = el("div", { class: "note-cote ecart-modele" });
  const majEcart = (x) => { // le modèle forcé ne suit plus Handy : le dire
    const a = texte(x.modele), h = texte(modeleHandy);
    ecart.textContent = a && h && a !== h ? L.voix.ecart(h, a) : "";
  };
  const modele = el("input", { type: "text", class: "champ-reglage", maxLength: 200, ariaLabel: L.voix.modeleHw,
    placeholder: L.voix.deHandy });
  const langue = el("input", { type: "text", class: "champ-reglage court", maxLength: 12, ariaLabel: L.voix.langueHw,
    placeholder: L.voix.deHandy });
  const mots = el("textarea", { class: "champ-reglage mots", rows: 3, ariaLabel: L.voix.vocabulaire });
  const remplir = (x) => {
    modele.value = texte(x.modele); langue.value = texte(x.langue);
    mots.value = Array.isArray(x.vocabulaire) ? x.vocabulaire.filter((m) => typeof m === "string").join("\n") : "";
    majEcart(x);
  };
  remplir(v);
  if (S.reglages) S.reglages.majVoix = (x) => { // diffusé par un autre appareil (événement « reglages »)
    if (![modele, langue, mots].includes(document.activeElement)) remplir(x);
  };
  const enregistrer = async () => {
    const voix = { modele: modele.value.trim() || null, langue: langue.value.trim() || null,
      vocabulaire: mots.value.split("\n").map((m) => m.trim()).filter(Boolean) };
    try {
      const r = await api("POST", "/api/reglages", { voix });
      if (r && r.voix) remplir(r.voix);
      msg.textContent = L.voix.enregistre;
    } catch (e) { msg.textContent = e.status === 401 ? "" : messageErreur(e); }
  };
  for (const c of [modele, langue, mots]) c.addEventListener("change", enregistrer);
  for (const c of [modele, langue]) c.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); c.blur(); } });
  const importer = el("button", { type: "button", class: "action", onclick: async () => {
    try {
      const r = await api("POST", "/api/reglages/voix/import", {});
      if (r && r.voix) remplir(r.voix);
      msg.textContent = L.voix.importe;
    } catch (e) { msg.textContent = e.status === 401 ? "" : messageErreur(e); }
  } }, L.voix.importer);
  return carteReglages(
    ligneReglage(L.voix.depuisHandy, L.voix.importerAide, importer),
    ligneReglage(L.voix.modeleHw, L.voix.modeleAide, modele), ecart,
    ligneReglage(L.voix.langueHw, L.voix.langueAide, langue),
    ligneReglage(L.voix.vocabulaire, L.voix.vocabulaireAide, mots),
    el("div", { class: "note-cote" }, L.voix.reference), msg);
}

function sectionsReglages(d) {
  const L = TXT_REGLAGES;
  const page = (k, ...kids) => el("section", { class: "reglages-page", role: "tabpanel" }, ...kids.filter(Boolean));
  // Voix : ce que la voix utilise vraiment (Handy), résumé lisible, chemins complets repliés
  const h = (d && d.handy) || null;
  const msgVoix = el("div", { class: "note-cote" });
  let voix;
  if (!h) voix = page("voix", el("div", { class: "note-cote erreur" }, L.illisibles));
  else {
    const absent = h.present === false || !texte(h.bin); // serveur d'avant sans « present » : le binaire suffit
    const pret = !absent && !!texte(h.ffmpeg);
    const pastille = el("span", { class: "etat-voix" });
    const majPastille = () => {
      pastille.className = "etat-voix " + (!S.voix ? "coupee" : pret ? "pret" : "indispo");
      pastille.textContent = !S.voix ? "○ " + L.voix.coupee : pret ? "● " + L.voix.pret : "✕ " + L.voix.indispo;
    };
    majPastille();
    const bascule = interrupteur(L.voix.activee, L.voix.aide, S.voix, async (c) => {
      const voulu = c.checked;
      msgVoix.textContent = L.voix.enregistrement;
      try {
        const r = await api("POST", "/api/reglages", { handy: voulu });
        appliquerReglagesServeur({ handy: r.handy });
        msgVoix.textContent = r.handy ? L.voix.onTous : L.voix.offTous;
      } catch (e) {
        c.checked = !voulu;
        msgVoix.textContent = e.status === 401 ? "" : messageErreur(e);
      }
      majPastille();
    });
    const ff = texte(h.ffmpeg);
    const details = el("details", { class: "details-reglages" }, el("summary", null, L.voix.details,
      el("span", { class: "description" }, " · " + L.voix.detailsAide)),
    ligneChemin(L.voix.binaire, h.bin, erreurBinaire(h) || L.voix.introuvable),
    ligneChemin(L.voix.modeleChemin, h.modele, L.voix.modeleIllisible),
    ligneChemin(L.voix.ffmpegChemin, h.ffmpeg, L.voix.introuvable));
    const depot = /^https:\/\/github\.com\//.test(texte(h.depot)) ? h.depot : HANDY_DEPOT;
    const lienProjet = (libelle) => el("a", { class: "lien-projet", href: depot, target: "_blank", rel: "noopener noreferrer" }, libelle);
    voix = page("voix", carteReglages(bascule, msgVoix),
      absent ? suggestionHandy(h, L, lienProjet) : null,
      titreCarte(L.voix.moteur),
      carteReglages(
        ligneReglage(L.voix.handy + (texte(h.version) ? " " + h.version : ""), pret ? "" : erreurBinaire(h), pastille),
        ligneReglage(L.voix.modele, "", valeurReglage(resumeModele(h.modele), L.voix.modeleIllisible)),
        ligneReglage(L.voix.langue, "", valeurReglage(h.langue, L.voix.modeleIllisible)),
        ligneReglage(L.voix.delai, "", valeurReglage(typeof h.delai_s === "number" ? h.delai_s + " s" : "")),
        ligneReglage(L.voix.ffmpeg, "", ff ? el("span", { class: "valeur ok" }, "✓ " + L.voix.trouve)
          : el("span", { class: "valeur ko" }, "✕ " + L.voix.introuvable)),
        absent ? null : el("div", { class: "lien-discret" }, lienProjet(L.voix.siteProjet))),
      titreCarte(L.voix.transcription),
      carteTranscription(d.voix, L, h.modele),
      details);
  }
  // Notifications Needs you : par appareil, permission demandée au clic sur l'interrupteur seulement
  const msgNotif = el("div", { class: "note-cote" });
  const notif = page("notif", titreCarte(L.notif.needsYou), carteReglages(interrupteur(L.notif.nom, L.notif.aide,
    masque(CLE_NOTIF), async (c) => {
      if (!c.checked) { ecrireStockage(CLE_NOTIF, "0"); msgNotif.textContent = ""; return; }
      if (!("Notification" in window)) { c.checked = false; msgNotif.textContent = L.notif.nonPrises; return; }
      let p = Notification.permission;
      try { if (p === "default") p = await Notification.requestPermission(); } catch (_) { p = "denied"; }
      if (p !== "granted") { c.checked = false; ecrireStockage(CLE_NOTIF, "0"); msgNotif.textContent = L.notif.refusee; return; }
      ecrireStockage(CLE_NOTIF, "1");
      msgNotif.textContent = L.notif.activee;
    }), msgNotif));
  // Affichage : sections de la liste et tri par défaut, par appareil
  const msgNom = el("div", { class: "msg-reglage", role: "status" });
  const affichage = page("affichage", titreCarte(L.affichage.sections), carteReglages(
    interrupteur(L.affichage.terminaux, L.affichage.terminauxAide, !masque(CLE_MASQUE_TERMINAUX), (c) => {
      ecrireStockage(CLE_MASQUE_TERMINAUX, c.checked ? "0" : "1"); rendreCote();
    })),
  carteReglages(ligneReglage(L.affichage.nom, L.affichage.nomAide, champNom(msgNom, L)), msgNom),
  carteLangue(L),
  carteReglages(ligneReglage(L.affichage.tri, L.affichage.triAide, segmente(L.affichage.tri,
    TRIS.map(([k, nom]) => [k, L.affichage.tris[k] || nom]), modeTri(), (k) => { ecrireStockage(CLE_TRI, k); rendreCote(); }))),
  titreCarte(L.affichage.raccourcis), carteRaccourcis());
  // Appareils : connecter un appareil (QR code + lien à usage unique, jamais le jeton), accès, session
  const se = (d && d.session) || {};
  const jours = typeof se.cookie_max_age_s === "number" ? L.appareils.jours(Math.round(se.cookie_max_age_s / 86400)) : "";
  const push = Array.isArray(se.appareils_push) ? se.appareils_push : null;
  const zoneAppairage = el("div", { class: "appairage" });
  const boutonAppairage = el("button", { type: "button", class: "action principal",
    onclick: () => connecterAppareil(zoneAppairage, boutonAppairage) }, L.appareils.connecter);
  const zoneSession = el("div", { class: "zone-session" });
  const demander = () => {
    const non = el("button", { type: "button", class: "action", onclick: () => { remplacer(zoneSession, ligneSession()); bouton().focus(); } },
      L.appareils.annuler);
    remplacer(zoneSession, el("div", { class: "confirmation", role: "alertdialog", ariaLabel: L.appareils.confirmer },
      el("div", { class: "intitule" }, L.appareils.confirmer),
      el("div", { class: "rangee" }, non, el("button", { type: "button", class: "action danger", onclick: deconnecter }, L.appareils.oui))));
    non.focus();
  };
  const ligneSession = () => ligneReglage(L.appareils.fermerSession, L.appareils.deconnexionAide,
    el("button", { type: "button", class: "action discret-danger", onclick: demander }, L.appareils.deconnexion));
  const bouton = () => zoneSession.querySelector(".discret-danger");
  remplacer(zoneSession, ligneSession());
  const appareils = page("appareils",
    el("div", { class: "carte-reglages vedette" }, ligneReglage(L.appareils.nouvel, L.appareils.connecterAide, boutonAppairage),
      zoneAppairage),
    titreCarte(L.appareils.acces),
    carteReglages(
      ligneReglage(L.appareils.push, push ? [...new Set(push)].join(", ") : "",
        valeurReglage(push ? (push.length ? String(push.length) : L.appareils.pushAucun) : "", L.appareils.pushOff)),
      ligneReglage(L.appareils.cookie, L.appareils.modeleSession, valeurReglage(jours, L.inconnu))),
    titreCarte(L.appareils.session),
    el("div", { class: "carte-reglages" }, zoneSession));
  // À propos : version servie, cache du service worker, rechargement
  const v = (d && d.version) || {};
  const apropos = page("apropos", carteReglages(
    ligneReglage(L.apropos.version, "", valeurReglage(versionCourte(v))),
    ligneReglage(L.apropos.commit, "", valeurReglage(v.commit, L.inconnu, "mono")),
    ligneReglage(L.apropos.cache, "", valeurReglage(v.cache, L.inconnu, "mono"))),
  carteReglages(ligneReglage(L.apropos.horsLigne, L.apropos.rechargerAide,
    el("button", { type: "button", class: "action", onclick: rechargerPage }, L.apropos.recharger))));
  return { voix, notif, affichage, appareils, apropos };
}

// Langue de la page : réglage serveur, partagé entre les appareils ; enregistrée, la page se recharge.
function carteLangue(L) {
  const msg = el("div", { class: "msg-reglage", role: "status" });
  const choix = segmente(L.affichage.langue, I18N.LANGUES.map((k) => [k, t("langue." + k)]), I18N.courante(), async (k) => {
    try {
      const r = await api("POST", "/api/reglages", { langue: k });
      msg.textContent = L.affichage.langueEnregistree;
      suivreLangue(r && r.langue);
    } catch (e) { msg.textContent = e.status === 401 ? "" : messageErreur(e); }
  });
  choix.classList.add("choix-langue");
  return carteReglages(ligneReglage(L.affichage.langue, L.affichage.langueAide, choix), msg);
}

// QR code du lien d'appairage, en SVG (encodeur embarqué : static/vendor/qrcode, aucun réseau).
// Fond clair et marge de 4 modules même en thème sombre : sinon les lecteurs le lisent mal.
function qrSvg(lien) {
  if (!window.HerdrQR) return null;
  const m = window.HerdrQR.matrice(lien), marge = 4, n = m.taille + 2 * marge;
  let d = "";
  for (let l = 0; l < m.taille; l++) for (let c = 0; c < m.taille; c++) {
    if (m.noir(l, c)) d += "M" + (c + marge) + " " + (l + marge) + "h1v1h-1z";
  }
  const s = svg("0 0 " + n + " " + n, "qr", ["rect", { width: String(n), height: String(n), fill: "#fff" }],
    ["path", { d, fill: "#000" }]);
  s.setAttribute("shape-rendering", "crispEdges");
  s.setAttribute("role", "img");
  s.setAttribute("aria-hidden", "false");
  s.setAttribute("aria-label", TXT_REGLAGES.appareils.qrNom);
  return s;
}

async function connecterAppareil(zone, bouton) {
  if (zone._fin) clearTimeout(zone._fin);
  bouton.disabled = true;
  const A = TXT_REGLAGES.appareils;
  remplacer(zone, el("div", { class: "note-cote" }, A.creation));
  let r;
  try {
    r = await api("POST", "/api/appairage", {});
  } catch (e) {
    bouton.disabled = false;
    remplacer(zone, e.status === 401 ? null : el("div", { class: "note-cote erreur" }, messageErreur(e)));
    return;
  }
  const lien = texte(r && r.lien), duree = r && typeof r.expire_s === "number" ? r.expire_s : 300;
  const fin = new Date(Date.now() + duree * 1000);
  let qr = null;
  try { qr = qrSvg(lien); } catch (_) { qr = null; }
  const copier = el("button", { type: "button", class: "action", onclick: async () => {
    try { await navigator.clipboard.writeText(lien); copier.textContent = A.copie; }
    catch (_) { copier.textContent = A.copieImpossible; }
  } }, A.copierLien);
  bouton.textContent = A.nouveau;
  bouton.disabled = false;
  remplacer(zone, qr || el("div", { class: "note-cote erreur" }, A.qrIndispo),
    el("div", { class: "lien-appairage" }, lien),
    el("div", { class: "note-cote" }, A.scanner(String(fin.getHours()).padStart(2, "0") + ":"
      + String(fin.getMinutes()).padStart(2, "0"))),
    el("div", { class: "rangee" }, copier));
  zone._fin = setTimeout(() => {
    if (zone.isConnected) remplacer(zone, el("div", { class: "note-cote" }, A.expire));
  }, duree * 1000);
}

async function rechargerPage() { // purge du cache du service worker, puis rechargement depuis le réseau
  try {
    if (window.caches) for (const k of await caches.keys()) await caches.delete(k);
    if ("serviceWorker" in navigator) for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
  } catch (_) { /* rien à purger : on recharge quand même */ }
  location.reload();
}

async function deconnecter() {
  try { await api("POST", "/api/logout", {}); } catch (_) { /* 401 : déjà dehors */ }
  fermerReglages();
  afficherConnexion();
}

// ---------- palette de commandes (Ctrl+Shift+P, modifiable) et Ctrl+L — brief palette, 2026-11-15 ----------
// Entrées, filtre et raccourcis : static/palette.js (pur). Ici : l'état passé à la palette, la boîte,
// le clavier et l'exécution des actions par les fonctions déjà là (menu « + », fermeture, réglages…).
// Par appareil : la combinaison de chaque action (« palette », « nettoyer » = Ctrl+L au terminal) et
// l'interception de « nettoyer » (désactivée : la combinaison va au navigateur comme avant).
const CLE_RACCOURCI = "herdr.raccourci-palette";
const CLE_RACCOURCI_NETTOYER = "herdr.raccourci-nettoyer";
const CLE_NETTOYER = "herdr.intercepter-nettoyer";
const autreAction = (action) => (action === "palette" ? "nettoyer" : "palette");
function comboStocke(action) { // la combinaison choisie sur cet appareil, sinon celle par défaut
  const c = action === "palette" ? lireStockage(CLE_RACCOURCI) : lireStockage(CLE_RACCOURCI_NETTOYER);
  return c && PALETTE.raccourciValide(c).ok ? c : PALETTE.DEFAUTS[action];
}
function stockerCombo(action, c) { // la valeur par défaut ne s'écrit pas : elle suivra un changement de défaut
  if (c === PALETTE.DEFAUTS[action]) {
    if (action === "palette") effacerStockage(CLE_RACCOURCI); else effacerStockage(CLE_RACCOURCI_NETTOYER);
  } else if (action === "palette") ecrireStockage(CLE_RACCOURCI, c);
  else ecrireStockage(CLE_RACCOURCI_NETTOYER, c);
}
const nettoyerActif = () => lireStockage(CLE_NETTOYER) !== "0";
const raccourciPalette = () => comboStocke("palette");
const combosClavier = () => ({ palette: comboStocke("palette"), nettoyer: nettoyerActif() ? comboStocke("nettoyer") : null });

function groupesCote() { // sections de la zone Agents à l'écran : { cle, titre, plie }
  return S.liste ? [...S.liste.querySelectorAll(".groupe-tete")].map((t) => ({ cle: t.dataset.cle,
    titre: texte(t.childNodes[0] && t.childNodes[0].textContent), plie: estPlie(t.dataset.cle) })) : [];
}

function contextePalette() {
  const v = S.vue && !S.vue.sous ? S.vue : null;
  const a = v ? trouver(v.addr) : null;
  const fam = famillesHc(S.agents);
  const urgence = (x) => URGENCE[etat(x.state)];
  const spA = a && texte(a.space) ? S.spaces.find((x) => x.name === a.space && texte(x.id)) : null;
  return {
    agents: S.agents.map((x) => ({ addr: x.address, nom: nomAgent(x), role: texte(x.role), etat: ETATS.includes(x.state) ? libelle(x.state) : "", // un terminal : son rôle le dit déjà
      modele: (familleModele(x.model) || {}).famille || "", space: texte(x.space), terminal: x.role === "terminal" })),
    spaces: listeSpaces().map((sp) => ({ nom: sp.name, n: sp.agents.length,
      addr: ([...sp.agents].sort((x, y) => urgence(x) - urgence(y))[0] || {}).address })),
    ouvert: a ? { addr: a.address, nom: nomAgent(a), claude: estClaude(a), bloque: a.state === "blocked",
      epinglable: !fam.estEnfant(a), epingle: !!a.pinned } : null,
    commandes: v ? v.commandes : [],
    creer: { spaces: (spA ? [spA] : S.spaces.filter((x) => texte(x.id))).map((x) => ({ id: x.id, nom: x.name })),
      connus: (S.connus || []).filter((c) => c.installed).map((c) => ({ id: c.id, nom: c.name })) },
    rubriques: RUBRIQUES.map((k) => [k, TXT_REGLAGES.rub[k]]), tri: modeTri(), tris: TRIS, groupes: groupesCote(),
  };
}

function fermerPalette() {
  const p = S.palette;
  if (!p) return;
  S.palette = null;
  p.racine.remove();
  if (p.retour && p.retour.isConnected && p.retour.focus) p.retour.focus();
}

function ouvrirPalette() {
  if (S.palette) { fermerPalette(); return; }
  if (S.confirmation) return; // une confirmation de fermeture attend sa réponse
  fermerReglages();
  const champ = el("input", { type: "text", class: "palette-champ", autocomplete: "off", spellcheck: false,
    placeholder: t("palette.placeholder"), ariaLabel: t("palette.rechercher") });
  champ.setAttribute("role", "combobox");
  champ.setAttribute("aria-expanded", "true");
  champ.setAttribute("aria-autocomplete", "list");
  champ.setAttribute("aria-controls", "palette-liste");
  const liste = el("div", { class: "palette-liste", id: "palette-liste", role: "listbox", ariaLabel: t("palette.commandes") });
  const note = el("div", { class: "palette-note", role: "status" });
  const kbd = (t) => el("kbd", {}, t);
  const pied = el("div", { class: "palette-pied" }, el("span", {}, kbd("↑"), kbd("↓"), t("palette.naviguer")),
    el("span", {}, kbd(PALETTE.libelle("enter")), t("palette.executer")), el("span", {}, kbd(PALETTE.libelle("escape")), t("palette.fermer")),
    el("span", { class: "palette-raccourci" }, kbd(PALETTE.libelle(raccourciPalette()))));
  // téléphone : plein écran, ni Échap ni voile à toucher — un bouton ferme (caché sur PC par la CSS)
  const fermer = el("button", { type: "button", class: "palette-fermer", title: t("commun.fermer"), ariaLabel: t("palette.fermerAria"),
    onclick: () => fermerPalette() });
  fermer.append(iconeCroix());
  const boite = el("div", { class: "boite palette", role: "dialog", ariaModal: "true", ariaLabel: t("palette.nom") },
    el("div", { class: "palette-tete" }, champ, fermer), liste, note, pied);
  const racine = el("div", { class: "voile voile-palette" }, boite);
  const p = S.palette = { racine, champ, liste, note, sel: 0, trouves: [],
    retour: document.activeElement !== document.body ? document.activeElement : null };
  racine.addEventListener("click", (ev) => { if (ev.target === racine) fermerPalette(); });
  // aria-modal : Tab ne sort pas de la boîte — il boucle entre le champ et ×, ou reste au champ (PC : × caché)
  boite.addEventListener("keydown", (ev) => {
    if (ev.key !== "Tab") return;
    ev.preventDefault();
    const cibles = [champ, fermer].filter((e) => !e.getClientRects || e.getClientRects().length > 0);
    const i = cibles.indexOf(document.activeElement);
    cibles[i < 0 ? 0 : (i + (ev.shiftKey ? cibles.length - 1 : 1)) % cibles.length].focus();
  });
  champ.addEventListener("input", () => { p.sel = 0; majPalette(); });
  champ.addEventListener("keydown", (ev) => {
    const n = p.trouves.length;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (n) { p.sel = (p.sel + (ev.key === "ArrowDown" ? 1 : n - 1)) % n; majPalette(); }
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      executerPalette(p.trouves[p.sel]);
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      fermerPalette();
    }
  });
  document.body.append(racine);
  champ.focus();
  majPalette();
  if (!S.connus) chargerConnus().then(() => { if (S.palette === p) majPalette(); }); // « Créer » s'en complète
}

function majPalette() {
  const p = S.palette;
  if (!p) return;
  p.trouves = PALETTE.filtrer(PALETTE.construire(contextePalette()), p.champ.value);
  p.sel = Math.min(p.sel, Math.max(0, p.trouves.length - 1));
  const kids = [];
  let section = null;
  p.trouves.forEach((e, i) => {
    if (e.section !== section) {
      section = e.section;
      kids.push(el("div", { class: "palette-section", role: "presentation" }, section));
    }
    const o = el("div", { class: "palette-entree" + (i === p.sel ? " sel" : ""), id: "palette-" + i, role: "option" },
      el("span", { class: "palette-titre" }, e.titre), texte(e.detail) ? el("span", { class: "palette-detail" }, e.detail) : null);
    o.setAttribute("aria-selected", String(i === p.sel));
    if (e.inactif) o.setAttribute("aria-disabled", "true");
    o.addEventListener("pointerdown", (ev) => ev.preventDefault()); // le focus reste au champ
    o.addEventListener("click", () => { p.sel = i; executerPalette(e); });
    kids.push(o);
  });
  if (!kids.length) kids.push(el("div", { class: "palette-vide" }, t("palette.aucune")));
  p.liste.replaceChildren(...kids);
  if (p.trouves.length) p.champ.setAttribute("aria-activedescendant", "palette-" + p.sel);
  else p.champ.removeAttribute("aria-activedescendant");
  const sel = p.liste.querySelector(".palette-entree.sel");
  if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
}

async function executerPalette(e) {
  const p = S.palette;
  if (!e || !p) return;
  if (e.inactif) { p.note.textContent = e.detail; return; } // touche à un agent bloqué : refusée ici aussi
  const x = e.action;
  if (x.type === "prefixe") { p.champ.value = x.texte; p.sel = 0; majPalette(); return; }
  fermerPalette();
  const a = x.addr ? trouver(x.addr) : null;
  switch (x.type) {
    case "aller": location.hash = hashAgent(x.addr); break;
    case "touche": envoyerTouche(x.addr, x.key, x.nom); break;
    case "commande": if (S.vue && S.vue.addr === x.addr && S.vue.champ) choisirCommande(S.vue, x.cmd); break;
    case "epingler": if (a) epingler(a); break;
    case "fermer": demanderFermeture(x.addr); break;
    case "ouvrir": {
      const ici = S.vue && trouver(S.vue.addr);
      const dans = ici && ici.space === x.space.nom && texte(ici.cwd) ? ici
        : S.agents.find((y) => y.space === x.space.nom && texte(y.cwd));
      try { await ouvrirDansSpace({ id: x.space.id, name: x.space.nom }, x.kind, x.model, dans ? dans.cwd : null); } catch (err) {
        if (err.status !== 401) { S.erreurCote = messageErreur(err); rendreCote(); statut(S.erreurCote, true); }
      }
      break;
    }
    case "space": S.formSpace = true; S.erreurCote = null; rendreCote(true); if (S.vue) location.hash = "#/"; break;
    case "reglages":
      if (x.rubrique) ecrireStockage(CLE_RUBRIQUE, x.rubrique);
      await ouvrirReglages();
      if (x.rubrique && S.reglages) {
        const o = [...S.reglages.boite.querySelectorAll(".onglet-reglages")].find((b) => b.dataset.rubrique === x.rubrique);
        if (o) o.click(); // téléphone : la page de la rubrique, pas la liste
      }
      break;
    case "tri": ecrireStockage(CLE_TRI, x.mode); rendreCote(); break;
    case "pli": basculerPli(x.cle); break;
    case "plis": {
      const pl = plis();
      for (const g of groupesCote()) { if (x.plie) pl.add(g.cle); else pl.delete(g.cle); }
      ecrireStockage(CLE_PLIS, JSON.stringify([...pl]));
      rendreCote();
      break;
    }
    case "recharger": rechargerPage(); break;
    default: break;
  }
}

// Une touche nommée au terminal (liste blanche du serveur) ; refusée ici quand l'agent attend un choix.
async function envoyerTouche(addr, key, nom) {
  const a = trouver(addr);
  if (a && a.state === "blocked") { statut(t("erreur.attendChoixCarte"), true); chargerChoix(); return; }
  try {
    await api("POST", urlAgent(addr, "keys"), { key });
    const fait = t("touche.envoyee", { nom });
    statut(fait);
    setTimeout(() => { if (S.vue && S.vue.statut.textContent === fait) statut(""); }, 2000);
  } catch (e) {
    if (e.status === 401 || sortieAgent(e)) return;
    if (e.code === "bloque") { statut(t("erreur.attendChoixCarte"), true); chargerChoix(); }
    else if (e.code === "occupe") statut(t("erreur.terminalOccupe"), true);
    else statut(messageErreur(e), true);
  }
}

function focusPourCtrlL() { // « aucun » | « message » | « terminal » | « autre »
  const f = document.activeElement;
  if (!f || f === document.body) return "aucun";
  if (S.vue && f === S.vue.champ) return "message";
  if (f.closest && f.closest(".ecran")) return "terminal"; // l'écran xterm, en lecture seule
  return /^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName) || f.isContentEditable ? "autre" : "aucun";
}

// Phase de capture : avant xterm et avant le champ. Pendant la saisie d'un raccourci (réglages), rien.
document.addEventListener("keydown", (ev) => {
  if (S.captureRaccourci) return;
  const r = PALETTE.raccourci(ev, combosClavier());
  if (r === "palette") {
    ev.preventDefault();
    ev.stopPropagation();
    ouvrirPalette(); // ouverte : la referme
  } else if (r === "nettoyer" && PALETTE.ctrlLPermis({ vue: !!S.vue, sous: !!(S.vue && S.vue.sous),
    modal: !!(S.palette || S.confirmation || S.reglages), focus: focusPourCtrlL(),
    champRempli: !!(S.vue && S.vue.champ && S.vue.champ.value.trim()) })) {
    ev.preventDefault();
    ev.stopPropagation();
    envoyerTouche(S.vue.addr, "ctrl+l", "Ctrl+L");
  }
}, true);

function boutonPalette() {
  const b = el("button", { type: "button", class: "bouton-palette", title: t("palette.nom"),
    ariaLabel: t("palette.nom"), ariaHasPopup: "dialog", onclick: ouvrirPalette });
  b.append(svg("0 0 24 24", "ico", ["path", { d: "M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3z",
    fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }]));
  return b;
}

// Réglages › Affichage : la combinaison d'une action (« palette », « nettoyer »), capturée au clavier,
// par appareil. Refus : combinaisons prises par le navigateur, sans Ctrl/Alt/Méta, celle de l'autre action.
function ligneRaccourci(action, intitule, aide) {
  const L = TXT_REGLAGES.affichage;
  const kbd = el("kbd", { class: "raccourci-actuel" });
  const msg = el("div", { class: "note-cote" });
  const modifier = el("button", { type: "button", class: "action" }, L.modifier);
  const defaut = el("button", { type: "button", class: "action" }, L.defaut);
  const alerte = el("div", { class: "note-cote alerte-raccourci" });
  const maj = () => {
    kbd.textContent = PALETTE.libelle(comboStocke(action));
    defaut.disabled = comboStocke(action) === PALETTE.DEFAUTS[action];
    alerte.textContent = PALETTE.avertissement(comboStocke(action), navigator.userAgent);
  };
  const finir = (t) => { S.captureRaccourci = false; modifier.textContent = L.modifier; modifier.classList.remove("capture");
    msg.textContent = texte(t); maj(); };
  const autre = () => ({ action: autreAction(action), combo: comboStocke(autreAction(action)) });
  modifier.addEventListener("click", () => {
    if (S.captureRaccourci) { finir(L.annule); return; }
    S.captureRaccourci = true;
    modifier.textContent = L.capture;
    modifier.classList.add("capture");
    msg.textContent = L.captureAide;
    modifier.focus();
  });
  modifier.addEventListener("keydown", (ev) => {
    if (!S.captureRaccourci) return;
    ev.preventDefault();
    ev.stopPropagation(); // Échap ne referme pas les réglages pendant la capture
    if (ev.key === "Escape") { finir(L.annule); return; }
    const c = PALETTE.combinaison(ev);
    if (!c) return; // modificateur seul : la touche n'est pas encore venue
    const v = PALETTE.raccourciValide(c, autre());
    if (!v.ok) { msg.textContent = PALETTE.libelle(c) + " : " + v.raison; return; }
    stockerCombo(action, c);
    finir(L.enregistre(PALETTE.libelle(c)));
  });
  modifier.addEventListener("blur", () => { if (S.captureRaccourci) finir(L.annule); });
  defaut.addEventListener("click", () => {
    const c = PALETTE.DEFAUTS[action];
    const v = PALETTE.raccourciValide(c, autre());
    if (!v.ok) { msg.textContent = PALETTE.libelle(c) + " : " + v.raison; return; }
    stockerCombo(action, c);
    finir(L.enregistre(PALETTE.libelle(c)));
  });
  maj();
  const ligne = ligneReglage(intitule, aide, el("div", { class: "rangee-raccourci" }, kbd, modifier, defaut));
  ligne.dataset.raccourci = action;
  return [ligne, alerte, msg];
}

function carteRaccourcis() {
  const L = TXT_REGLAGES.affichage;
  return carteReglages(...ligneRaccourci("palette", L.palette, L.paletteAide),
    interrupteur(L.nettoyer, L.nettoyerAide, nettoyerActif(), (c) => ecrireStockage(CLE_NETTOYER, c.checked ? "1" : "0")),
    ...ligneRaccourci("nettoyer", L.nettoyerCombo, L.nettoyerComboAide));
}

// ---------- routage ----------
function route() {
  if (document.querySelector(".connexion")) return;
  squelette();
  const m = location.hash.match(/^#\/a\/([^/]+)(?:\/s\/([^/]+))?$/);
  fermerVue();
  if (m) {
    document.getElementById("bandeau-alerte").hidden = true;
    app.dataset.vue = "agent";
    ouvrirAgent(decodeURIComponent(m[1]), m[2] ? decodeURIComponent(m[2]) : null);
  } else {
    app.dataset.vue = "liste";
    rendreAccueil();
  }
  rendreCote();
  rendreBarre();
}

// les durées « depuis » vieillissent sans événement
setInterval(() => {
  if (!S.cote || document.hidden || document.querySelector(".connexion")) return;
  rendreBarre();
  rendreCote();
  if (S.vue && !S.vue.sous) {
    const a = trouver(S.vue.addr);
    const d = S.vue.meta.querySelector(".depuis");
    if (a && d) d.textContent = depuis(a.since_ms);
  }
}, 15000);

// ---------- notifications Web Push ----------
function cleVersOctets(b64url) {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64url.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

async function abonner(reg, cle) {
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: cleVersOctets(cle) });
  await api("POST", "/api/push/subscribe", sub.toJSON()); // idempotent : dédoublonné par endpoint
}

async function initPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return;
  try {
    const { key } = await api("GET", "/api/push/key"); // 404 : push désactivé côté serveur
    const reg = await navigator.serviceWorker.ready;
    if (Notification.permission === "granted") return await abonner(reg, key);
    if (Notification.permission !== "default") return;
    const bouton = el("button", { class: "bandeau alerte", type: "button" }, t("bandeau.notifs"));
    bouton.addEventListener("click", async () => { // la permission exige un geste de l'utilisateur
      try {
        if ((await Notification.requestPermission()) === "granted") await abonner(reg, key);
      } catch (_) { /* refus ou échec : le bouton disparaît, rien d'autre */ }
      bouton.remove();
    });
    document.getElementById("bandeaux").append(bouton);
  } catch (_) { /* pas de push : la page fonctionne sans */ }
}

window.addEventListener("hashchange", route);
// mise à jour : une appli installée restée ouverte ne se recharge jamais et gardait l'ancien code
// (2026-11-14, badge de contexte absent sur le téléphone). Au retour sur la page, on demande au
// navigateur de relire sw.js ; un nouveau service worker prend la main, et la page se recharge —
// tout de suite si le champ est vide, sinon après le prochain envoi, pour ne pas perdre un brouillon.
if ("serviceWorker" in navigator) {
  const dejaControlee = !!navigator.serviceWorker.controller; // 1re installation : pas de rechargement
  navigator.serviceWorker.register("/sw.js").then((reg) => {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") reg.update().catch(() => {});
    });
  }).catch(() => {});
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!dejaControlee || S.rechargement) return;
    S.rechargement = true;
    const brouillon = S.vue && S.vue.champ && S.vue.champ.value.trim();
    if (!brouillon) location.reload();
  });
}
function demarrer(delai) {
  api("GET", "/api/agents").then(async (liste) => {
    S.agents = Array.isArray(liste) ? liste : [];
    await chargerSpaces();
    route(); connecterFlux(); initPush(); chargerBarre(); chargerReglages();
  }).catch((e) => {
    if (e.status === 401) return; // l'écran de jeton est déjà affiché
    const msg = el("div", { class: "vide" }, messageErreur(e) + t("erreur.nouvelEssai", { n: delai }));
    app.replaceChildren(el("div", { class: "attente" }, el("h1", {}, TITRE), msg));
    setTimeout(() => demarrer(Math.min(delai * 2, 30)), delai * 1000);
  });
}
demarrer(2);
