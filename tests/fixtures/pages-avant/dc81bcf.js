"use strict";
// herdr — page de pilotage. Tout texte venu du serveur passe par textContent (jamais de HTML) ;
// le Markdown de l'agent est rendu par md.js, qui construit des nœuds DOM.
// Le jeton n'est jamais conservé ici : seul le cookie HttpOnly posé par le serveur le porte.
// Un champ absent ou nul cache son élément : la page n'affiche jamais « null », « undefined », « NaN ».

const ETATS = ["blocked", "working", "done", "idle", "error"];
const LIBELLES = { blocked: "Needs you", working: "working", done: "done", idle: "idle",
  error: "error", terminal: "terminal" };
const GROUPES = [["blocked", "Needs you"], ["working", "Working"], ["done", "Done"],
  ["idle", "Idle"], ["error", "Error"], ["terminal", "Terminaux"]];
const POIDS = { blocked: 0, working: 1, error: 2, done: 3, idle: 4, terminal: 5 };
const TEINTES = ["#94e2d5", "#89b4fa", "#cba6f7", "#fab387", "#a6e3a1", "#f5c2e7", "#f9e2af", "#89dceb"];
const MAX_PHOTO = 8 * 1024 * 1024;
const MAX_VOIX = 12 * 1024 * 1024;
const VOCAL_MIN_MS = 500;
const BAS_PX = 40;
const SOUS_POLL_MS = 3000;
const NOUVEL_AGENT_MS = 30000;
const APPUI_LONG_MS = 550;
const POLICE_MIN = 9;
const POLICE_MAX = 14;
const CLE_COTE = "herdr.cote";
const TIRET = "—";
const ERREURS = {
  perime: "la demande a changé",
  occupe: "un envoi est déjà en cours",
  vide: "rien entendu",
  introuvable: "introuvable",
  "herdr-app": "herdr-app a refusé",
  "hc kill": "hc kill a échoué",
  "hc rm": "hc rm a échoué",
  confirmation: "confirmation refusée",
};
const TITRE = "herdr";
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
const remplacer = (n, ...kids) => n.replaceChildren(...kids.filter((k) => k != null && k !== ""));
const texte = (v) => (v == null || (typeof v === "number" && !isFinite(v)) ? "" : String(v));
const etat = (s) => (ETATS.includes(s) ? s : "terminal");
const libelle = (s) => LIBELLES[etat(s)];
const nomAgent = (a) => texte(a.name) || texte(a.title) || texte(a.address);
const trouver = (addr) => S.agents.find((a) => a.address === addr);
const estClaude = (a) => a.program === "claude-code" || a.program === "claude";
const urlAgent = (addr, fin) => "/api/agents/" + encodeURIComponent(addr) + "/" + fin;
const hashAgent = (addr, sous) => "#/a/" + encodeURIComponent(addr)
  + (sous ? "/s/" + encodeURIComponent(sous) : "");
const lireStockage = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
const ecrireStockage = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* privé */ } };

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

const ETOILE = "M12 2.5l1.6 6.1 5.3-3.4-3.4 5.3 6.1 1.6-6.1 1.6 3.4 5.3-5.3-3.4L12 21.5l-1.6-6.1-5.3 3.4 3.4-5.3L2.5 12l6.1-1.6-3.4-5.3 5.3 3.4z";
function iconeRole(a) {
  // rôle (template hc) ou activité en cours ; animée seulement pendant « working »
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
  } else if (role === "shell") {
    i = svg("0 0 24 24", "role shell" + anime,
      ["path", { d: "M4 7l5 5-5 5", fill: "none", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" }],
      ["rect", { class: "curseur", x: "11", y: "16", width: "9", height: "2.6", rx: "1", fill: "currentColor" }]);
  } else if (role === "claude") {
    i = svg("0 0 24 24", "role claude" + anime, ["path", { d: ETOILE, fill: "#D97757" }]);
  } else return null; // un terminal sans état garde son rond gris, sans icône
  const t = document.createElementNS(SVG_NS, "title");
  t.textContent = role;
  i.prepend(t);
  return i;
}

function iconeSous(s) { // rôle déduit du titre (role_infere) : marteau ou loupe, sinon rien
  return s && (s.role === "maker" || s.role === "checker") ? iconeRole({ role: s.role, state: s.state }) : null;
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

function rond(s) {
  const e = etat(s);
  return el("span", { class: "rond " + e, title: LIBELLES[e], role: "img", ariaLabel: LIBELLES[e] });
}

function badgeEtat(s) { return el("span", { class: "etat " + etat(s) }, libelle(s)); }

function familleModele(m) {
  const f = /(opus|sonnet|haiku)[-_ ]?(\d+)?(?:[-.](\d+))?/i.exec(texte(m));
  return f ? { famille: f[1].toLowerCase(), version: f[2] ? f[2] + (f[3] ? "." + f[3] : "") : "" } : null;
}

const FMT_HEURE = (() => {
  try { return new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" }); }
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

function depuis(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms <= 0) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "depuis " + Math.round(s) + " s";
  if (s < 3600) return "depuis " + Math.floor(s / 60) + " min";
  if (s < 86400) return "depuis " + Math.floor(s / 3600) + " h";
  return "depuis " + Math.floor(s / 86400) + " j";
}

function branche(b) { return texte(b) ? el("span", { class: "branche", title: "branche git" }, "⎇ " + b) : null; }

function teinte(nom) {
  let h = 0;
  for (const c of nom) h = (h * 31 + c.codePointAt(0)) >>> 0;
  return TEINTES[h % TEINTES.length];
}

function pire(liste) { // statut agrégé : le plus urgent
  return liste.map((a) => etat(a.state)).sort((x, y) => POIDS[x] - POIDS[y])[0] || "terminal";
}

const joindre = (...parts) => parts.map(texte).filter(Boolean).join(" · ");

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
  if (!r.ok) throw { status: r.status, code: (data && data.erreur) || "inconnue", detail: (data && data.detail) || "" };
  return data;
}

function messageErreur(e) {
  if (e.status === 404) return "agent disparu";
  if (e.status === 503) return "déconnecté de herdr-core";
  if (e.status === 504) return "échec après 90 s";
  if (e.status === 502) return (ERREURS[e.code] || "erreur herdr-core") + (e.detail ? " : " + e.detail : "");
  if (e.status === 413) return "fichier trop gros";
  if (e.status === 415) return "type de fichier refusé";
  if (e.status === 0) return "réseau injoignable";
  return ERREURS[e.code] || texte(e.detail) || "erreur " + texte(e.status);
}

// ---------- connexion (jeton) ----------
function afficherConnexion() {
  if (S.es) { S.es.close(); S.es = null; }
  fermerVue();
  const champ = el("input", { type: "password", placeholder: "Jeton", autocomplete: "current-password" });
  const msg = el("div", { class: "note" });
  const f = el("form", { class: "connexion" },
    el("h1", {}, el("img", { class: "logo-img", src: "/static/herdr.svg", alt: "" }), "herdr"),
    champ, el("button", { type: "submit" }, "Entrer"), msg);
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
      msg.textContent = r.status === 401 ? "jeton faux" : "erreur " + r.status;
    } catch (_) { msg.textContent = "réseau injoignable"; }
  });
  app.replaceChildren(f);
  S.vue = null;
}

function squelette() { // barre latérale + panneau du fil, recréés après un écran plein
  if (!S.cote) {
    S.liste = el("div", { class: "cote-defile" });
    S.barre = el("div", { class: "barre-etat" });
    S.cote = el("aside", { id: "cote" }, S.liste, S.barre);
    S.panneau = el("section", { id: "vue" });
    appliquerCote(lireStockage(CLE_COTE) === "replie");
  }
  if (S.cote.parentNode !== app) app.replaceChildren(S.cote, S.panneau);
}

// ---------- panneau de gauche repliable (PC) ----------
function appliquerCote(replie) { app.classList.toggle("replie", !!replie); }
function basculerCote() {
  const replie = !app.classList.contains("replie");
  appliquerCote(replie);
  ecrireStockage(CLE_COTE, replie ? "replie" : "ouvert");
}
function boutonCote() {
  const b = el("button", { type: "button", class: "bascule-cote", title: "Ouvrir ou fermer la liste",
    ariaLabel: "Ouvrir ou fermer la liste", onclick: basculerCote });
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

function annoncer(a) {
  const b = document.getElementById("bandeau-alerte");
  b.textContent = "Needs you : " + nomAgent(a) + (texte(a.msg) ? " — " + a.msg : "");
  b.dataset.addr = a.address;
  b.hidden = false;
  if (navigator.vibrate) navigator.vibrate(200);
}
document.getElementById("bandeau-alerte").addEventListener("click", (ev) => {
  const b = ev.currentTarget;
  b.hidden = true;
  location.hash = hashAgent(b.dataset.addr);
});

// ---------- barre d'état : cpu, mémoire, quota, modèles, heure ----------
function decompteModeles(m) {
  if (!m || typeof m !== "object") return "";
  return Object.entries(m).filter(([, n]) => typeof n === "number" && n > 0)
    .sort((a, b) => b[1] - a[1]).map(([f, n]) => f + " " + n).join(" · ");
}

function rendreBarre() {
  if (!S.barre) return;
  const v = S.valsBarre || {};
  const seg = (classe, val) => el("span", { class: "seg " + classe }, texte(val) || TIRET);
  const h = new Date();
  const heure = String(h.getHours()).padStart(2, "0") + ":" + String(h.getMinutes()).padStart(2, "0");
  const modeles = decompteModeles(v.models);
  remplacer(S.barre, seg("cpu", v.cpu), seg("mem", v.mem), seg("quota", v.quota),
    modeles ? el("span", { class: "seg modeles", title: "agents et sous-agents actifs par modèle" }, modeles) : null,
    el("span", { class: "seg heure" }, heure));
}

async function chargerBarre() {
  try { S.valsBarre = await api("GET", "/api/statusbar"); } catch (_) { S.valsBarre = null; }
  rendreBarre();
}

// ---------- barre latérale : spaces et agents ----------
function ligneAgent(a) {
  const v = S.vue;
  const actif = v && v.addr === a.address;
  const lieu = [texte(a.space), texte(a.tab)].filter(Boolean).join(" › ");
  const lien = el("a", { class: "agent " + etat(a.state) + (actif && !v.sous ? " actif" : ""),
    href: hashAgent(a.address) },
    el("div", { class: "l1" }, rond(a.state), iconeRole(a), el("span", { class: "nom" }, nomAgent(a)),
      badgeModele(a.model), el("span", { class: "depuis" }, depuis(a.since_ms))));
  if (texte(a.dir) || texte(a.branch)) {
    lien.append(el("div", { class: "l2" }, texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null,
      branche(a.branch)));
  }
  const l3 = joindre(a.machine, lieu);
  if (l3) lien.append(el("div", { class: "l3" }, l3));
  if (a.state === "blocked" && texte(a.msg)) lien.append(el("div", { class: "msg" }, a.msg));
  const fermer = el("button", { type: "button", class: "fermer-carte", title: "Fermer…",
    ariaLabel: "Fermer " + nomAgent(a), onclick: (ev) => { ev.preventDefault(); demanderFermeture(a.address); } });
  fermer.append(iconeCroix());
  const bloc = el("div", { class: "bloc-agent" }, lien, fermer);
  // clic droit (PC) ou appui long (téléphone) : la même confirmation
  lien.addEventListener("contextmenu", (ev) => { ev.preventDefault(); demanderFermeture(a.address); });
  let appui = 0;
  lien.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType !== "touch") return;
    appui = setTimeout(() => { appui = -1; demanderFermeture(a.address); }, APPUI_LONG_MS);
  });
  const lacher = () => { if (appui > 0) clearTimeout(appui); if (appui !== -1) appui = 0; };
  for (const t of ["pointerup", "pointercancel", "pointerleave", "pointermove"]) lien.addEventListener(t, lacher);
  lien.addEventListener("click", (ev) => { if (appui === -1) { ev.preventDefault(); appui = 0; } });
  // sous-agents : seulement ceux qui tournent, comme le mod agents de herdr-app
  const subs = (Array.isArray(a.subagents) ? a.subagents : []).filter((s) => s && !["done", "idle"].includes(s.state));
  subs.forEach((s, i) => {
    bloc.append(el("a", { class: "sous-agent" + (actif && v.sous === s.id ? " actif" : ""),
      href: hashAgent(a.address, s.id) },
      el("span", { class: "arbre" }, i === subs.length - 1 ? "└" : "├"), rond(s.state), iconeSous(s),
      el("span", { class: "nom" }, texte(s.title) || "sous-agent " + texte(s.id)), badgeModele(s.model)));
  });
  return bloc;
}

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
  const nom = el("input", { type: "text", placeholder: "Nom du space", maxLength: 64, required: true });
  const dossier = el("input", { type: "text", placeholder: "Dossier (/home/user/…)", required: true });
  dossier.setAttribute("list", "dossiers-connus");
  if (dossiers.length) dossier.value = dossiers[0];
  const msg = el("div", { class: "note-cote" });
  const f = el("form", { class: "form-space" }, nom, dossier, liste,
    el("div", { class: "rangee" }, el("button", { type: "submit", class: "action principal" }, "Créer"),
      el("button", { type: "button", class: "action", onclick: () => fini(null) }, "Annuler")),
    msg);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg.textContent = "création…";
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

async function ouvrirDansSpace(sp, kind, model) { // rejette avec l'erreur du serveur
  if (!sp || !sp.id) throw { status: 400, code: "", detail: "aucun space choisi" };
  const corps = kind === "claude" ? { kind, model: model || "default" } : { kind };
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
  const bouton = el("button", { type: "button", class: "bouton-plus", title: "Nouveau terminal ou agent",
    ariaLabel: "Nouveau terminal ou agent", ariaHasPopup: "menu" });
  bouton.append(svg("0 0 24 24", "ico", ["path", { d: "M12 5v14M5 12h14", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round" }]));
  const menu = el("div", { class: "menu-nouveau", role: "menu", hidden: true });
  const fermer = () => { menu.hidden = true; menu.replaceChildren(); };
  const remplir = () => { // construit UNE fois par ouverture : aucune mise à jour ne le refait (I2)
    const a = cible();
    const spA = a && texte(a.space) ? S.spaces.find((x) => x.name === a.space && texte(x.id)) : null;
    const avecId = S.spaces.filter((x) => texte(x.id));
    const msg = el("div", { class: "note-cote" });
    let choix = null;
    const kids = [];
    if (spA) kids.push(el("div", { class: "titre-menu" }, "Dans " + spA.name));
    else {
      choix = el("select", { class: "choix-space", ariaLabel: "Space" },
        ...avecId.map((x) => el("option", { value: x.id }, x.name)));
      kids.push(el("div", { class: "titre-menu" }, "Space"), avecId.length ? choix
        : el("div", { class: "note-cote" }, "Aucun space : en créer un ci-dessous"));
    }
    const vise = () => spA || avecId.find((x) => x.id === (choix && choix.value)) || avecId[0] || null;
    const lancer = async (kind, model) => {
      msg.textContent = "ouverture…";
      try { await ouvrirDansSpace(vise(), kind, model); fermer(); } catch (e) {
        if (e.status !== 401) { msg.className = "note-cote erreur"; msg.textContent = messageErreur(e); }
      }
    };
    kids.push(ligneMenu(el("span", { class: "ico-texte" }, ">_"), "Terminal", "", () => lancer("terminal")));
    kids.push(el("div", { class: "titre-menu" }, "Start an agent"));
    const connus = Array.isArray(S.connus) ? S.connus : [];
    if (!connus.length) kids.push(el("div", { class: "note-cote" }, S.connus ? "Aucun agent connu" : "recherche des agents…"));
    let rang = 0;
    for (const c of connus) {
      const ok = !!c.installed;
      if (ok) rang++;
      const modele = c.id === "claude" && ok ? el("select", { class: "choix-modele", ariaLabel: "Modèle" },
        el("option", { value: "default" }, "défaut"), el("option", { value: "opus" }, "opus"),
        el("option", { value: "sonnet" }, "sonnet")) : null;
      const ligne = ligneMenu(iconeAgentConnu(c.id, c.name), c.name, ok ? String(rang) : "not installed",
        ok ? () => lancer(c.id, modele ? modele.value : undefined) : null, !ok);
      if (!ok) ligne.classList.add("absent");
      kids.push(ligne);
      if (modele) kids.push(el("div", { class: "sous-ligne" }, el("span", {}, "Modèle"), modele));
    }
    kids.push(el("div", { class: "sep" }));
    kids.push(ligneMenu(el("span", { class: "ico-texte" }, "+"), "New space", "", () => {
      remplacer(menu, el("div", { class: "titre-menu" }, "New space"), formulaireSpace((cree) => {
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
  const e = etat(a.state);
  const msg = el("div", { class: "note-cote" });
  const annuler = el("button", { type: "button", class: "action", onclick: fermerConfirmation }, "Annuler");
  const ok = el("button", { type: "button", class: "action danger" }, "Fermer");
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    msg.className = "note-cote";
    msg.textContent = "fermeture…";
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
    ariaLabel: "Fermer " + nomAgent(a) },
  el("div", { class: "boite-titre" }, "Fermer « " + nomAgent(a) + " » ?"),
  el("div", { class: "boite-detail" }, joindre(a.dir, libelle(a.state)) || libelle(a.state)),
  e === "working" || e === "blocked"
    ? el("div", { class: "alerte-rouge" }, e === "working"
      ? "Il travaille encore : son tour en cours sera perdu."
      : "Il attend ta réponse : son tour en cours sera perdu.") : null,
  el("div", { class: "note-cote" }, "Le programme est arrêté (hc kill) puis le terminal retiré (hc rm)."),
  el("div", { class: "rangee" }, annuler, ok), msg);
  S.confirmation = el("div", { class: "voile" }, boite);
  S.confirmation.addEventListener("click", (ev) => { if (ev.target === S.confirmation) fermerConfirmation(); });
  S.confirmation.addEventListener("keydown", (ev) => { if (ev.key === "Escape") fermerConfirmation(); });
  document.body.append(S.confirmation);
  annuler.focus(); // le geste par défaut est l'annulation
}

function sectionSpaces() { // une ligne par space, statut agrégé, ses onglets avec leur compte
  const kids = [el("div", { class: "section-titre" }, "Spaces",
    boutonPlus("Nouveau space", () => { S.formSpace = !S.formSpace; S.erreurCote = null; rendreCote(true); }))];
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
      kids.push(el("div", { class: "onglet" }, el("span", { class: "nom" }, "› " + tb),
        rond(pire(liste)), el("span", { class: "n" }, String(liste.length))));
    }
  }
  return kids;
}

function sectionAgents() { // groupés par statut comme herdr-app : « Needs you », « Working », puis le reste
  const kids = [el("div", { class: "section-titre" }, "Agents")];
  if (!S.agents.length) kids.push(el("div", { class: "vide" }, "Aucun agent"));
  for (const [cle, titre] of GROUPES) {
    const liste = S.agents.filter((a) => etat(a.state) === cle);
    if (!liste.length) continue;
    kids.push(el("div", { class: "groupe-tete " + cle }, titre, el("span", { class: "n" }, String(liste.length))));
    for (const a of liste) kids.push(ligneAgent(a));
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
        el("span", { class: "logo" }, "herdr"), compte, boutonCote(), menuPlus(() => null)),
      err: el("div", { class: "note-cote erreur" }),
      spaces: el("div", { class: "section" }), agents: el("div", { class: "section" }) };
    S.liste.replaceChildren(c.tete, c.err, c.spaces, c.agents);
    force = true;
  }
  c.compte.textContent = S.agents.length + (S.agents.length > 1 ? " terminaux" : " terminal");
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
  const t = S.agents.length ? "Choisis un agent dans la liste" : "Aucun agent";
  if (S.accueil && S.accueil.racine.parentNode === S.panneau) { S.accueil.texte.textContent = t; return; }
  const texteAccueil = el("div", { class: "accueil-texte" }, t);
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
  if (v.ro) v.ro.disconnect();
  if (v.rec) finVocal(v, false);
  S.vue = null;
}

function ouvrirAgent(addr, sous) {
  const a = trouver(addr);
  const v = { addr, sous, mode: null, pid: a ? a.pid : null, msgCarte: null,
    suivi: true, nouveaux: 0, offset: null, commandes: [], selCmd: 0 };
  v.rond = el("span", { class: "rond-hote" });
  v.nom = el("div", { class: "nom" }, a ? nomAgent(a) : addr);
  v.meta = el("div", { class: "meta" });
  v.subs = el("div", { class: "subs-entete" });
  const retour = sous
    ? el("a", { class: "retour-parent", href: hashAgent(addr), title: "Retour à l'agent parent" },
      "‹ " + (a ? nomAgent(a) : addr))
    : el("a", { class: "retour", href: "#/", title: "Retour" }, "‹");
  const actions = el("div", { class: "actions-entete" }, menuPlus(() => trouver(addr)));
  if (!sous) { // un sous-agent in-process n'a pas de terminal à fermer
    const fermer = el("button", { type: "button", class: "bouton-fermer", title: "Fermer…",
      ariaLabel: "Fermer " + (a ? nomAgent(a) : addr), onclick: () => demanderFermeture(addr) });
    fermer.append(iconeCroix());
    actions.append(fermer);
  }
  const entete = el("header", { class: "entete" }, boutonCote(), retour, v.rond,
    el("div", { class: "titres" }, v.nom, v.meta, v.subs), actions);
  v.chat = el("div", { id: "chat" });
  v.bas = el("button", { type: "button", class: "bouton-bas", title: "Revenir en bas", ariaLabel: "Revenir en bas",
    hidden: true, onclick: () => allerEnBas(v) });
  v.bas.append(svg("0 0 24 24", "ico", ["path", { d: "M12 5v13M6 12l6 6 6-6", fill: "none", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" }]));
  v.live = el("button", { type: "button", class: "live actif", onclick: () => allerEnBas(v) });
  v.carte = el("div", { id: "choix", hidden: true });
  v.statut = el("div", { id: "statut" });
  v.cmds = el("div", { class: "commandes", role: "listbox", hidden: true });
  const kids = [entete, el("div", { class: "cadre-chat" }, v.chat, v.bas), el("div", { class: "barre-live" }, v.live),
    v.carte, v.statut, v.cmds];
  if (sous) kids.push(el("div", { class: "lecture-seule" }, "Sous-agent : lecture seule"));
  else kids.push(document.getElementById("pied").content.cloneNode(true));
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
    v.nom.textContent = (s && texte(s.title)) || "sous-agent " + texte(v.sous);
    remplacer(v.rond, rond(s ? s.state : "done"), iconeSous(s));
    remplacer(v.meta, s ? badgeEtat(s.state) : el("span", {}, "terminé"), s ? badgeModele(s.model) : null,
      el("span", {}, "de " + nomAgent(a)), texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null);
    return;
  }
  v.nom.textContent = nomAgent(a);
  remplacer(v.rond, rond(a.state), iconeRole(a));
  const d = depuis(a.since_ms);
  remplacer(v.meta, badgeEtat(a.state), badgeModele(a.model),
    d ? el("span", { class: "depuis" }, d) : null,
    texte(a.dir) ? el("span", { class: "dir" }, a.dir) : null, branche(a.branch),
    texte(a.machine) ? el("span", { class: "machine" }, a.machine) : null);
  // sous-agents actifs : pastilles cliquables vers leur fil (lecture seule)
  const actifs = (Array.isArray(a.subagents) ? a.subagents : []).filter((s) => !["done", "idle"].includes(s.state));
  remplacer(v.subs, ...actifs.map((s) => el("a", { class: "pastille-sous", href: hashAgent(a.address, s.id) },
    rond(s.state), iconeSous(s), el("span", {}, texte(s.title) || "sous-agent " + texte(s.id)), badgeModele(s.model))));
  v.subs.hidden = !actifs.length;
  if (a.state === "blocked") chargerChoix();
  else { v.carte.hidden = true; v.carte.replaceChildren(); }
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

// ---------- fil : suivi en bas et bouton « Live » ----------
function auBas(v) { return v.chat.scrollHeight - v.chat.scrollTop - v.chat.clientHeight < BAS_PX; }
function basDuChat(v) { v.chat.scrollTop = v.chat.scrollHeight; }

function majLive(v) {
  v.live.className = "live" + (v.suivi ? " actif" : "");
  v.live.textContent = v.suivi ? "Live" : "Live ↓" + (v.nouveaux ? " " + v.nouveaux : "");
  v.live.title = v.suivi ? "Suivi des nouveaux éléments" : "Revenir en bas et suivre";
  if (v.bas) v.bas.hidden = v.suivi; // visible seulement quand on a remonté
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
  v.chat.replaceChildren(v.flux);
  if (v.ro) v.ro.observe(v.flux);
  v.run = null; // suite d'outils consécutifs en cours
  v.outils = new Map(); // ref (id du tool_use) -> ligne d'outil, pour y accrocher son résultat
  v.dernierOutil = null;
  v.derniereCle = null; // clé du dernier élément rendu : point d'accroche d'une fusion
  v.groupe = null; // groupe de messages en cours : { auteur, corps }
}

function auteurGroupe(v, auteur) { // { nom, classe } de la tête de groupe
  const a = trouver(v.addr);
  if (auteur === "moi") return { nom: v.sous ? (a ? nomAgent(a) : "agent parent") : "Alex", classe: "user" };
  let nom = a ? nomAgent(a) : texte(v.addr);
  let modele = a ? a.model : null;
  if (v.sous) {
    const s = (a && Array.isArray(a.subagents) ? a.subagents : []).find((x) => x && x.id === v.sous);
    nom = (s && texte(s.title)) || "sous-agent " + texte(v.sous);
    modele = s ? s.model : null;
  }
  const f = familleModele(modele);
  return { nom: nom || "agent", classe: "ia" + (f ? " " + f.famille : "") }; // pas « agent » : classe des cartes
}

function corpsPour(v, auteur, ts) { // le corps du groupe en cours, ou un nouveau groupe si l'auteur change
  if (v.groupe && v.groupe.auteur === auteur) return v.groupe.corps;
  const qui = auteurGroupe(v, auteur);
  const heure = heureParis(ts);
  const corps = el("div", { class: "groupe-corps" },
    el("div", { class: "groupe-tete" }, el("span", { class: "auteur " + qui.classe }, qui.nom),
      heure ? el("span", { class: "heure" }, "· " + heure) : null));
  v.flux.append(el("div", { class: "groupe-msg " + (auteur === "moi" ? "de-user" : "de-agent") },
    el("div", { class: "avatar " + qui.classe, ariaHidden: "true" }, qui.nom.slice(0, 1).toUpperCase()), corps));
  v.groupe = { auteur, corps };
  v.run = null;
  return corps;
}

function cleItem(it) {
  return [it.role, it.ts, it.ref, it.tool, texte(it.text).slice(0, 160), it.image, it.error]
    .map((x) => texte(x)).join("\u0001");
}

function bulle(it) {
  if (it.role === "moi") {
    const b = el("div", { class: "bulle moi" });
    if (typeof it.image === "string" && it.image.startsWith("/") && !it.image.startsWith("//")) { // chemin absolu, jamais une URL
      const src = "/api/image?p=" + encodeURIComponent(it.image);
      b.append(el("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, el("img", { src, alt: "image envoyée" })));
    }
    if (texte(it.text)) b.append(it.text);
    return b;
  }
  const m = el("div", { class: "msg-agent md" });
  for (const n of MD.rendre(texte(it.text), document)) m.append(n);
  return m;
}

function ligneOutil(it) {
  const nom = texte(it.tool) || "outil";
  let arg = texte(it.text);
  if (arg.startsWith(nom + ": ")) arg = arg.slice(nom.length + 2);
  else if (arg === nom) arg = "";
  const d = el("details", { class: "outil" },
    el("summary", {}, el("span", { class: "nom-outil" }, nom),
      arg ? el("span", { class: "arg" }, " · " + arg.split("\n")[0]) : null));
  d.sortie = el("pre", { class: "sortie attente" }, "en cours…");
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
  remplacer(run.tete, el("span", {}, n + " outils"),
    run.erreurs ? el("span", { class: "err" }, " · " + run.erreurs + " en erreur") : null,
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
    ligne = ligneOutil({ tool: "résultat", text: "résultat" });
    ajouterOutil(v, ligne);
  }
  ligne.resolu = true;
  ligne.sortie.className = "sortie";
  ligne.sortie.textContent = texte(it.text) || "(vide)";
  if (it.error) {
    ligne.classList.add("erreur");
    ligne.sortie.classList.add("erreur");
    ligne.run.erreurs++;
    majRun(ligne.run);
  }
}

function ajouterItems(v, items) { // rend le nombre d'éléments visibles ajoutés
  let n = 0;
  for (const it of Array.isArray(items) ? items : []) {
    if (!it) continue;
    v.derniereCle = cleItem(it);
    if (it.role === "resultat") { attacherResultat(v, it); continue; }
    n++;
    if (it.role === "outil") {
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

function remplacerFil(v, items) {
  nouveauFil(v);
  ajouterItems(v, items);
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
  const pre = v.flux && v.flux.querySelector("pre.brut");
  if (!pre) return;
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

async function chargerChat(fusion) {
  const v = S.vue;
  if (!v || v.sous) return;
  try {
    const d = await api("GET", urlAgent(v.addr, "chat?n=200"));
    if (S.vue !== v) return;
    const etaitChat = v.mode === "chat";
    v.mode = d.mode;
    if (d.mode === "brut") {
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
    if (e.status === 404 && S.vue === v && trouver(v.addr)) statut("fil du sous-agent introuvable", true);
    else if (e.status !== 401 && !sortieAgent(e)) statut(messageErreur(e), true);
  } finally { v.charge = false; }
}

function ajouterChat(p) {
  const v = S.vue;
  if (!v || v.sous || p.addr !== v.addr || v.mode !== "chat") return;
  if (p.reset) remplacerFil(v, p.items); // reset : la liste reçue remplace tout le fil
  else suite(v, p.items);
}

async function chargerChoix() {
  const v = S.vue;
  if (!v) return;
  try {
    const d = await api("GET", urlAgent(v.addr, "choices"));
    if (S.vue !== v) return;
    v.msgCarte = d.msg;
    const kids = [el("div", { class: "question" }, texte(d.msg) || "Choix demandé")];
    for (const c of d.choices || []) {
      const b = el("button", { type: "button", onclick: () => repondre(c.digit) }, texte(c.digit) + "  " + texte(c.label));
      if (texte(c.detail)) b.append(el("small", {}, c.detail));
      b.disabled = S.occupe;
      kids.push(b);
    }
    if (!(d.choices || []).length) kids.push(el("div", { class: "note" }, "Aucune option lisible à l'écran"));
    v.carte.replaceChildren(...kids);
    v.carte.hidden = false;
  } catch (e) { if (e.status !== 401 && !sortieAgent(e)) statut(messageErreur(e), true); }
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
      c.interactive ? el("span", { class: "cmd-inter" }, "s'ouvre dans le terminal") : null,
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
  majEnvoi(v);
  v.cmds.hidden = true;
  v.selCmd = 0;
  statut(c.interactive ? "/" + c.name + " s'ouvre dans le terminal : son sélecteur n'apparaît pas dans le fil" : "");
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
      if (e.code === "bloque") { statut("l'agent attend un choix", true); chargerChoix(); }
      else if (e.code === "perime") { statut("la demande a changé", true); chargerChoix(); }
      else statut(messageErreur(e), true);
    }
  }
  figer(false);
}

function repondre(digit) {
  return envoi("envoi en attente…", async (v) => {
    await api("POST", urlAgent(v.addr, "answer"), { digit, msg: v.msgCarte || "" });
    v.carte.hidden = true; // un second appui renverrait le chiffre
    v.carte.replaceChildren();
    statut("");
  });
}

async function envoyerTexte() {
  const t = S.vue && S.vue.champ.value.trim();
  if (!t) return;
  S.vue.cmds.hidden = true;
  await envoi("envoi en attente…", async (v) => {
    await api("POST", urlAgent(v.addr, "send"), { text: t });
    v.champ.value = "";
    majEnvoi(v);
    statut("");
  });
  if (S.vue && S.vue.champ) S.vue.champ.focus();
}

async function envoyerFichier(f, nom, route, max) {
  if (!S.vue || !f || S.occupe) return;
  if (f.size > max) { statut("fichier trop gros (max " + max / 1048576 + " Mo)", true); return; }
  await envoi(route === "voice" ? "transcription…" : "envoi en attente…", async (v) => {
    const fd = new FormData();
    fd.append("file", f, nom);
    if (route === "photo" && v.champ.value.trim()) fd.append("caption", v.champ.value.trim());
    await api("POST", urlAgent(v.addr, route), fd);
    statut(""); // le texte transcrit arrive dans le fil comme un message envoyé
    if (route === "photo") { v.champ.value = ""; majEnvoi(v); }
  });
}

function depuisInput(ev, route, max) {
  const input = ev.target;
  const f = input.files && input.files[0];
  input.value = "";
  fermerMenu();
  if (f) envoyerFichier(f, f.name, route, max);
}

function fermerMenu() {
  const m = document.getElementById("menu-plus");
  const b = document.getElementById("plus");
  if (m) m.hidden = true;
  if (b) b.setAttribute("aria-expanded", "false");
}

function majEnvoi(v) { // façon Discord : flèche d'envoi seulement avec du texte, sinon le micro
  if (!v || !v.champ || !v.btnEnvoyer) return;
  const plein = texte(v.champ.value).trim() !== "";
  v.btnEnvoyer.hidden = !plein;
  if (v.btnMicro) v.btnMicro.hidden = plein && !v.rec; // jamais pendant un enregistrement
}

function brancherSaisie(v) {
  const p = S.panneau;
  v.champ = p.querySelector("#champ");
  v.btnEnvoyer = p.querySelector("#envoyer");
  v.btnMicro = p.querySelector("#micro");
  p.querySelector("#saisie").addEventListener("submit", (ev) => { ev.preventDefault(); envoyerTexte(); });
  v.champ.addEventListener("input", () => { v.selCmd = 0; majCommandes(v); majEnvoi(v); });
  v.champ.addEventListener("keydown", (ev) => clavierCommandes(v, ev));
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
        + (rec.dehors ? " · relâcher pour annuler" : " · glisser pour annuler");
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
    }).catch(() => { finVocal(v, false); statut("micro refusé ou indisponible", true); });
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
  if (envoyer && court) statut("maintenir pour parler");
  else if (!envoyer) statut("vocal annulé");
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
      if (a.state === "blocked" && (!p || p.state !== "blocked")
          && !(S.vue && S.vue.addr === a.address && !S.vue.sous)) annoncer(a);
    }
  }
  const nomsAvant = [...new Set([...avant.values()].map((a) => texte(a.space)))].sort().join("|");
  const nomsApres = [...new Set(S.agents.map((a) => texte(a.space)))].sort().join("|");
  if (!S.premier && nomsAvant !== nomsApres) chargerSpaces().then(rendreCote);
  S.premier = false;
  majBandeaux();
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
  es.addEventListener("ping", () => {});
  es.onerror = async () => {
    // EventSource ne dit pas pourquoi : sonder pour distinguer un jeton refusé d'une coupure
    try {
      const r = await fetch("/api/agents", { credentials: "same-origin" });
      if (r.status === 401) afficherConnexion();
    } catch (_) { /* hors ligne : EventSource retente seul */ }
  };
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

// les durées « depuis » et l'heure vieillissent sans événement
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
    const bouton = el("button", { class: "bandeau alerte", type: "button" }, "Activer les notifications");
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
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
function demarrer(delai) {
  api("GET", "/api/agents").then(async (liste) => {
    S.agents = Array.isArray(liste) ? liste : [];
    await chargerSpaces();
    route(); connecterFlux(); initPush(); chargerBarre();
  }).catch((e) => {
    if (e.status === 401) return; // l'écran de jeton est déjà affiché
    const msg = el("div", { class: "vide" }, messageErreur(e) + " — nouvelle tentative dans " + delai + " s");
    app.replaceChildren(el("div", { class: "attente" }, el("h1", {}, "herdr"), msg));
    setTimeout(() => demarrer(Math.min(delai * 2, 30)), delai * 1000);
  });
}
demarrer(2);
