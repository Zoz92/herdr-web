// Banc de la page (static/app.js) sous node, avec un faux DOM qui se comporte comme le vrai là où
// la panne se cache : append(null) écrit « null », append(undefined) écrit « undefined ».
// Usage : node page_banc.js <scénario> [chemin de app.js]. Imprime un JSON.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "static");
const scenario = process.argv[2];
const appJs = process.argv[3] || path.join(STATIC, "app.js");

let actif = null; // document.activeElement
function contient(n, x) { for (let e = x; e; e = e.parentNode) if (e === n) return true; return false; }
function perdFocus(n) { if (actif && contient(n, actif)) actif = null; } // détaché : le focus tombe
class Noeud {
  constructor() { this.parentNode = null; this.childNodes = []; }
  get isConnected() { return contient(document.body, this); }
  contains(x) { return contient(this, x); }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  get firstChild() { return this.childNodes[0] || null; }
  _adopte(x) {
    const n = x instanceof Noeud ? x : new Texte(String(x)); // comme le DOM : null -> « null »
    if (n instanceof Fragment) return n.childNodes.splice(0).map((c) => this._adopte(c)).flat();
    if (n.parentNode) { perdFocus(n); n.parentNode.childNodes = n.parentNode.childNodes.filter((c) => c !== n); }
    n.parentNode = this;
    return [n];
  }
  append(...xs) { for (const x of xs) this.childNodes.push(...this._adopte(x)); }
  appendChild(x) { this.append(x); return x; }
  insertBefore(x, ref) { const n = this._adopte(x); const i = this.childNodes.indexOf(ref); this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, ...n); return x; }
  prepend(...xs) { const n = xs.map((x) => this._adopte(x)).flat(); this.childNodes.unshift(...n); }
  replaceChildren(...xs) { for (const c of this.childNodes) { perdFocus(c); c.parentNode = null; } this.childNodes = []; this.append(...xs); }
  remove() { perdFocus(this); if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter((c) => c !== this); this.parentNode = null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(t) { this.replaceChildren(); if (t != null && t !== "") this.append(String(t)); }
  descendants() { return this.childNodes.flatMap((c) => (c.nodeType === 1 ? [c, ...c.descendants()] : [])); }
  querySelectorAll(sel) {
    const alts = sel.split(",").map((s) => s.trim().split(/\s+/));
    return this.descendants().filter((e) => alts.some((chaine) => e._correspond(chaine)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
// relevé (HERDR_RELEVE=<fichier>) : chaque texte créé et chaque title / aria-label / placeholder / alt posé,
// même s'il a disparu à la fin du scénario ; tests/test_i18n.py y cherche du texte hors dictionnaire
const RELEVE = process.env.HERDR_RELEVE ? [] : null;
const relever = (v) => { if (RELEVE && v != null && String(v) !== "") RELEVE.push(String(v)); };
if (RELEVE) process.on("exit", () => fs.writeFileSync(process.env.HERDR_RELEVE, JSON.stringify(RELEVE)));
class Texte extends Noeud {
  constructor(t) { super(); this.nodeType = 3; this.data = t; relever(t); }
  get textContent() { return this.data; }
}
class Fragment extends Noeud { constructor() { super(); this.nodeType = 11; } }

function simple(e, s) {
  const m = s.match(/^([a-z]*)((?:[.#][\w-]+)*)$/i);
  if (!m) return false;
  if (m[1] && e.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
  for (const p of m[2].match(/[.#][\w-]+/g) || []) {
    if (p[0] === "." && !e.classList.contains(p.slice(1))) return false;
    if (p[0] === "#" && e.id !== p.slice(1)) return false;
  }
  return true;
}

class Element extends Noeud {
  constructor(tag) {
    super();
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = {}; this.dataset = {};
    this.style = { setProperty(k, v) { this[k] = String(v); }, removeProperty(k) { delete this[k]; },
      getPropertyValue(k) { return this[k] || ""; } };
    this.className = ""; this.id = ""; this.hidden = false;
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; this.clientWidth = 800;
    const soi = this;
    this.classList = {
      _l() { return soi.className.split(/\s+/).filter(Boolean); },
      contains(c) { return this._l().includes(c); },
      add(...cs) { soi.className = [...new Set([...this._l(), ...cs])].join(" "); },
      remove(...cs) { soi.className = this._l().filter((c) => !cs.includes(c)).join(" "); },
      toggle(c, f) { const on = f === undefined ? !this.contains(c) : !!f; if (on) this.add(c); else this.remove(c); return on; },
    };
  }
  removeAttribute(k) { delete this.attrs[k]; }
  setAttribute(k, v) { if (["title", "aria-label", "placeholder", "alt"].includes(k)) relever(v); this.attrs[k] = String(v); if (k === "class") this.className = String(v); if (k === "id") this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, f) { ((this._ec ||= {})[t] ||= []).push(f); }
  removeEventListener() {}
  declencher(t, ev = {}) {
    const e = Object.assign({ type: t, target: this, defaultPrevented: false }, ev);
    e.preventDefault = () => { e.defaultPrevented = true; };
    for (const f of (this._ec && this._ec[t]) || []) f(e);
    return e;
  }
  click() { if (!this.disabled) this.declencher("click"); }
  focus() { actif = this; }
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, right: 40, top: 0, bottom: 40, width: 600, height: 40 }; }
  closest(sel) { let e = this; while (e && e.nodeType === 1) { if (sel.split(",").some((s) => simple(e, s.trim()))) return e; e = e.parentNode; } return null; }
  _correspond(chaine) {
    if (!simple(this, chaine[chaine.length - 1])) return false;
    let e = this.parentNode;
    for (let i = chaine.length - 2; i >= 0; i--) {
      while (e && !(e.nodeType === 1 && simple(e, chaine[i]))) e = e.parentNode;
      if (!e) return false;
      e = e.parentNode;
    }
    return true;
  }
  set innerHTML(_) { throw new Error("innerHTML interdit"); }
}

for (const k of ["title", "ariaLabel", "placeholder", "alt"]) { // n[k] = v (el()) passe par le relevé
  Object.defineProperty(Element.prototype, k, { configurable: true,
    get() { return this["_" + k]; }, set(v) { relever(v); this["_" + k] = v; } });
}
const parId = {};
const document = {
  body: new Element("body"),
  createElement: (t) => new Element(t),
  createElementNS: (_, t) => new Element(t),
  createTextNode: (t) => new Texte(String(t)),
  getElementById: (id) => parId[id] || null,
  querySelector: (s) => document.body.querySelector(s),
  querySelectorAll: (s) => document.body.querySelectorAll(s),
  // gardés sans être déclenchés : seul un scénario qui le demande (visibilitychange) les appelle
  ecouteurs: {},
  addEventListener(t, f) { (this.ecouteurs[t] ||= []).push(f); },
  hidden: false,
  get activeElement() { return actif && actif.isConnected ? actif : document.body; },
};
function enregistrer(e, id) { e.id = id; parId[id] = e; return e; }
const app = enregistrer(new Element("main"), "app");
const bandeaux = enregistrer(new Element("div"), "bandeaux");
bandeaux.append(enregistrer(new Element("div"), "bandeau-deco"), enregistrer(new Element("button"), "bandeau-alerte"));
document.body.append(bandeaux, app);
// gabarit du pied : les éléments que la page y cherche
const pied = enregistrer(new Element("template"), "pied");
pied.content = { cloneNode() {
  const f = new Fragment();
  const form = new Element("form"); form.id = "saisie";
  const mk = (tag, id) => { const e = new Element(tag); e.id = id; return e; };
  const plus = new Element("div"); plus.className = "plus";
  plus.append(mk("button", "plus"), mk("div", "menu-plus"));
  const micro = mk("button", "micro");
  const cpt = new Element("span"); cpt.className = "compteur"; micro.append(cpt);
  form.append(plus, mk("input", "champ"), micro, mk("input", "in-galerie"), mk("input", "in-photo"), mk("input", "in-voix"),
    mk("button", "envoyer"));
  f.append(form);
  return f;
} };

const intervalles = []; // minuteries de la page : le banc les déclenche à la main
const minuteurs = new Map(); // setTimeout : jamais seuls, feu() les déclenche
let nMinuteurs = 0;
const feu = () => { for (const [id, f] of [...minuteurs]) { minuteurs.delete(id); f(); } };
const ctx = {
  document, console: { log() {}, error() {} }, location: { hash: "" }, navigator: {},
  fetch: () => new Promise(() => {}), setInterval: (f) => { intervalles.push(f); return intervalles.length; },
  clearInterval() {}, setTimeout: (f) => { minuteurs.set(++nMinuteurs, f); return nMinuteurs; },
  clearTimeout: (id) => { minuteurs.delete(id); },
  requestAnimationFrame: () => 0, performance: { now: () => 0 }, Date, Math, JSON, Promise, Map, Set,
  Object, Array, String, Number, isFinite, encodeURIComponent, decodeURIComponent, URL, Error,
  FormData: class {}, Blob: class {},
};
ctx.window = ctx;
const stockage = new Map(); // localStorage de l'appareil, pour la taille du fil (point 38)
ctx.localStorage = { getItem: (k) => (stockage.has(k) ? stockage.get(k) : null),
  setItem: (k, v) => stockage.set(k, String(v)), removeItem: (k) => stockage.delete(k) };
ctx.EventSource = class { constructor(u) { this.url = u; ctx.__es = this; } addEventListener() {} close() {} };
ctx.addEventListener = () => {};
vm.createContext(ctx);
// langue de la page : HERDR_LANGUE (fr par défaut, la langue des scénarios écrits avant l'anglais) ;
// dans le navigateur, le serveur la pose dans <html lang>, lue par i18n.js
for (const f of ["en.js", "fr.js", "i18n.js"]) {
  vm.runInContext(fs.readFileSync(path.join(STATIC, "i18n", f), "utf8"), ctx, { filename: f });
}
vm.runInContext(`I18N.poser(${JSON.stringify(process.env.HERDR_LANGUE || "fr")})`, ctx);
for (const f of [path.join(STATIC, "md.js"), path.join(STATIC, "commandes.js"), path.join(STATIC, "palette.js"),
  path.join(STATIC, "vendor", "qrcode", "qrcode.js"), appJs]) {
  vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: path.basename(f) });
}

const NUL = /\b(null|undefined|NaN)\b/;
function fautes() { // chaque nœud texte et chaque attribut title qui porte null/undefined/NaN
  const out = [];
  const visite = (n, chemin) => {
    if (n.nodeType === 3 && NUL.test(n.data)) out.push(chemin + " : « " + n.data.slice(0, 60) + " »");
    if (n.nodeType === 1) {
      for (const k of ["title", "href"]) if (typeof n[k] === "string" && NUL.test(n[k])) out.push(chemin + "[" + k + "]");
      n.childNodes.forEach((c) => visite(c, chemin + ">" + n.tagName.toLowerCase() + (n.className ? "." + n.className.split(" ")[0] : "")));
    }
  };
  visite(document.body, "");
  return out;
}

const run = (code) => vm.runInContext(code, ctx);

// ---------- scénarios asynchrones : api() remplacée par un faux serveur ----------
const appels = [];
let reponses = () => ({});
ctx.api = async (method, chemin, corps) => {
  appels.push([method, chemin, corps === undefined ? null : JSON.parse(JSON.stringify(corps))]);
  const r = reponses(method, chemin, corps);
  if (r && r.__erreur) throw r.__erreur;
  return r;
};
const souffle = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
const tic = () => { for (const f of intervalles.slice()) f(); }; // la minuterie de 15 s, et les autres
const S = () => run("S"); // const de la page : lexical, pas une propriété du global
const agent = (addr, plus = {}) => Object.assign({ address: addr, name: "agent-" + addr, state: "working",
  program: "claude-code", pid: 5, space: "sp", tab: "t", cwd: "/home/user/" + addr, dir: addr, subagents: [] }, plus);
const SPACES = [{ id: "sp1", name: "sp", folder: "/home/user/a" }, { id: "sp2", name: "autre", folder: "/home/user/b" }];
const CONNUS = [{ id: "claude", name: "Claude Code", installed: true },
  { id: "opencode", name: "OpenCode", installed: true }, { id: "codex", name: "Codex", installed: false }];
// tr("Annuler") : le même libellé dans la langue du banc (sa clé retrouvée dans fr.js) ; un texte venu
// des données (« Rouge », « Claude Code ») n'a pas de clé et reste tel quel
const tr = (fr) => {
  const D = run("I18N.DICOS");
  const k = Object.keys(D.fr).find((x) => D.fr[x] === fr);
  return k ? run("I18N.t")(k) : fr;
};
const parTexte = (racine, sel, t) => racine.querySelectorAll(sel).find((n) => n.textContent.includes(tr(t))) || null;
function serveurDeBase(m, chemin) {
  if (chemin === "/api/known-agents") return CONNUS;
  if (chemin === "/api/spaces") return SPACES;
  if (chemin.endsWith("/commands")) return [];
  if (chemin.includes("/chat")) return { mode: "chat", items: [] };
  return {};
}

const SCENARIOS = {
  // roue crantée (2026-11-15) : panneau, voix partagée, sections masquées, notifications, déconnexion
  async "reglages"() {
    const REG = { handy: { active: true, bin: "/nix/store/x-handy-0.9.8/bin/h", version: "0.9.8", modele: "Qwen3",
      langue: "fr", delai_s: 90, ffmpeg: "/bin/ffmpeg", bin_erreur: null },
    version: { commit: "abc123def456", cache: "herdr-coquille-v28" },
    session: { cookie_max_age_s: 31536000, appareils_push: ["fcm.googleapis.com", "fcm.googleapis.com"], modele: "un jeton partagé" } };
    reponses = (m, chemin, corps) => {
      if (chemin === "/api/reglages") return m === "GET" ? REG : { ok: true, handy: corps.handy };
      return serveurDeBase(m, chemin);
    };
    const notifs = [], vibre = [], demandes = [];
    ctx.Notification = class { constructor(t, o) { notifs.push([t, o && o.tag]); } };
    ctx.Notification.permission = "default";
    ctx.Notification.requestPermission = async () => { demandes.push(1); ctx.Notification.permission = "granted"; return "granted"; };
    ctx.navigator.vibrate = (x) => vibre.push(x);
    S().agents = [agent("t1", { state: "idle" }), agent("tt", { program: "zsh", state: null, role: "terminal" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    S().premier = false; // le premier lot d'agents n'annonce rien : celui-ci est déjà passé
    const out = {};
    const roue =S().liste.querySelector(".cote-tete .bouton-reglages");
    out.roue = !!roue;
    const sections = () => S().liste.querySelectorAll(".groupe-tete").map((t) => t.dataset.cle);
    out.sectionsAvant = sections();
    roue.click();
    await souffle();
    const boite = () => document.body.querySelector(".boite.reglages");
    out.titres = boite().querySelectorAll(".reglages-titre").map((t) => t.textContent);
    out.onglets = boite().querySelectorAll(".onglet-reglages").map((t) => t.querySelector(".nom").textContent);
    // lignes « intitulé -> valeur » des cartes (un contrôle se lit par son rôle), chemins complets des Détails
    out.lignes = Object.fromEntries(boite().querySelectorAll(".reglage-ligne").map((l) => {
      const d = l.querySelector(".reglage-droite");
      const v = !d ? null : d.querySelector(".interrupteur") ? "switch" : d.querySelector(".segmente") ? "segmente"
        : d.querySelector("button") ? "bouton" : d.textContent;
      const d2 = l.querySelector(".description");
      return [l.querySelector(".intitule").textContent, d2 && !["switch", "bouton", "segmente"].includes(v) ? [v, d2.textContent] : v];
    }));
    out.chemins = Object.fromEntries(boite().querySelectorAll(".details-reglages .chemin").map((l) =>
      [l.querySelector(".nom").textContent, l.querySelector(".val").textContent]));
    out.versionNav = boite().querySelector(".reglages-version").textContent;
    const caseDe = (nom) => boite().querySelectorAll(".reglage-bascule").find((l) => l.querySelector(".intitule").textContent === tr(nom))
      .querySelector(".interrupteur");
    out.demandesAvantClic = demandes.length;
    // a. voix désactivée (clic sur l'interrupteur) : appel serveur, puis plus de micro dans la vue d'un agent
    const voix = caseDe("Voix activée");
    out.voixRole = [voix.getAttribute("role") || voix.role, voix.getAttribute("aria-checked"), voix.tabIndex];
    voix.click();
    await souffle();
    out.appelVoix = appels.filter((a) => a[1] === "/api/reglages" && a[0] === "POST").map((a) => a[2]);
    out.voix = run("S.voix");
    out.voixAria = voix.getAttribute("aria-checked");
    out.pastille = boite().querySelector(".etat-voix").textContent;
    // d. section masquée (par appareil), au CLAVIER : Espace la masque, une autre touche ne fait rien, Entrée la rend
    const term = caseDe("Terminaux");
    const espace = term.declencher("keydown", { key: " " });
    term.declencher("keydown", { key: "a" }); // une autre touche : rien
    out.clavier = [term.getAttribute("aria-checked"), espace.defaultPrevented];
    out.sectionsMasquees = sections();
    out.stockage = [ctx.localStorage.getItem("herdr.masque-terminaux")];
    term.declencher("keydown", { key: "Enter" });
    out.entree = [term.getAttribute("aria-checked"), sections()];
    // tri par défaut : contrôle segmenté
    // le tri, pas la langue (deux contrôles segmentés dans Affichage depuis le choix de langue)
    const tri = boite().querySelectorAll(".segmente").find((g) => g.ariaLabel === tr("Tri par défaut"));
    const segs = tri.querySelectorAll(".segment");
    out.segments = segs.map((s) => [s.textContent, s.getAttribute("aria-checked")]);
    segs[1].click();
    out.triApres = [ctx.localStorage.getItem("herdr.tri-agents"), segs.map((s) => s.getAttribute("aria-checked"))];
    tri.declencher("keydown", { key: "ArrowLeft" });
    out.triClavier = ctx.localStorage.getItem("herdr.tri-agents");
    // c. notifications : permission demandée au clic seulement, puis notification au passage en blocked
    const notif = caseDe("Notifier quand un agent attend"); notif.click();
    await souffle();
    out.demandesApresClic = demandes.length;
    out.notifStockee = ctx.localStorage.getItem("herdr.notif-needs-you");
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked", msg: "Bash: x" }))}]);`);
    await souffle();
    out.notifs = notifs;
    out.vibre = vibre.length > 0;
    // e. déconnexion : confirmation d'abord ; Annuler ne déconnecte pas ; confirmer déconnecte
    const n = appels.length;
    const boutonTexte = (t) => boite().querySelectorAll("button").find((b) => b.textContent === tr(t));
    boutonTexte("Déconnexion").click();
    await souffle();
    out.avantConfirmation = appels.slice(n).map((a) => a[0] + " " + a[1]);
    out.question = boite().querySelector(".confirmation .intitule").textContent;
    out.focusAnnuler = run("document.activeElement.textContent");
    boutonTexte("Annuler").click();
    await souffle();
    out.apresAnnuler = [appels.slice(n).length, !!boite(), !!boite().querySelector(".confirmation"), !!boutonTexte("Déconnexion")];
    boutonTexte("Déconnexion").click();
    boutonTexte("Se déconnecter").click();
    await souffle();
    out.logout = appels.slice(n).map((a) => a[0] + " " + a[1]);
    out.ferme = !boite();
    // serveur muet ou ancien (champs absents) : jamais « null », « undefined », « NaN » à l'écran
    reponses = (m, chemin) => (chemin === "/api/reglages" ? { handy: {}, version: null, session: { appareils_push: [] } } : {});
    run("ouvrirReglages()");
    await souffle();
    out.fautesVide = fautes();
    out.valsVide = boite().querySelectorAll(".reglage-ligne .valeur").map((v) => v.textContent);
    out.versionVide = boite().querySelector(".reglages-version").textContent;
    run("fermerReglages()");
    reponses = (m, chemin) => (chemin === "/api/reglages" ? { __erreur: { status: 404, code: "introuvable" } } : {});
    run("ouvrirReglages()");
    await souffle();
    out.fautesAncien = fautes();
    out.ancien = boite().querySelector(".note-cote.erreur").textContent;
    run("fermerReglages()");
    // la voix suit le serveur : micro absent puis revenu (événement « reglages » d'un autre appareil)
    S().agents = [agent("t1", { state: "idle" })];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    out.microCoupe = S().panneau.querySelector("#micro").hidden;
    run("appliquerReglagesServeur({ handy: true });");
    out.microRevenu = S().panneau.querySelector("#micro").hidden;
    return out;
  },

  // rubrique Voix (brief handy-reglages, 2026-11-15) : Handy présent (lien discret, import, champs) ;
  // absent (suggestion, dépôt, agent d'installation après confirmation) ; serveur d'avant sans « present »
  async "handy-voix"() {
    const BASE = { active: true, version: "0.9.8", modele: "Qwen3", langue: "fr", delai_s: 90, ffmpeg: "/bin/ffmpeg",
      depot: "https://github.com/cjpais/Handy", dossier_perso: "/home/user" };
    const PRESENT = { ...BASE, present: true, bin: "/nix/store/x-handy-0.9.8/bin/h", bin_erreur: null };
    const ABSENT = { ...BASE, present: false, bin: "/nix/store/x-handy-0.9.8/bin/h", bin_erreur: "binaire introuvable : h",
      bin_motif: "bin_introuvable", bin_params: { nom: "h" } }; // serveur actuel : le motif, que la page traduit
    const VIDE = { modele: null, langue: null, vocabulaire: [], source: null };
    const IMPORTE = { modele: "parakeet-tdt-0.6b-v3", langue: "fr", vocabulaire: ["herdr", "Alex"], source: "handy" };
    const SP = [{ id: "sp1", name: "sp", folder: "/home/user/a" }, { id: "sp0", name: "my-project", folder: "/home/user" }];
    let handy = PRESENT;
    reponses = (m, chemin, corps) => {
      if (chemin === "/api/reglages") return m === "GET" ? { handy, voix: VIDE, version: {}, session: {} }
        : { ok: true, handy: true, nom_affiche: "Alex", voix: { ...corps.voix, source: "page" } };
      if (chemin === "/api/reglages/voix/import") return { ok: true, voix: IMPORTE };
      if (chemin === "/api/spaces/sp0/open") return { ok: true, result: { terminal: "t9" } };
      if (chemin === "/api/agents/t1/voice") return { ok: true, text: "bonjour",
        avertissement: "modèle « x » refusé par Handy : transcrit avec le modèle choisi dans Handy", motif: "modele_refuse", params: { modele: "x" } };
      return serveurDeBase(m, chemin);
    };
    S().agents = [agent("t1", { state: "idle" })];
    S().spaces = SP;
    run("squelette(); rendreCote(); rendreAccueil();");
    const boite = () => document.body.querySelector(".boite.reglages");
    const btn = (t) => (boite() ? boite().querySelectorAll("button").find((b) => b.textContent === tr(t)) : null);
    const clic = (t) => { const x = btn(t); if (x) x.click(); return !!x; } // absent : l'assertion mord, le banc ne casse pas
    const ouvrir = async () => { run("fermerReglages(); ouvrirReglages()"); await souffle(); };
    const voixPage = () => boite().querySelectorAll(".reglages-page")[0];
    const etat = () => ({
      suggestion: !!voixPage().querySelector(".suggestion-handy"),
      lignes: voixPage().querySelector(".suggestion-handy")
        ? voixPage().querySelector(".suggestion-texte").children.map((c) => c.textContent) : null,
      liens: voixPage().querySelectorAll("a.lien-projet").map((a) => [a.textContent, a.href, a.target, a.rel]),
      installer: !!btn("Installer Handy"), importer: !!btn("Importer la configuration d'Handy"),
      reference: !!voixPage().querySelectorAll(".note-cote").find((n) => n.textContent.includes("servent de référence")),
    });
    const out = {};
    // 1. Handy présent : lien discret seulement, import puis champs modifiables
    await ouvrir();
    out.present = etat();
    out.fautesPresent = fautes();
    const ecart = () => texte(voixPage().querySelector(".ecart-modele") && voixPage().querySelector(".ecart-modele").textContent);
    const texte = (x) => (x == null ? "" : String(x));
    out.ecartAvant = ecart();
    const n0 = appels.length;
    clic("Importer la configuration d'Handy");
    await souffle();
    const champs = () => voixPage().querySelectorAll(".champ-reglage");
    out.importAppel = appels.slice(n0).map((a) => [a[0], a[1], a[2]]);
    out.champsApresImport = champs().map((c) => c.value);
    out.msgImport = voixPage().querySelector(".msg-reglage").textContent;
    out.ecartApresImport = ecart(); // Handy : « Qwen3 » ; herdr-web : le modèle importé d'avant
    const [modele] = champs();
    if (modele) { modele.value = " whisper-small "; modele.declencher("change"); }
    await souffle();
    out.maj = appels.slice(n0).filter((a) => a[1] === "/api/reglages").map((a) => a[2]);
    // R5 : un autre appareil modifie la voix (événement « reglages ») : les champs suivent, panneau ouvert
    run(`appliquerReglagesServeur(${JSON.stringify({ voix: { modele: "Qwen3", langue: "en", vocabulaire: ["a"], source: "page" } })})`);
    out.champsApresDiffusion = champs().map((c) => c.value);
    out.ecartApresDiffusion = ecart(); // revenu au modèle de Handy : plus d'écart
    // 2. Handy absent : suggestion, et l'agent ne part qu'après confirmation
    handy = ABSENT;
    await ouvrir();
    out.absent = etat();
    out.fautesAbsent = fautes();
    const n1 = appels.length;
    clic("Installer Handy");
    await souffle();
    out.avantConfirmation = appels.slice(n1).map((a) => a[0] + " " + a[1]);
    const conf = boite() ? voixPage().querySelector(".confirmation") : null; // lancé sans demander : panneau fermé
    out.question = conf ? [conf.querySelector(".intitule").textContent, conf.getAttribute ? conf.role : null] : null;
    out.focus = run("document.activeElement && document.activeElement.textContent");
    clic("Annuler");
    await souffle();
    out.apresAnnuler = [appels.slice(n1).length, !!(boite() && voixPage().querySelector(".confirmation")), !!btn("Installer Handy")];
    clic("Installer Handy");
    const lancerBtn = btn("Lancer l'agent");
    if (lancerBtn) { lancerBtn.click(); lancerBtn.click(); } // R3 : double appui, un seul agent
    await souffle();
    out.lancement = appels.slice(n1).map((a) => [a[0], a[1], a[2]]);
    out.fermeApresLancement = !boite();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "idle" }))}, ${JSON.stringify(agent("t9", { space: "my-project" }))}]);`);
    await souffle();
    out.hash = ctx.location.hash;
    // 3. serveur d'avant : ni « present » ni binaire => suggestion, jamais « null » à l'écran
    handy = { active: true, bin: null, bin_erreur: "FileNotFoundError : Handy.desktop", delai_s: 90, ffmpeg: null };
    await ouvrir();
    out.ancien = etat();
    out.fautesAncien = fautes();
    run("fermerReglages()");
    // R4 : la réponse vocale dit le repli de modèle, dans la ligne d'état de la saisie
    const fd = ctx.FormData;
    ctx.FormData = class { append() {} };
    run(`ouvrirAgent("t1", null);`);
    await souffle();
    run(`envoyerFichier({ size: 10 }, "vocal.webm", "voice", 1e6)`);
    await souffle();
    out.statutVocal = S().vue.statut.textContent;
    ctx.FormData = fd;
    return out;
  },

  // refonte du panneau (brief design-reglages, 2026-11-15) : onglets latéraux, rubrique mémorisée,
  // téléphone : la liste des rubriques puis la page (classe « sur-page »), « ‹ Retour »
  async "reglages-rubriques"() {
    reponses = (m, chemin) => (chemin === "/api/reglages" ? { handy: null, version: { commit: "abc123def456", cache: "herdr-coquille-v27" },
      session: {} } : serveurDeBase(m, chemin));
    S().agents = [agent("t1", { state: "idle" })];
    run("squelette(); rendreCote(); rendreAccueil();");
    const boite = () => document.body.querySelector(".boite.reglages");
    const etat = () => ({
      selection: boite().querySelectorAll(".onglet-reglages").filter((o) => o.getAttribute("aria-selected") === "true").map((o) => o.dataset.rubrique),
      visibles: boite().querySelectorAll(".reglages-page").filter((p) => !p.hidden).map((p) => p.querySelector(".reglages-titre").textContent),
      surPage: boite().classList.contains("sur-page"),
    });
    const onglet = (k) => boite().querySelectorAll(".onglet-reglages").find((o) => o.dataset.rubrique === k);
    const out = {};
    run("ouvrirReglages()");
    await souffle();
    out.ouverture = etat(); // rien de mémorisé : Voix, et la liste d'abord (téléphone)
    out.tabindex = boite().querySelectorAll(".onglet-reglages").map((o) => o.tabIndex);
    out.relies = (() => { const o = onglet("voix"), p = boite().querySelector("#" + o.getAttribute("aria-controls"));
      return [o.getAttribute("role") || o.role, !!p, p && p.getAttribute("aria-labelledby") === o.id]; })();
    onglet("affichage").click();
    out.apresClic = etat();
    out.stockee = ctx.localStorage.getItem("herdr.reglages-rubrique");
    boite().querySelectorAll(".reglages-page").find((p) => !p.hidden).querySelector(".retour-reglages").click();
    out.apresRetour = etat();
    out.focusRetour = run("document.activeElement.dataset.rubrique");
    const bas = boite().querySelector(".reglages-nav").declencher("keydown", { key: "ArrowDown" });
    out.flecheBas = [etat(), run("document.activeElement.dataset.rubrique"), bas.defaultPrevented];
    boite().querySelector(".reglages-nav").declencher("keydown", { key: "ArrowUp" });
    boite().querySelector(".reglages-nav").declencher("keydown", { key: "ArrowUp" });
    out.flecheHaut = etat().selection;
    run("fermerReglages()");
    run("ouvrirReglages()");
    await souffle();
    out.reouverture = etat(); // la dernière rubrique revient, la liste d'abord sur téléphone
    out.version = boite().querySelector(".reglages-version").textContent;
    // stockage illisible ou valeur inconnue : Voix
    ctx.localStorage.setItem("herdr.reglages-rubrique", "inexistante");
    run("fermerReglages(); ouvrirReglages()");
    await souffle();
    out.inconnue = etat().selection;
    out.fautes = fautes();
    return out;
  },

  // brief point 5 : infobulles des icônes de rôle, au repos et au travail (table du brief, 2026-11-15)
  async "infobulles"() {
    const bulle = (a) => {
      const i = run(`iconeRole(${JSON.stringify(a)})`);
      if (!i) return null;
      const t = i.childNodes.find((c) => c.tagName === "TITLE");
      return [t ? t.textContent : i.title, t ? "title" : "attr", i.title === undefined ? null : i.getAttribute("aria-label") || i.ariaLabel || null];
    };
    const out = {};
    for (const role of ["maker", "concepteur", "checker", "checker-conception", "reviewer"]) {
      out[role] = [bulle({ role, state: "idle", program: "claude-code" })[0], bulle({ role, state: "working", program: "claude-code" })[0]];
    }
    out.shell = bulle({ role: "maker", activity: "shell", state: "working", program: "claude-code" })[0];
    out.claude = [bulle({ role: "claude", state: "done", program: "claude-code" })[0], bulle({ role: "claude", state: "working", program: "claude-code" })[0]];
    out.claudeSansRole = bulle({ state: "blocked", program: "claude-code" })[0]; // ancien serveur : rôle absent
    out.terminal = bulle({ role: "terminal", state: null, program: "zsh" });
    out.autre = [bulle({ role: "terminal", state: "idle", program: "codex" })[0], bulle({ role: "terminal", state: "working", program: "opencode" })[0]];
    out.sous = run(`iconeSous({ role: "checker", state: "working" })`).childNodes[0].textContent; // sous-agent : même table
    return out;
  },

  // réserves du checker de la PR #25 : agent ouvert pas ré-annoncé, /seen en échec sans relance à chaque
  // rafraîchissement, une seule vibration au passage en blocked quand la notification est active
  async "reserves"() {
    const out = {};
    const b = document.getElementById("bandeau-alerte");
    const vibre = [];
    ctx.navigator.vibrate = (x) => vibre.push(x);
    reponses = (m, chemin) => (chemin.endsWith("/seen") ? { __erreur: { status: 502, code: "herdr" } } : serveurDeBase(m, chemin));
    b.hidden = true;
    S().agents = [agent("t1", { state: "working" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    S().premier = false;
    // 1. t1 se bloque (bandeau), puis on l'ouvre pour lui répondre : le bandeau ne le ré-annonce plus
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked", msg: "q" }))}]);`);
    out.annonce = !b.hidden;
    run(`ouvrirAgent("t1", null);`);
    await souffle();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked", msg: "q" }))}]);`);
    out.ouvertReannonce = !b.hidden;
    // 2. /seen en échec : un essai, pas un par rafraîchissement ; de nouveau au retour sur la page
    const vus = () => appels.filter((a) => a[1].endsWith("/seen")).length;
    const fini = (turn) => JSON.stringify(agent("t1", { state: "done", turn, seen: 1 }));
    run(`surAgents([${fini(3)}]);`);
    await souffle();
    out.premierEssai = vus();
    for (let i = 0; i < 3; i++) { run(`surAgents([${fini(3)}]);`); await souffle(); }
    out.apresRafraichis = vus();
    for (const f of document.ecouteurs.visibilitychange || []) f();
    await souffle();
    out.retourVisible = vus();
    run(`surAgents([${fini(4)}]);`);
    await souffle();
    out.tourSuivant = vus();
    // 3. notification active : une seule vibration au passage en blocked
    ctx.Notification = class { constructor() {} };
    ctx.Notification.permission = "granted";
    ctx.localStorage.setItem("herdr.notif-needs-you", "1");
    run(`location.hash = "#/"; fermerVue();`);
    vibre.length = 0;
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working" }))}]);`);
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked", msg: "q" }))}]);`);
    await souffle();
    out.vibrations = vibre.length;
    out.bandeau = !b.hidden;
    return out;
  },

  // brief point 4 : le bandeau Needs you suit l'état (répondu dans herdr-app : il part ou passe au suivant)
  async "bandeau"() {
    reponses = serveurDeBase;
    const vibre = [];
    ctx.navigator.vibrate = (x) => vibre.push(x);
    const b = document.getElementById("bandeau-alerte");
    b.hidden = true;
    S().agents = [agent("t1", { state: "working" }), agent("t2", { state: "working" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    S().premier = false;
    const vu = () => (b.hidden ? null : [b.dataset.addr, b.textContent]);
    const lot = (e1, e2, m2) => run(`surAgents(${JSON.stringify([agent("t1", { state: e1, msg: "answer: oui" }),
      agent("t2", { state: e2, msg: m2 || "Bash: ls" })])});`);
    const out = {};
    lot("blocked", "working");
    out.annonce = vu();
    lot("blocked", "blocked");               // t2 se bloque aussi : annoncé à son tour
    out.deux = vu();
    lot("blocked", "blocked", "Bash: rm x"); // son message change : le bandeau suit
    out.msgChange = vu();
    lot("blocked", "working");               // t2 répondu dans herdr-app : t1 attend encore, il revient
    out.repondu = vu();
    lot("working", "working");               // plus personne n'attend : bandeau masqué
    out.personne = vu();
    lot("blocked", "working");
    run(`surAgents([${JSON.stringify(agent("t2", { state: "working" }))}]);`); // t1 fermé
    out.disparu = vu();
    out.vibrations = vibre.length;           // une par blocage nouveau, aucune au remplacement
    return out;
  },

  // « vu » partagé : ouvrir un agent pose herdr.seen (POST /seen), une fois par tour, jamais un sous-agent
  // langue de la page (brief langue-web, 2026-11-15) : sélecteur dans Réglages › Affichage, réglage serveur
  // partagé ; un changement recharge la page, après le prochain envoi s'il y a un brouillon
  async "langue"() {
    let langue = "fr";
    reponses = (m, chemin, corps) => {
      if (chemin === "/api/reglages") {
        if (m === "POST") { langue = corps.langue; return { ok: true, handy: true, nom_affiche: "Alex", langue }; }
        return { handy: { active: true }, voix: {}, version: {}, session: {}, nom_affiche: "Alex", langue };
      }
      return serveurDeBase(m, chemin);
    };
    let recharges = 0;
    ctx.location.reload = () => { recharges++; };
    S().agents = [agent("t1", { state: "idle" })];
    S().spaces = SPACES;
    const out = { langue: run("I18N.courante()") };
    run("squelette(); rendreCote(); rendreAccueil(); ouvrirReglages()");
    await souffle();
    const boite = () => document.body.querySelector(".boite.reglages");
    const choix = boite().querySelectorAll(".segmente").find((g) => g.classList.contains("choix-langue"));
    const ligne = choix.parentNode.parentNode;
    out.ligne = [ligne.querySelector(".intitule").textContent, ligne.querySelector(".description").textContent];
    out.options = choix.querySelectorAll(".segment").map((s) => [s.dataset.valeur, s.textContent, s.getAttribute("aria-checked")]);
    // même langue : rien n'est rechargé (événement d'un autre appareil qui n'a rien changé)
    run(`appliquerReglagesServeur({ langue: "fr" })`);
    out.memeLangue = recharges;
    // clic sur « English » : écrit au serveur, puis la page se recharge (champ vide)
    choix.querySelectorAll(".segment").find((s) => s.dataset.valeur === "en").click();
    await souffle();
    out.appel = appels.filter((a) => a[1] === "/api/reglages" && a[0] === "POST").map((a) => a[2]);
    out.message = boite().querySelector(".choix-langue").parentNode.parentNode.parentNode.querySelector(".msg-reglage").textContent;
    out.apresClic = recharges;
    // un autre appareil change la langue pendant une saisie : pas de rechargement avant l'envoi
    run("fermerReglages(); S.rechargement = false;");
    run(`ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    v.champ.value = "brouillon en cours"; v.champ.declencher("input");
    const avant = recharges;
    run(`appliquerReglagesServeur({ langue: "en" })`);
    out.pendantSaisie = recharges - avant;
    await ctx.envoyerTexte();
    out.apresEnvoi = recharges - avant;
    // langue inconnue (fichier écrit à la main, serveur futur) : ignorée
    run("S.rechargement = false;");
    const avantInconnue = recharges;
    run(`appliquerReglagesServeur({ langue: "de" })`);
    out.inconnue = recharges - avantInconnue;
    return out;
  },

  // textes calculés, dans la langue du banc : dates relatives, nombres, erreurs rendues par le serveur
  async "textes"() {
    const il = (s) => `depuis(Date.now() - ${s * 1000})`;
    return run(`({
      depuis: [${il(42)}, ${il(180)}, ${il(7200)}, ${il(3 * 86400)}],
      ctx: badgeContexte({ context: 123456, context_max: 200000 }).title,
      terminaux: [0, 1, 2].map((n) => tn("cote.terminaux", n)),
      outils: [1, 3].map((n) => tn("fil.outils", n)),
      erreurs: [
        messageErreur({ status: 400, code: "invalide", detail: "dossier absent ou hors de /home/x", motif: "dossier_hors", params: { home: "/home/x" } }),
        messageErreur({ status: 400, code: "invalide", detail: "voix.modele : identifiant invalide", motif: "voix_modele", params: {} }),
        messageErreur({ status: 404, code: "introuvable", detail: "onglet inconnu", motif: "onglet_inconnu" }),
        messageErreur({ status: 404, code: "introuvable", detail: "" }),
        messageErreur({ status: 409, code: "perime", detail: "" }),
        messageErreur({ status: 502, code: "hc kill", detail: "terminal occupé" }),
        messageErreur({ status: 0, code: "reseau", detail: "" }),
        messageErreur({ status: 418, code: "inconnue", detail: "" }),
        messageErreur({ status: 400, code: "invalide", detail: "texte d'un serveur d'avant" }),
        messageErreur({ status: 400, code: "invalide", detail: "x", motif: "motif_futur" }),
      ],
      binaire: [erreurBinaire({ bin_motif: "bin_introuvable", bin_params: { nom: "handy" }, bin_erreur: "binaire introuvable : handy" }),
        erreurBinaire({ bin_erreur: "texte d'avant" })],
      avertissement: avertissementVoix({ motif: "modele_refuse", params: { modele: "whisper" }, avertissement: "x" }),
      nomParDefaut: S.nom,
      heure: heureParis("2026-11-15T14:05:00Z"),
    })`);
  },

  async "vu"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1", { state: "done", turn: 3, seen: 2 }), agent("t2", { state: "done", turn: 5, seen: 5 }),
      agent("t3", { state: "done", turn: 4, subagents: [{ id: "a", title: "s", state: "working" }] })];
    S().spaces = SPACES;
    const vus = () => appels.filter((a) => a[1].endsWith("/seen")).map((a) => a[1]);
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const out = { ouvert: vus() };
    run(`surAgents(${JSON.stringify(S().agents)});`); // rafraîchi avant que le label revienne : pas de doublon
    await souffle();
    out.rafraichi = vus();
    run(`ouvrirAgent("t2", null);`); // déjà vu : rien
    await souffle();
    out.dejaVu = vus();
    run(`ouvrirAgent("t3", "a");`); // sous-agent : pas de terminal à marquer
    await souffle();
    out.sous = vus();
    run(`ouvrirAgent("t1", null);`);
    run(`surAgents([${JSON.stringify(agent("t1", { state: "done", turn: 4, seen: 3 }))}]);`); // nouveau tour fini, vue ouverte
    await souffle();
    out.nouveauTour = vus();
    return out;
  },

  // réserve de la revue UI v2 : appui long sur une carte (téléphone)
  async "appui-long"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1", { state: "idle" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    const lien = () => S().liste.querySelector("a.agent");
    const voile = () => !!document.body.querySelector(".voile");
    const doigt = (n, t, x = 0, y = 0) => n.declencher(t, { pointerType: "touch", clientX: x, clientY: y });
    const out = {};
    let l = lien(); // tap court, la carte rafraîchie entre l'appui et le lâcher
    doigt(l, "pointerdown");
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working" }))}]);`);
    out.remplacee = lien() !== l && !l.isConnected;
    doigt(lien(), "pointerup");
    feu();
    out.tapCourt = voile();
    l = lien(); // appui tenu avec un tremblement de 4 px : la confirmation s'ouvre
    doigt(l, "pointerdown", 100, 100); doigt(l, "pointermove", 103, 102); feu();
    out.tremblement = voile();
    run("fermerConfirmation()");
    l = lien(); // le doigt défile de 30 px : pas de confirmation
    doigt(l, "pointerdown", 100, 100); doigt(l, "pointermove", 100, 130); feu();
    out.defilement = voile();
    return out;
  },

  // réserve de la revue UI v2 : la boîte de fermeture suit l'état de l'agent
  async "boite-etat"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1", { state: "idle" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    run('demanderFermeture("t1")');
    const rouge = () => { const r = document.body.querySelector(".alerte-rouge"); return r ? r.textContent : null; };
    const out = { repos: rouge() };
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working" }))}]);`);
    out.travaille = rouge();
    out.ouverte = !!document.body.querySelector(".voile");
    run("surAgents([]);");
    out.partie = !!document.body.querySelector(".voile");
    return out;
  },

  // image collée dans Claude Code : montrée par /api/collee/<empreinte>, jamais une URL fournie
  async "image-collee"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1")];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const cle = "ab".repeat(32);
    run(`ajouterItems(S.vue, ${JSON.stringify([
      { role: "moi", text: "[Image #1] vois", ts: "2026-11-14T20:19:56Z" },
      { role: "moi", text: "", collee: cle, ts: "2026-11-14T20:19:56Z" },
      { role: "moi", text: "", collee: "https://ailleurs.example/x.png", ts: "2026-11-14T20:19:57Z" },
      { role: "moi", text: "", collee: "../../etc", ts: "2026-11-14T20:19:58Z" }])});`);
    return { srcs: v.flux.querySelectorAll("img").map((i) => i.src),
      textes: v.flux.querySelectorAll(".bulle.moi").map((b) => b.textContent) };
  },

  // message tapé par hc : à gauche, signé par l'agent émetteur, jamais par l'utilisateur ; nom affiché lu du serveur
  async "hc-attribution"() {
    reponses = serveurDeBase;
    S().agents = [agent("t4s", { name: "design-reglages" })];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t4s", null);`);
    await souffle();
    const v = S().vue;
    const items = [{ role: "moi", text: "bonjour", ts: "2026-11-15T10:00:00Z" },
      { role: "hc", text: "rapport fini", de: "t4s", nom: "design-reglages", ts: "2026-11-15T10:01:00Z" },
      { role: "hc", text: "alerte", de: "t99", nom: "parti", ts: "2026-11-15T10:02:00Z" },
      { role: "hc", text: "sans émetteur", ts: "2026-11-15T10:03:00Z" }];
    const rendre = () => { run(`nouveauFil(S.vue); ajouterItems(S.vue, ${JSON.stringify(items)});`);
      return { auteurs: v.flux.querySelectorAll(".auteur").map((n) => n.textContent),
        hc: v.flux.querySelectorAll(".msg-hc").map((n) => ({ gauche: n.classList.contains("de-agent"),
          lien: (n.querySelector("a.hc-de") || { href: null }).href, texte: n.querySelector(".msg-agent").textContent })),
        bullesMoi: v.flux.querySelectorAll(".bulle.moi").map((n) => n.textContent) }; };
    const out = { defaut: rendre() };
    run(`appliquerReglagesServeur({ nom_affiche: "Marie Curie" });`);
    out.nomme = rendre();
    run(`appliquerReglagesServeur({ nom_affiche: "   " });`);
    out.vide = rendre().auteurs[0];
    return out;
  },

  // message tapé pendant un tour : grisé sous le fil tant que l'agent ne l'a pas pris
  async "attente"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1")];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const etat = () => ({ textes: v.attente.children.map((x) => x.textContent), cache: !!v.attente.hidden,
      grises: v.attente.querySelectorAll(".en-attente").length, fil: v.flux.querySelectorAll(".bulle.moi").length });
    const out = {};
    const ajoute = (items) => run(`ajouterItems(S.vue, ${JSON.stringify(items)});`);
    ajoute([{ role: "attente", text: "un", ts: "2026-11-14T22:41:12Z" }, { role: "attente", text: "deux", ts: "2026-11-14T22:41:13Z" },
      { role: "attente", text: "un", ts: "2026-11-14T22:41:14Z" }]);
    out.misEnFile = etat();
    ajoute([{ role: "moi", text: "un ", ts: "2026-11-14T22:42:09Z" }]); // livré : apparaît dans le fil
    out.livre = etat();
    ajoute([{ role: "retrait", text: "deux", ts: "2026-11-14T22:42:10Z" }]);
    out.retire = etat();
    ajoute([{ role: "attente", text: "/model opus", ts: "2026-11-14T22:42:30Z" }]); // `/cmd` mis en file…
    out.cmdEnFile = etat();
    ajoute([{ role: "commande", text: "/model opus", ts: "2026-11-14T22:42:40Z" }]); // …puis pris : il part
    out.cmdPris = etat();
    ajoute([{ role: "attente", text: "trois", ts: "2026-11-14T22:43:00Z" }]);
    run(`surAgents([${JSON.stringify(agent("t1", { state: "idle" }))}]);`);
    tic();
    await souffle();
    out.repos = etat();
    return out;
  },

  // I2 : un refresh pendant la saisie du nouveau space garde la valeur, le dossier et le focus
  async "saisie-cote"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1"), agent("t2", { cwd: "/home/user/c" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil();");
    S().liste.querySelectorAll("button").find((b) => b.title === tr("Nouveau space")).click();
    const form = S().liste.querySelector("form.form-space");
    const [nom, dossier] = form.querySelectorAll("input");
    nom.value = "mon space"; dossier.value = "/home/user/zz"; nom.focus();
    const avant = document.activeElement === nom;
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked" }))}, ${JSON.stringify(agent("t3", { cwd: "/home/user/0" }))}]);`);
    tic();
    await souffle();
    const f2 = S().liste.querySelector("form.form-space");
    const vus = f2 ? f2.querySelectorAll("input") : [];
    return { avant, meme: f2 === form, attache: form.isConnected, nom: nom.value, dossier: dossier.value,
      nomVisible: vus[0] ? vus[0].value : null, dossierVisible: vus[1] ? vus[1].value : null,
      focus: document.activeElement === nom, agents: S().liste.querySelectorAll(".bloc-agent").length };
  },

  // points 32, 34 et I2 : le menu « + » de l'en-tête, de l'accueil et de la liste survit aux refresh
  async "menu-plus"() {
    reponses = serveurDeBase;
    S().agents = [agent("t1")];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const out = {};
    const plus = S().panneau.querySelector(".entete .bouton-plus");
    plus.click();
    await souffle();
    const menu = S().panneau.querySelector(".entete .menu-nouveau");
    out.lignes = menu.querySelectorAll(".ligne-menu").map((b) => [b.querySelector(".nom").textContent,
      (b.querySelector(".droite") || { textContent: "" }).textContent, !!b.disabled]);
    out.titres = menu.querySelectorAll(".titre-menu").map((n) => n.textContent);
    out.choixSpaceEntete = !!menu.querySelector("select.choix-space");
    const modele = menu.querySelector("select.choix-modele");
    modele.value = "opus"; modele.focus();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked" }))}, ${JSON.stringify(agent("t4"))}]);`);
    tic(); run("majVueAgent();");
    await souffle();
    out.entete = { ouvert: !menu.hidden, attache: menu.isConnected, meme: menu.querySelector("select.choix-modele") === modele,
      modele: modele.value, focus: document.activeElement === modele };
    // lancer Claude opus dans le space de l'agent ouvert
    reponses = (m, c, b) => (m === "POST" ? { ok: true, result: { terminal: "t7" } } : serveurDeBase(m, c));
    parTexte(menu, ".ligne-menu", "Claude Code").click();
    await souffle();
    out.lanceEntete = appels.filter((a) => a[0] === "POST").pop();
    out.fermeApresLancement = menu.hidden;
    // accueil (pas d'agent ouvert) : le menu demande d'abord le space
    run(`fermerVue(); rendreAccueil();`);
    const plus2 = S().panneau.querySelector(".accueil .bouton-plus");
    plus2.click();
    await souffle();
    const menu2 = S().panneau.querySelector(".accueil .menu-nouveau");
    const choix = menu2.querySelector("select.choix-space");
    out.optionsSpace = choix ? choix.querySelectorAll("option").map((o) => o.textContent) : null;
    choix.value = "sp2"; choix.focus();
    run(`surAgents([${JSON.stringify(agent("t1"))}]);`);
    tic();
    await souffle();
    out.accueil = { ouvert: !menu2.hidden, attache: menu2.isConnected, valeur: choix.value,
      meme: menu2.querySelector("select.choix-space") === choix, focus: document.activeElement === choix };
    parTexte(menu2, ".ligne-menu", "OpenCode").click();
    await souffle();
    out.lanceAccueil = appels.filter((a) => a[0] === "POST").pop();
    const nbPost = appels.filter((a) => a[0] === "POST").length;
    const codex = parTexte(menu2, ".ligne-menu", "Codex");
    if (codex) codex.click();
    out.codexNEnvoieRien = appels.filter((a) => a[0] === "POST").length === nbPost;
    // le « + » de la liste (téléphone) est posé une fois, et le « + » par space a disparu
    const plusListe = S().liste.querySelector(".cote-tete .bouton-plus");
    plusListe.click();
    await souffle();
    const sel = S().liste.querySelector(".cote-tete select.choix-space");
    sel.value = "sp2"; sel.focus();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "idle" }))}]);`);
    tic();
    out.liste = { meme: S().liste.querySelector(".cote-tete select.choix-space") === sel, focus: document.activeElement === sel };
    out.plusParSpace = S().liste.querySelectorAll(".space .mini-plus").length;
    out.plusSpaces = S().liste.querySelectorAll(".section-titre .mini-plus").length;
    return out;
  },

  // m6 : aller au terminal rendu par herdr-app do, pas au premier nouvel agent venu
  async "nouvel-agent"() {
    reponses = (m, c) => (m === "POST" ? { ok: true, result: { tab: { id: "x", panes: [{ id: "p", terminal: "t9" }] } } }
      : serveurDeBase(m, c));
    S().agents = [agent("t1")];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); rendreAccueil();`);
    await ctx.ouvrirDansSpace(SPACES[0], "claude", "opus");
    const post = appels.filter((a) => a[0] === "POST").pop();
    run(`surAgents([${JSON.stringify(agent("t1"))}, ${JSON.stringify(agent("t8"))}, ${JSON.stringify(agent("t9"))}]);`);
    await souffle();
    return { post, hash: ctx.location.hash };
  },

  // m1 : à la reconnexion SSE, fusion sans saut ni repli des outils dépliés
  async fusion() {
    const A = { role: "moi", text: "question", ts: "1" };
    const O1 = { role: "outil", tool: "Bash", text: "Bash: ls", ref: "r1", ts: "2" };
    const R1 = { role: "resultat", text: "a b", ref: "r1", ts: "3" };
    const B = { role: "agent", text: "réponse B", ts: "4" };
    const C = { role: "agent", text: "réponse C", ts: "5" };
    const O2 = { role: "outil", tool: "Read", text: "Read: x", ref: "r2", ts: "6" };
    let items = [A, O1, R1, B];
    reponses = (m, c) => (c.includes("/chat") ? { mode: "chat", items } : serveurDeBase(m, c));
    S().agents = [agent("t1")];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const details = v.flux.querySelector("details.outil");
    details.open = true;
    const runEl = v.flux.querySelector(".run");
    runEl.classList.add("ouvert");
    v.chat.scrollHeight = 5000; v.chat.clientHeight = 500; v.chat.scrollTop = 1000; v.suivi = false;
    run("connecterFlux();");
    const es = ctx.__es;
    es.onopen(); // première ouverture : rien à relire
    items = [O1, R1, B, C, O2]; // fenêtre lue après la coupure
    es.onopen();
    await souffle();
    return { memeDetails: v.flux.querySelector("details.outil") === details && details.isConnected,
      ouvert: details.open === true, runOuvert: runEl.isConnected && runEl.classList.contains("ouvert"),
      scrollTop: v.chat.scrollTop, suivi: v.suivi, nouveaux: v.nouveaux,
      agents: v.flux.querySelectorAll(".msg-agent").length, moi: v.flux.querySelectorAll(".bulle").length,
      outils: v.flux.querySelectorAll("details.outil").length, texteC: v.flux.textContent.includes("réponse C") };
  },

  // point 33 : confirmation qui nomme l'agent, annulation sans envoi, fermeture par l'adresse répétée
  async fermer() {
    reponses = serveurDeBase;
    S().agents = [agent("t1", { name: "maker-1", dir: "herdr-web",
      subagents: [{ id: "s1", title: "Review", state: "working" }] }), agent("t2", { state: "idle", name: "calme" })];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    ctx.location.hash = "#/a/t1";
    await souffle();
    const out = {};
    S().panneau.querySelector(".entete .bouton-fermer").click();
    let voile = document.body.querySelector(".voile");
    out.texte = voile ? voile.textContent : null;
    out.rouge = voile ? (voile.querySelector(".alerte-rouge") || { textContent: "" }).textContent : null;
    out.focusAnnuler = document.activeElement && document.activeElement.textContent === "Annuler";
    parTexte(voile, "button", "Annuler").click();
    out.apresAnnuler = { voile: !!document.body.querySelector(".voile"),
      posts: appels.filter((a) => a[0] === "POST").length };
    // carte de la liste : clic droit, puis échec de hc kill affiché tel quel
    const carte = S().liste.querySelectorAll("a.agent").find((a) => a.textContent.includes("maker-1"));
    const ev = carte.declencher("contextmenu");
    out.clicDroitBloque = ev.defaultPrevented;
    voile = document.body.querySelector(".voile");
    reponses = (m, c) => (m === "POST" ? { __erreur: { status: 502, code: "hc kill", detail: "terminal occupé" } } : serveurDeBase(m, c));
    parTexte(voile, "button", "Fermer").click();
    await souffle();
    out.erreur = (voile.querySelector(".note-cote.erreur") || { textContent: "" }).textContent;
    out.toujoursLa = !!document.body.querySelector(".voile") && ctx.location.hash === "#/a/t1"
      && S().agents.length === 2;
    reponses = (m, c) => (m === "POST" ? { ok: true } : serveurDeBase(m, c));
    parTexte(voile, "button", "Fermer").click();
    await souffle();
    out.post = appels.filter((a) => a[0] === "POST").pop();
    out.apresFermer = { voile: !!document.body.querySelector(".voile"), hash: ctx.location.hash };
    // agent au repos : pas d'alerte rouge ; sous-agent : pas de bouton fermer
    run(`demanderFermeture("t2");`);
    out.rougeRepos = !!document.body.querySelector(".voile .alerte-rouge");
    run(`fermerConfirmation(); ouvrirAgent("t1", "s1");`);
    await souffle();
    out.fermerSousAgent = S().panneau.querySelectorAll(".bouton-fermer").length;
    out.fermerLigneSous = S().liste.querySelectorAll(".sous-agent .fermer-carte, .sous-agent .bouton-fermer").length;
    out.fermerCartes = S().liste.querySelectorAll(".bloc-agent .fermer-carte").length;
    return out;
  },

  // renommer un onglet depuis Spaces : crayon seulement si l'id est connu, modale, corps envoyé
  async renommer() {
    const AVEC_TABS = [{ id: "sp1", name: "sp", folder: "/home/user/a", tabs: [{ id: "tb1", name: "build" }] }];
    const base = (m, c) => (c === "/api/spaces" ? AVEC_TABS : serveurDeBase(m, c));
    reponses = base;
    S().agents = [agent("t1", { space: "sp", tab: "build" }), agent("t2", { space: "sp", tab: "sans-id" })];
    S().spaces = AVEC_TABS;
    run(`squelette(); rendreCote();`);
    const out = {};
    const lignes = S().liste.querySelectorAll(".onglet");
    out.crayons = lignes.map((l) => [l.textContent, l.querySelectorAll(".renommer-onglet").length]);
    S().liste.querySelector(".renommer-onglet").click();
    let voile = document.body.querySelector(".voile");
    const champ = voile.querySelector("input");
    out.prerempli = champ.value;
    out.focusChamp = document.activeElement === champ;
    // le refresh de 15 s ne détruit pas la modale
    run(`rendreCote(true);`);
    out.survitAuRefresh = voile.isConnected;
    voile.querySelector("form").declencher("keydown", { key: "Escape" }); // Échap sur la boîte remonte au voile
    voile.declencher("keydown", { key: "Escape" });
    out.echap = !document.body.querySelector(".voile");
    out.postsApresAnnuler = appels.filter((a) => a[0] === "POST").length;
    // renommer : corps envoyé, liste relue, modale fermée
    S().liste.querySelector(".renommer-onglet").click();
    voile = document.body.querySelector(".voile");
    voile.querySelector("input").value = "  neuf  ";
    reponses = (m, c) => (m === "POST" ? { ok: true } : base(m, c));
    voile.querySelector("form").declencher("submit");
    await souffle();
    out.post = appels.filter((a) => a[0] === "POST").pop();
    out.fermee = !document.body.querySelector(".voile");
    out.releu = appels.filter((a) => a[0] === "GET" && a[1] === "/api/spaces").length >= 1;
    // nom vide accepté ; échec 502 affiché, modale gardée
    S().liste.querySelector(".renommer-onglet").click();
    voile = document.body.querySelector(".voile");
    voile.querySelector("input").value = "";
    reponses = (m, c) => (m === "POST" ? { __erreur: { status: 502, code: "herdr-app", detail: "fenêtre fermée" } } : base(m, c));
    voile.querySelector("form").declencher("submit");
    await souffle();
    out.postVide = appels.filter((a) => a[0] === "POST").pop();
    out.erreur = (voile.querySelector(".note-cote.erreur") || { textContent: "" }).textContent;
    out.toujoursLa = voile.isConnected;
    return out;
  },

  // point 36 : groupes « nom · HH:MM » (Europe/Paris), flèche d'envoi selon le champ, bouton ↓ flottant
  async discord() {
    const items = [
      { role: "moi", text: "q1", ts: "2026-07-14T13:05:00Z" },
      { role: "moi", text: "q2", ts: "2026-07-14T13:06:00Z" },
      { role: "agent", text: "r1", ts: "2026-07-14T13:07:30Z" },
      { role: "outil", tool: "Bash", text: "Bash: ls", ref: "r", ts: "2026-07-14T13:08:00Z" },
      { role: "resultat", text: "ok", ref: "r", ts: "2026-07-14T13:08:01Z" },
      { role: "agent", text: "r2", ts: "2026-07-14T13:09:00Z" },
      { role: "moi", text: "q3", ts: "2026-01-10T23:30:00Z" },
      { role: "agent", text: "r3", ts: "pas une date" },
    ];
    reponses = (m, c) => (c.includes("/chat") ? { mode: "chat", items } : serveurDeBase(m, c));
    S().agents = [agent("t1", { name: "maker-1", model: "claude-opus-5-5" })];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const out = { fuseau: Intl.DateTimeFormat().resolvedOptions().timeZone };
    out.groupes = v.flux.children.map((g) => ({
      classe: g.className,
      avatar: g.querySelectorAll(".avatar").length,
      auteur: (g.querySelector(".groupe-tete .auteur") || { className: "" }).className,
      tete: (g.querySelector(".groupe-tete") || { textContent: null }).textContent,
      messages: g.querySelectorAll(".bulle, .msg-agent").map((n) => n.textContent),
      outils: g.querySelectorAll("details.outil").length }));
    const etatBoutons = () => (v.btnEnvoyer && v.btnMicro ? [v.btnEnvoyer.hidden, v.btnMicro.hidden] : null);
    out.vide = etatBoutons();
    v.champ.value = "bonjour"; v.champ.declencher("input");
    out.texte = etatBoutons();
    v.champ.value = "   "; v.champ.declencher("input");
    out.blancs = etatBoutons();
    v.champ.value = "/mo"; v.champ.declencher("input");
    reponses = (m, c) => (m === "POST" ? { ok: true } : serveurDeBase(m, c));
    v.champ.value = "envoie"; v.champ.declencher("input");
    await ctx.envoyerTexte();
    out.apresEnvoi = etatBoutons();
    out.boutonBas = document.body.querySelectorAll(".bouton-bas").length + (v.bas ? 1 : 0);
    v.chat.scrollHeight = 5000; v.chat.clientHeight = 500; v.chat.scrollTop = 100;
    v.chat.declencher("scroll");
    out.liveRemonte = v.live.textContent;
    out.boutonBasRemonte = document.body.querySelectorAll(".bouton-bas").length;
    run("rendreBarre();");
    out.barre = S().barre.querySelectorAll(".seg").map((n) => n.className);
    run(`S.valsBarre = { cpu: "◔ CPU 9%", mem: "◑ MEM 3,0G", quota: null, models: {} }; rendreBarre();`);
    out.barreSansQuota = S().barre.querySelectorAll(".seg").map((n) => n.className);
    return out;
  },

  // point 38 : pincer (deux pointeurs tactiles) et Ctrl + molette changent la taille du texte du fil
  async pincement() {
    const items = Array.from({ length: 30 }, (_, i) => ({ role: i % 2 ? "agent" : "moi", text: "m" + i, ts: "" }));
    reponses = (m, c) => (c.includes("/chat") ? { mode: "chat", items } : serveurDeBase(m, c));
    S().agents = [agent("t1"), agent("t2", { program: "zsh", cols: 80 })];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    let v = S().vue;
    const out = { depart: v.chat.style.getPropertyValue("--taille-fil"), stockeAvant: stockage.get("herdr.taille-fil") || null };
    const p = (type, id, x, y, extra = {}) => v.chat.declencher(type, Object.assign({ pointerType: "touch", pointerId: id, clientX: x, clientY: y }, extra));
    const pince = (d0, d1) => { p("pointerdown", 1, 100, 100, { isPrimary: true }); p("pointerdown", 2, 100, 100 + d0);
      p("pointermove", 2, 100, 100 + d1); p("pointerup", 1, 100, 100); p("pointerup", 2, 100, 100 + d1); };
    v.chat.scrollHeight = 5000; v.chat.clientHeight = 500;
    pince(100, 300);  // ×3 : borné à 26
    out.ecarte = [v.chat.style.getPropertyValue("--taille-fil"), stockage.get("herdr.taille-fil"), v.chat.scrollTop];
    pince(100, 10);  // ÷10 : borné à 11
    out.serre = [v.chat.style.getPropertyValue("--taille-fil"), stockage.get("herdr.taille-fil")];
    pince(100, 130);  // 11 × 1,3 = 14,3 : arrondi au demi-pixel
    out.demi = v.chat.style.getPropertyValue("--taille-fil");
    pince(100, 102);  // sous le seuil de 4 % : rien ne bouge
    out.tremble = v.chat.style.getPropertyValue("--taille-fil");
    p("pointerdown", 7, 0, 0, { pointerType: "mouse" }); p("pointerdown", 8, 0, 100, { pointerType: "mouse" });
    p("pointermove", 8, 0, 400, { pointerType: "mouse" });
    out.souris = v.chat.style.getPropertyValue("--taille-fil"); // la souris ne pince pas
    // réouverture : la taille est mémorisée par appareil
    run(`ouvrirAgent("t1", null);`);
    await souffle();
    v = S().vue;
    out.rouvert = v.chat.style.getPropertyValue("--taille-fil");
    // double tape à deux doigts : retour au défaut, mémoire effacée
    for (let i = 0; i < 2; i++) { p("pointerdown", 1, 50, 50, { isPrimary: true }); p("pointerdown", 2, 50, 150); p("pointerup", 1, 50, 50); p("pointerup", 2, 50, 150); }
    out.doubleTape = [v.chat.style.getPropertyValue("--taille-fil"), stockage.has("herdr.taille-fil")];
    // doigt fantôme : un pointerup perdu (nœud remplacé), puis un défilement à UN doigt
    p("pointerdown", 41, 100, 100, { isPrimary: true }); // son pointerup n'arrivera jamais
    p("pointerdown", 42, 100, 100, { isPrimary: true }); p("pointermove", 42, 100, 400); p("pointerup", 42, 100, 400);
    out.fantome = v.chat.style.getPropertyValue("--taille-fil");
    // pointercancel au milieu d'un pincement : tout est purgé, le doigt suivant défile sans redimensionner
    p("pointerdown", 51, 100, 100, { isPrimary: true }); p("pointerdown", 52, 100, 200);
    p("pointercancel", 51, 100, 100);
    p("pointermove", 52, 100, 600);
    out.annule = v.chat.style.getPropertyValue("--taille-fil");
    // PC : Ctrl + molette, petits pas d'un pavé tactile qui s'accumulent ; sans Ctrl, rien
    const sansCtrl = v.chat.declencher("wheel", { deltaY: -100, ctrlKey: false, clientX: 10, clientY: 10 });
    out.molette = [v.chat.style.getPropertyValue("--taille-fil"), sansCtrl.defaultPrevented];
    let e;
    for (let i = 0; i < 20; i++) e = v.chat.declencher("wheel", { deltaY: -10, ctrlKey: true, clientX: 10, clientY: 10 });
    out.ctrlMolette = [v.chat.style.getPropertyValue("--taille-fil"), e.defaultPrevented, stockage.get("herdr.taille-fil")];
    // suivi Live : un pincement garde le bas
    v.suivi = true; v.chat.scrollTop = 0; v.chat.scrollHeight = 5000; v.chat.clientHeight = 500;
    pince(100, 150);
    out.suiviGarde = [v.chat.scrollTop, v.suivi];
    // terminal : part de sa taille ajustée, le pincement la remplace jusqu'à réouverture, sans mémoire
    reponses = (m, c) => (c.includes("/chat") ? { mode: "brut", screen: "$ ls\nfichier", note: null } : serveurDeBase(m, c));
    const avantTerm = stockage.get("herdr.taille-fil");
    run(`ouvrirAgent("t2", null);`);
    await souffle();
    v = S().vue;
    const pre = () => v.flux.querySelector("pre.brut");
    out.termDepart = pre().style.fontSize;
    pince(100, 200);
    out.termPince = [pre().style.fontSize, stockage.get("herdr.taille-fil") === avantTerm];
    run("ajusterPolice(S.vue);");
    out.termGarde = pre().style.fontSize;
    run(`ouvrirAgent("t2", null);`);
    await souffle();
    v = S().vue;
    out.termRouvert = pre().style.fontSize;
    return out;
  },

  // agents hc lancés depuis un agent : rangés sous leur parent, famille dans son groupe le plus urgent
  async enfants() {
    reponses = serveurDeBase;
    const e = (addr, parent, state, plus = {}) => agent(addr, Object.assign({ name: addr, state, parent }, plus));
    S().agents = [
      e("p", null, "working"), e("c1", "p", "done"), e("c2", "p", "idle", { name: "c2" }), e("g", "c1", "done"),
      e("o", "absent", "done"),                                   // parent fermé : reste dans son groupe
      e("ca", "cb", "idle"), e("cb", "ca", "idle"),               // cycle de données a -> b -> a
      e("q", null, "idle"), e("r", "q", "blocked", { msg: "ok ?" }), // enfant bloqué : la famille remonte
      e("z", null, "done"),
    ];
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    const nom = (b) => b.querySelector(".nom").textContent;
    const arbre = (b) => {
      const kids = b.children.find((c) => c.classList.contains("enfants"));
      return { nom: nom(b), enfants: kids ? kids.children.map(arbre) : [] };
    };
    const groupes = [];
    for (const n of S().cotes.agents.children) {
      if (n.classList.contains("groupe-tete")) groupes.push({ titre: n.textContent, blocs: [] });
      else if (n.classList.contains("bloc-agent")) groupes.at(-1).blocs.push(arbre(n));
    }
    const out = { groupes, total: S().liste.querySelectorAll(".bloc-agent").length, compte: S().cotes.compte.textContent };
    const enf = (x) => S().liste.querySelectorAll(".enfant-hc").find((n) => nom(n) === x);
    out.hrefG = enf("g")?.querySelector("a.agent").href ?? null;
    out.dernierC2 = enf("c2")?.classList.contains("dernier") ?? null;
    out.dernierC1 = enf("c1")?.classList.contains("dernier") ?? null;
    out.msgR = enf("r")?.querySelector(".msg").textContent ?? null;
    out.statutR = enf("r")?.querySelector(".statut")?.textContent ?? null;
    out.croix = enf("g")?.querySelectorAll(".fermer-carte").length ?? 0;
    run(`demanderFermeture = (a) => { window.__ferme = a; }; `);
    enf("g")?.querySelector("a.agent").declencher("contextmenu");
    out.fermeture = run("window.__ferme || null");
    run(`ouvrirAgent("g", null);`);
    await souffle();
    run("rendreCote();");
    out.actif = S().liste.querySelectorAll("a.agent.actif").map((n) => n.querySelector(".nom").textContent);
    return out;
  },

  // chaîne n0 > n1 > ... > n6 : au plus 4 niveaux rangés sous la racine, le reste à plat
  async profondeur() {
    reponses = serveurDeBase;
    S().agents = [0, 1, 2, 3, 4, 5, 6].map((i) => agent("n" + i, { name: "n" + i, state: "working", parent: i ? "n" + (i - 1) : null }));
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    const racines = S().cotes.agents.children.filter((n) => n.classList.contains("bloc-agent"));
    const prof = (b) => { const k = b.children.find((c) => c.classList.contains("enfants")); return k ? 1 + Math.max(...k.children.map(prof)) : 0; };
    return { total: S().liste.querySelectorAll(".bloc-agent").length,
      racines: racines.map((b) => b.querySelector(".nom").textContent), niveaux: Math.max(...racines.map(prof)) };
  },

  // Claude sans rôle qui dort : la structure « dort » n'existe qu'en idle
  async sommeil() {
    reponses = serveurDeBase;
    const f = (a) => { const i = ctx.iconeRole(a); return i ? { tag: i.tagName.toLowerCase(), classe: i.className,
      zs: i.querySelectorAll(".z").length, perso: i.tagName === "IMG" ? 1 : i.querySelectorAll("img").length,
      titre: i.title || null } : null; };
    const out = {};
    for (const etat of ["idle", "working", "done", "blocked", "error", null]) {
      out[String(etat)] = f({ state: etat, program: "claude-code" });
    }
    out.roleClaudeExplicite = f({ role: "claude", state: "idle", program: "claude-code" });
    out.maker = f({ role: "maker", state: "idle", program: "claude-code" });
    out.terminal = f({ role: "terminal", state: "idle", program: "zsh" });
    out.sous = ((i) => (i ? i.className : null))(ctx.iconeSous({ role: null, state: "idle" }));
    return out;
  },

  // Claude idle dont la famille (enfants hc, sous-agents de chacun) travaille : il ne dort pas
  async sommeilFamille() {
    reponses = serveurDeBase;
    const rendu = (liste, cible) => {
      const i = ctx.iconeRole(liste.find((x) => x.address === cible), ctx.famillesHc(liste));
      return { tag: i.tagName.toLowerCase(), classe: i.className, zs: i.querySelectorAll(".z").length, titre: i.querySelector("title") ? i.querySelector("title").textContent : i.title };
    };
    const sa = (id, state) => ({ id, title: "x", state, role: null, role_infere: false });
    const out = {};
    out.enfantWorking = rendu([agent("p", { state: "idle" }), agent("e", { state: "working", parent: "p" })], "p");
    out.enfantBlocked = rendu([agent("p", { state: "idle" }), agent("e", { state: "blocked", parent: "p" })], "p");
    out.sousAgentDEnfant = rendu([agent("p", { state: "idle" }), agent("e", { state: "idle", parent: "p", subagents: [sa("s", "working")] })], "p");
    out.sousAgentPropre = rendu([agent("p", { state: "idle", subagents: [sa("s", "working")] })], "p");
    out.petitEnfant = rendu([agent("p", { state: "idle" }), agent("e", { state: "idle", parent: "p" }), agent("g", { state: "working", parent: "e" })], "p");
    out.familleIdle = rendu([agent("p", { state: "idle" }), agent("e", { state: "idle", parent: "p", subagents: [sa("s", "done")] })], "p");
    out.familleDoneEtIdle = rendu([agent("p", { state: "idle" }), agent("e", { state: "done", parent: "p" })], "p");
    out.sansFamille = rendu([agent("p", { state: "idle" })], "p");
    return out;
  },

  // m5, m7, sous-agents finis masqués, rôle déduit des sous-agents (point 35)
  async divers() {
    reponses = serveurDeBase;
    const classes = (a) => { const i = ctx.iconeRole(a); return i ? i.className : null; };
    const out = {
      revueTravaille: classes({ role: "reviewer", state: "working" }),
      revueBloquee: classes({ role: "reviewer", state: "blocked" }),
      revueRepos: classes({ role: "reviewer", state: "idle" }),
      revueShell: classes({ role: "reviewer", state: "working", activity: "shell" }),
      revueFormes: ((i) => (i ? i.querySelectorAll("path").map((n) => n.getAttribute("class")) : null))(
        ctx.iconeRole({ role: "reviewer", state: "working" })),
      shellBloque: classes({ activity: "shell", state: "blocked", program: "claude-code" }),
      shellTravaille: classes({ activity: "shell", state: "working", program: "claude-code" }),
      shellRepos: classes({ activity: "shell", state: "idle", program: "zsh" }),
    };
    const b = ctx.bulle({ role: "moi", image: "/home/user/x.png", text: "" });
    out.rel = b.querySelector("a").rel;
    S().agents = [agent("t1", { subagents: [
      { id: "a", title: "Implement herdr-web", state: "working", role: "maker", role_infere: true },
      { id: "b", title: "Review", state: "done", role: "checker", role_infere: true },
      { id: "c", title: "Explore", state: "idle", role: null, role_infere: false },
      { id: "d", title: "Check the diff", state: "blocked", role: "checker", role_infere: true },
      { id: "e", title: "Explore more", state: "working", role: null, role_infere: false },
      { id: "f", title: "Review the diff", state: "working", role: "reviewer", role_infere: true }] })];
    run(`squelette(); rendreCote();`);
    out.sousVisibles = S().liste.querySelectorAll(".sous-agent").map((n) => n.querySelector(".nom").textContent);
    out.iconesSous = S().liste.querySelectorAll(".sous-agent").map((n) => {
      const i = n.querySelector("svg.role"); return i ? i.className : null; });
    run(`ouvrirAgent("t1", null);`);
    out.pastilles = S().panneau.querySelectorAll(".pastille-sous").map((n) => {
      const i = n.querySelector("svg.role"); return i ? i.className : null; });
    return out;
  },

  // logo d'OS de la 3e ligne (logoOs) : fichier choisi par la clé `os`, PNG pour glfos, repli générique,
  // clé hostile refusée, badge Windows sous WSL
  async logosOs() {
    reponses = serveurDeBase;
    const CAS = { a1: ["ubuntu", false], a2: ["glfos", false], a3: ["", false], a4: [undefined, false], a5: ["debian", true],
      a6: ["../x\"><b", false], a7: ["quantum", false], a8: ["linux", false] };
    S().agents = Object.entries(CAS).map(([a, [os, wsl]]) => agent(a, { name: a, state: "idle", role: "claude", machine: "m", os, os_wsl: wsl }));
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    await souffle();
    const out = {};
    for (const n of S().liste.querySelectorAll("a.agent")) {
      const l3 = n.querySelector(".l3");
      const quoi = (x) => [x.tagName.toLowerCase(), x.className, x.src || null].concat(x.tagName.toLowerCase() === "span" ? [x.children.map(quoi)] : []);
      out[n.querySelector(".nom").textContent] = l3 ? l3.children.map(quoi) : null;
    }
    return out;
  },

  // harmonie des zones Agents (spec du 2026-11-15) : sections, tri, plis, épingle, statut à gauche,
  // icônes, badge fable, enfants hc, sous-agents
  async harmonie() {
    const now = Date.now();
    reponses = serveurDeBase;
    const e = (addr, state, plus = {}) => agent(addr, Object.assign({ name: addr, state, role: "claude" }, plus));
    S().agents = [
      e("w1", "working", { since_ms: now - 600e3 }),
      e("w2", "working", { since_ms: now - 60e3, subagents: [
        { id: "s1", title: "Review the diff", state: "working", model: "claude-opus-5-5", context: 5000,
          context_max: 100000, role: "reviewer" },
        { id: "s2", title: "Explore", state: "done", model: null, role: null }] }),
      e("pin", "idle", { pinned: true, since_ms: now - 10e3 }),
      e("pinb", "blocked", { pinned: true, since_ms: now - 999e3, msg: "ok ?" }),
      e("b1", "blocked", { msg: "Allow?" }),
      e("d1", "done", { model: "claude-fable-5-2" }),
      e("i1", "idle", { space: "autre" }),
      e("er", "error"),
      e("term", "unknown", { program: "zsh", role: "terminal" }),
      e("k1", "working", { parent: "w1", branch: "main", dir: "proj › wt", since_ms: now - 120e3, machine: "my-laptop" }),
      e("k2", "idle", { parent: "k1" }),
      // famille « working » par son enfant, mais lui-même idle : après les working malgré son changement récent
      e("ip", "idle", { since_ms: now - 1e3 }), e("kw", "working", { parent: "ip" }),
    ];
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    await souffle();
    const tetes = () => S().cotes.agents.children.filter((n) => n.classList.contains("groupe-tete"));
    const plan = () => { // [clé de section, [noms des lignes racines]]
      const out = [];
      for (const n of S().cotes.agents.children) {
        if (n.classList.contains("groupe-tete")) out.push([n.dataset.cle || null, []]);
        else if (n.classList.contains("bloc-agent")) out.at(-1)[1].push(n.querySelector(".nom").textContent);
      }
      return out;
    };
    const ligne = (nom) => S().liste.querySelectorAll("a.agent").find((n) => n.querySelector(".nom").textContent === nom) || null;
    const statut = (nom) => { const s = ligne(nom)?.querySelector(".l1").children[0];
      return s ? [s.className, s.textContent] : null; };
    const icone = (nom) => { const i = ligne(nom)?.querySelector(".l1").children[1];
      return i ? [i.tagName.toLowerCase(), i.className, i.src || null] : null; };
    const out = { priorite: plan() };
    out.titres = tetes().map((n) => n.textContent);
    out.statuts = Object.fromEntries(["w2", "b1", "pinb", "i1", "d1", "er", "term"].map((n) => [n, statut(n)]));
    out.icones = Object.fromEntries(["w2", "i1", "term", "k1"].map((n) => [n, icone(n)]));
    out.badgeFable = [ligne("d1").querySelector(".modele").className, ligne("d1").querySelector(".modele").textContent];
    const k1 = S().liste.querySelectorAll(".enfant-hc").find((n) => n.querySelector(".nom").textContent === "k1");
    out.enfant = { depuis: k1.querySelector(".depuis")?.textContent ?? null, l2: k1.querySelector(".l2")?.textContent ?? null,
      l3: !!k1.querySelector("a.agent").querySelector(".l3"),
      petit: k1.querySelector(".enfants")?.querySelector(".nom").textContent ?? null };
    const sous = S().liste.querySelectorAll(".sous-agent");
    out.sous = sous.map((n) => n.children.map((c) => c.className.split(" ")[0] || c.tagName.toLowerCase()));
    // plier un groupe : ses lignes disparaissent, la tête garde le compte ; mémorisé dans l'appareil
    tetes().find((n) => n.dataset.cle === "sec-working").click();
    out.groupePlie = { plan: plan().find(([c]) => c === "sec-working"), memo: stockage.get("herdr.plis") || null,
      chevron: !!tetes().find((n) => n.dataset.cle === "sec-working").querySelector(".chevron") };
    tetes().find((n) => n.dataset.cle === "sec-working").click();
    // plier un agent : ses enfants hc disparaissent, le chevron dit combien
    const plier = () => S().liste.querySelectorAll(".bloc-agent").find((b) => b.querySelector(".nom").textContent === "w1").querySelector(".plier");
    out.chevronOuvert = plier() ? plier().textContent : null;
    plier()?.declencher("click", { stopPropagation() {} });
    out.agentPlie = { k1: S().liste.querySelectorAll(".enfant-hc").some((n) => n.querySelector(".nom").textContent === "k1"),
      compte: plier()?.textContent ?? null,
      memo: stockage.get("herdr.plis") || null };
    plier()?.declencher("click", { stopPropagation() {} });
    // épingler / désépingler : la route du serveur, l'inverse de l'état courant
    appels.length = 0;
    ligne("i1")?.parentNode.querySelector(".epingler")?.declencher("click", { stopPropagation() {} });
    ligne("pin")?.parentNode.querySelector(".epingler")?.declencher("click", { stopPropagation() {} });
    await souffle();
    out.epingles = appels.filter(([m, c]) => m === "POST" && c.endsWith("/pin"));
    // menu de tri : survit à un rafraîchissement, coche le mode, mémorise le choix
    const boutonTri = () => S().cotes.agents.querySelector(".bouton-tri");
    boutonTri()?.click();
    run("rendreCote();");
    const menu = () => S().cotes.agents.querySelector(".menu-tri");
    out.menu = menu() ? { lignes: menu().querySelectorAll(".ligne-menu").map((n) => n.textContent),
      coche: menu().querySelectorAll(".ligne-menu").findIndex((n) => !!n.querySelector(".coche")) } : null;
    menu()?.querySelectorAll(".ligne-menu").find((n) => n.textContent.includes("Grouped"))?.click();
    out.apresChoix = { menu: !!menu() && !menu().hidden, memo: stockage.get("herdr.tri-agents") || null };
    out.groupe = plan();
    out.statutGroupeB1 = statut("b1");
    return out;
  },

  // M1/M2 du checker de la PR #24 : l'épingle n'existe que sur une racine (bouton au survol et boîte de
  // l'appui long) ; un agent épinglé est toujours une racine, même s'il porte un `parent`
  async "epingle-racine"() {
    reponses = serveurDeBase;
    S().agents = [agent("r", { state: "working" }), agent("c", { state: "idle", parent: "r" }),
      agent("g", { state: "idle", parent: "c" }), agent("p", { state: "idle", parent: "r", pinned: true })];
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    await souffle();
    const nom = (b) => b.querySelector(".nom").textContent;
    const blocs = () => S().liste.querySelectorAll(".bloc-agent");
    const out = { avecEpingle: blocs().filter((b) => b.querySelector(".epingler")).map(nom),
      enfants: blocs().filter((b) => b.classList.contains("enfant-hc")).map(nom),
      racinePinned: S().cotes.agents.children.filter((n) => n.classList.contains("bloc-agent")).map(nom) };
    out.boite = {};
    for (const a of ["r", "c", "g", "p"]) {
      run(`demanderFermeture("${a}");`);
      out.boite[a] = document.body.querySelector(".voile").querySelectorAll("button").map((b) => b.textContent);
      run("fermerConfirmation();");
    }
    return out;
  },

  // complément PR #24 : racine idle, enfant hc idle dont un sous-agent tourne -> la famille va en Working
  async "urgence-sous-enfant"() {
    reponses = serveurDeBase;
    S().agents = [agent("r", { state: "idle" }),
      agent("c", { state: "idle", parent: "r", subagents: [{ id: "s1", title: "Review", state: "working", model: null, role: null }] })];
    S().spaces = SPACES;
    run("squelette(); rendreCote();");
    await souffle();
    const out = [];
    for (const n of S().cotes.agents.children) {
      if (n.classList.contains("groupe-tete")) out.push([n.dataset.cle || null, []]);
      else if (n.classList.contains("bloc-agent")) out.at(-1)[1].push(n.querySelector(".nom").textContent);
    }
    return { plan: out };
  },

  // AskUserQuestion à plusieurs questions (capture de l'utilisateur, 2026-11-15) : l'agent reste « blocked »
  // d'une question à l'autre et le core n'émet rien ; la page relit l'écran après sa réponse, sans
  // remontrer la question déjà répondue tant que l'écran n'a pas tourné.
  async "askuser-multi"() {
    const Q = (contexte, ...labels) => ({ contexte, choices: labels.map((l, i) => ({ digit: String(i + 1), label: l, detail: "" })) });
    const ecrans = [Q("←  ☐ Couleur  ☐ Taille  ✔ Submit  →\nQuelle couleur ?", "Rouge", "Vert"),
      Q("←  ☒ Couleur  ☐ Taille  ✔ Submit  →\nQuelle taille ?", "Petit", "Grand"),
      Q("←  ☒ Couleur  ☒ Taille  ✔ Submit  →\nReady to submit your answers?", "Submit answers", "Cancel"),
      Q("")];
    let i = 0, retard = 0; // retard : lectures qui rendent encore l'écran d'avant la touche
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) {
        const e = ecrans[retard > 0 ? i - 1 : i];
        if (retard > 0) retard--;
        return { msg: "Claude needs your input", kind: null, menu: null, ...e };
      }
      if (chemin.endsWith("/answer")) { i++; retard = 1; return { ok: true }; }
      return serveurDeBase(m, chemin);
    };
    S().agents = [agent("t1", { state: "blocked", msg: "Claude needs your input" })];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const boutons = () => (v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent));
    const etape = async () => { feu(); await souffle(); };
    const out = { q1: boutons(), contexte1: v.carte.textContent.includes("Quelle couleur ?") };
    parTexte(v.carte, "button", "Rouge").click();
    await souffle();
    await etape(); // 1re relecture : l'écran n'a pas encore tourné
    out.pendant = boutons();
    await etape();
    out.q2 = boutons();
    out.contexte2 = v.carte.textContent.includes("Quelle taille ?");
    if (out.q2.length) {
      parTexte(v.carte, "button", "Petit")?.click();
      await souffle(); await etape(); await etape();
    }
    out.submit = boutons();
    if (out.submit.length) {
      parTexte(v.carte, "button", "Submit answers")?.click();
      await souffle();
      for (let k = 0; k < 15; k++) await etape();
    }
    out.fin = boutons();
    out.finNote = !v.carte.hidden && v.carte.textContent.includes("Aucune option");
    out.posts = appels.filter((a) => a[0] === "POST").map((a) => a[2].digit);
    return out;
  },

  // « Type something. » d'un AskUserQuestion (brief appairage, 2026-11-15) : la cliquer ne répondait
  // rien (le chiffre seul pose le curseur sur le champ du TUI). Elle ouvre un champ ; le texte part
  // avec le chiffre ; la question suivante vient ensuite comme après une autre option.
  async "saisie-libre"() {
    const Q = (contexte, ...labels) => ({ contexte, choices: labels.map((l, i) => ({ digit: String(i + 1), label: l, detail: "" })) });
    const ecrans = [Q("←  ☐ Couleur  ☐ Taille  ✔ Submit  →\nQuelle couleur ?", "Rouge", "Vert", "Type something."),
      Q("←  ☒ Couleur  ☐ Taille  ✔ Submit  →\nQuelle taille ?", "Petit", "Grand", "Type something."), Q("")];
    let i = 0, retard = 0;
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) {
        const e = ecrans[retard > 0 ? i - 1 : i];
        if (retard > 0) retard--;
        return { msg: "Claude needs your input", kind: null, menu: null, ...e };
      }
      if (chemin.endsWith("/answer")) { i++; retard = 1; return { ok: true }; }
      return serveurDeBase(m, chemin);
    };
    S().agents = [agent("t1", { state: "blocked", msg: "Claude needs your input" })];
    S().spaces = SPACES;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const boutons = () => (v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent));
    const posts = () => appels.filter((a) => a[0] === "POST").map((a) => a[2]);
    const etape = async () => { feu(); await souffle(); };
    const out = { q1: boutons() };
    parTexte(v.carte, "button", "Type something.").click();
    await souffle();
    out.postsApresClic = posts().length; // rien ne part avant le texte
    const champ = () => v.carte.querySelector(".saisie-libre input");
    out.champ = !!champ();
    out.focus = !!champ() && document.activeElement === champ();
    if (champ()) { champ().value = "Bleu canard, mat"; champ().declencher("input"); }
    run("chargerChoix()"); // un événement reconstruit la carte pendant la saisie : le texte reste
    await souffle();
    out.texteGarde = champ() ? champ().value : null;
    if (champ()) v.carte.querySelector(".saisie-libre").declencher("submit");
    await souffle();
    out.corps = posts();
    out.cacheeApres = v.carte.hidden;
    await etape(); await etape();
    out.q2 = boutons();
    out.contexte2 = v.carte.textContent.includes("Quelle taille ?");
    out.champQ2 = !!champ(); // la saisie de Q1 ne réapparaît pas sur Q2
    if (out.q2.length) { parTexte(v.carte, "button", "Petit").click(); await souffle(); }
    out.corpsQ2 = posts().slice(1);
    return out;
  },

  // question à cases (multiSelect) : « [ ] Type something » (sans point) ouvre aussi le champ ; une
  // case cochée d'abord par la page reste cochée (l'état est celui du TUI, relu après chaque réponse)
  async "saisie-libre-cases"() {
    const Q = (...labels) => ({ contexte: "←  ☐ Langages  ✔ Submit  →\nQuels langages ?",
      choices: labels.map((l, i) => ({ digit: String(i + 1), label: l, detail: "" })) });
    const ecrans = [Q("[ ] Python", "[ ] Rust", "[ ] Type something"), Q("[✔] Python", "[ ] Rust", "[ ] Type something"),
      Q("[✔] Python", "[ ] Rust", "[✔] Zig")];
    let i = 0, retard = 0;
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) {
        const e = ecrans[retard > 0 ? i - 1 : i];
        if (retard > 0) retard--;
        return { msg: "m", kind: null, menu: null, ...e };
      }
      if (chemin.endsWith("/answer")) { i++; retard = 1; return { ok: true }; }
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    const boutons = () => (v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent));
    const etape = async () => { feu(); await souffle(); };
    const out = { avant: boutons() };
    parTexte(v.carte, "button", "Python").click();
    await souffle(); await etape(); await etape();
    out.python = boutons();
    parTexte(v.carte, "button", "Type something").click();
    await souffle();
    const champ = v.carte.querySelector(".saisie-libre input");
    out.champ = !!champ;
    if (champ) { champ.value = "Zig"; champ.declencher("input"); v.carte.querySelector(".saisie-libre").declencher("submit"); }
    await souffle(); await etape(); await etape();
    out.apres = boutons();
    out.corps = appels.filter((a) => a[0] === "POST").map((a) => ({ digit: a[2].digit, texte: a[2].texte }));
    return out;
  },

  // AskUserQuestion à aperçu (capture de l'utilisateur, 2026-11-15) : boutons au libellé propre, aperçu de
  // chaque option montré (sous l'option, et dans un panneau qui suit le survol / le focus)
  async "apercu"() {
    const P1 = "+-----+\n| > V |\n+-----+", P2 = "+---+\n| C |\n+---+";
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) return { msg: "m", kind: null, menu: null, contexte: "Quelle mise en page ?",
        choices: [{ digit: "1", label: "Onglets", detail: "Rubriques à gauche", preview: P1 },
          { digit: "2", label: "Cartes", detail: "Une colonne", preview: P2 }] };
      if (chemin.endsWith("/answer")) return { ok: true };
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    const out = { boutons: v.carte.querySelectorAll("button").map((b) => b.textContent) };
    out.apercus = v.carte.querySelectorAll("pre.apercu").map((p) => p.textContent);
    const panneau = v.carte.querySelector("pre.apercu-panneau");
    out.panneau = panneau ? panneau.textContent : null;
    parTexte(v.carte, "button", "Cartes").declencher("mouseenter");
    out.survol = panneau ? panneau.textContent : null;
    parTexte(v.carte, "button", "Onglets").declencher("focus");
    out.focus = panneau ? panneau.textContent : null;
    out.ordre = v.carte.children.map((n) => n.tagName.toLowerCase() + (n.className ? "." + n.className.split(" ")[0] : ""));
    out.fautes = fautes();
    parTexte(v.carte, "button", "Cartes").click();
    await souffle();
    out.corps = appels.filter((a) => a[0] === "POST").map((a) => a[2]);
    return out;
  },

  // roue crantée : « Connecter un appareil » (brief appairage, 2026-11-15) — QR code + lien, sans jeton
  async "appairage"() {
    const LIEN = "https://herdr.example.org:10370/appairer/Ab-_9xYz0123456789abcdefghijKLMN";
    let echec = null;
    reponses = (m, chemin) => {
      if (chemin === "/api/reglages") return { handy: null, version: {}, session: {} };
      if (chemin === "/api/appairage") return echec || { lien: LIEN, expire_s: 300 };
      return serveurDeBase(m, chemin);
    };
    S().agents = [agent("t1", { state: "idle" })];
    S().spaces = SPACES;
    run("squelette(); rendreCote(); rendreAccueil(); ouvrirReglages();");
    await souffle();
    const boite = () => document.body.querySelector(".boite.reglages");
    const out = {};
    const bouton = () => boite().querySelectorAll("button").find((b) => [tr("Connecter un appareil"), tr("Nouveau lien")].includes(b.textContent));
    out.bouton = !!bouton();
    out.avant = appels.filter((a) => a[1] === "/api/appairage").length;
    bouton().click();
    await souffle();
    out.appels = appels.filter((a) => a[1] === "/api/appairage").map((a) => a[0]);
    const zone = boite().querySelector(".appairage");
    const qr = zone.querySelector(".qr");
    out.qr = !!qr;
    const chemin = qr ? qr.querySelector("path").getAttribute("d") : "";
    // le dessin est exactement la matrice du lien (le décodage réel est dans test_appairage.py)
    out.dessinDuLien = chemin === run(`(() => { const m = HerdrQR.matrice(${JSON.stringify(LIEN)}); let d = "";
      for (let l = 0; l < m.taille; l++) for (let c = 0; c < m.taille; c++) if (m.noir(l, c)) d += "M" + (c + 4) + " " + (l + 4) + "h1v1h-1z";
      return d; })()`);
    out.lien = zone.querySelector(".lien-appairage") ? zone.querySelector(".lien-appairage").textContent : null;
    out.note = zone.textContent.includes("Une seule fois");
    out.fautes = fautes();
    feu(); // 5 min plus tard : le lien affiché est retiré
    out.expire = zone.textContent;
    out.qrApres = !!zone.querySelector(".qr");
    echec = { __erreur: { status: 401, code: "jeton", detail: "" } };
    bouton().click();
    await souffle();
    out.apres401 = zone.textContent;
    return out;
  },

  // Réserve D1 de la PR #22 : après la réponse, /choices échoue (core indisponible) ; la relecture
  // doit s'arrêter au plafond quand même, sans minuterie vivante.
  // palette de commandes et Ctrl+L (brief palette, 2026-11-15) : clavier, exécution, refus en blocked, réglages
  async "palette"() {
    let etatT1 = "idle";
    reponses = (m, chemin, corps) => {
      if (chemin === "/api/reglages") return { version: { commit: "abc", cache: "herdr-coquille-v31" } };
      if (chemin.endsWith("/commands")) return [{ name: "model", description: "Changer de modèle" }, { name: "compact" }];
      if (chemin.endsWith("/choices")) return { msg: "", choices: [] };
      return serveurDeBase(m, chemin);
    };
    S().agents = [agent("t1", { state: "idle", role: "concepteur" }), agent("t2", { name: "zsh", program: "zsh", state: null, role: "terminal" }),
      agent("t3", { name: "relecteur", state: "idle", role: "checker", space: "autre" })];
    S().spaces = SPACES;
    S().connus = CONNUS;
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const touche = (key, mods = {}) => {
      const e = Object.assign({ key, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.arrete = true; } }, mods);
      for (const f of document.ecouteurs.keydown || []) f(e);
      return e;
    };
    const pal = () => document.body.querySelector(".boite.palette");
    const champ = () => pal() && pal().querySelector(".palette-champ");
    const selTitre = () => pal().querySelector(".palette-entree.sel .palette-titre").textContent;
    const keys = () => appels.filter((a) => a[1].endsWith("/keys")).map((a) => [a[1], a[2]]);
    const out = {};
    // ouverture : Ctrl+Shift+P ; Ctrl+K ne fait rien par défaut
    out.ctrlK = [touche("k", { ctrlKey: true }).defaultPrevented, !!pal()];
    const ev = touche("P", { ctrlKey: true, shiftKey: true });
    out.ouverture = { prevenu: ev.defaultPrevented, boite: !!pal(), role: pal().getAttribute("role") || pal().role,
      modal: pal().ariaModal, focus: run("document.activeElement") === champ(),
      combobox: champ().getAttribute("role"), actif: champ().getAttribute("aria-activedescendant"),
      sections: pal().querySelectorAll(".palette-section").map((x) => x.textContent),
      premier: selTitre(), slashCache: !pal().querySelectorAll(".palette-titre").some((x) => x.textContent === "/model"),
      pied: pal().querySelector(".palette-raccourci").textContent };
    // clavier : ↓ ↓ ↑, puis la sélection suit aria-activedescendant
    champ().declencher("keydown", { key: "ArrowDown" });
    champ().declencher("keydown", { key: "ArrowDown" });
    champ().declencher("keydown", { key: "ArrowUp" });
    const sel = pal().querySelector(".palette-entree.sel");
    out.clavier = { actif: champ().getAttribute("aria-activedescendant"), selId: sel.id, ariaSel: sel.getAttribute("aria-selected") };
    // filtre et exécution : « relec » + Entrée ⇒ le fil de t3, palette fermée
    champ().value = "relec";
    champ().declencher("input");
    out.filtre = selTitre();
    champ().declencher("keydown", { key: "Enter" });
    out.aller = [ctx.location.hash, !!pal()];
    run(`ouvrirAgent("t1", null);`);
    await souffle();
    // « / » : les commandes de Claude par commandes.js ; Entrée remplit le champ de message
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "/mo"; champ().declencher("input");
    out.slash = pal().querySelectorAll(".palette-titre").map((x) => x.textContent);
    champ().declencher("keydown", { key: "Enter" });
    out.commande = run("S.vue.champ.value");
    run("S.vue.champ.value = ''");
    // une touche par la palette : POST keys
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "touche ctrl c"; champ().declencher("input");
    out.toucheTitre = selTitre();
    champ().declencher("keydown", { key: "Enter" });
    await souffle();
    out.touchePalette = keys();
    // réserve 1 (checker) : aria-modal ⇒ Tab ne sort pas de la boîte (champ ↔ ×, Maj+Tab à rebours)
    touche("P", { ctrlKey: true, shiftKey: true });
    const ou = () => { const f = run("document.activeElement"); return f === champ() ? "champ"
      : f && f.classList && f.classList.contains("palette-fermer") ? "fermer" : "dehors"; };
    const tab = (shiftKey) => pal().declencher("keydown", { key: "Tab", shiftKey }).defaultPrevented;
    out.tab = [ou(), tab(false), ou(), tab(false), ou(), tab(true), ou()];
    champ().declencher("keydown", { key: "Escape" });
    // Échap ferme et rend le focus
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().declencher("keydown", { key: "Escape" });
    out.echap = !!pal();
    // Ctrl+L : focus hors champ ⇒ POST keys ctrl+l, touche retenue (pas de barre d'adresse)
    appels.length = 0;
    run("document.body.focus ? 0 : 0");
    const l1 = touche("l", { ctrlKey: true });
    await souffle();
    out.ctrlL = { prevenu: l1.defaultPrevented, keys: keys() };
    // champ de message vide focalisé : idem ; rempli : laissé au navigateur
    appels.length = 0;
    run("S.vue.champ.focus()");
    const l2 = touche("l", { ctrlKey: true });
    run("S.vue.champ.value = 'brouillon'");
    const l3 = touche("l", { ctrlKey: true });
    await souffle();
    out.ctrlLChamp = { vide: l2.defaultPrevented, rempli: l3.defaultPrevented, keys: keys() };
    run("S.vue.champ.value = ''");
    // écran xterm focalisé (lecture seule) : Ctrl+L part au terminal
    appels.length = 0;
    const ecran = run(`(() => { const e = el("div", { class: "ecran" }, el("textarea", { class: "xterm-helper-textarea" })); S.vue.chat.append(e); return e; })()`);
    ecran.querySelector("textarea").focus();
    touche("l", { ctrlKey: true });
    await souffle();
    out.ctrlLEcran = keys();
    run("document.body.focus ? 0 : 0");
    run("S.vue.champ.focus(); S.vue.champ.value = ''");
    // réserve 2 (checker, M3) : focus dans un autre input (formulaire « Nouveau space »), vue ouverte ⇒
    // Ctrl+L reste au navigateur, aucun POST keys
    appels.length = 0;
    run("S.formSpace = true; rendreCote(true);");
    const dossier = S().liste.querySelectorAll(".form-space input")[1];
    dossier.focus();
    const lInput = touche("l", { ctrlKey: true });
    await souffle();
    out.ctrlLInput = { input: !!dossier, prevenu: lInput.defaultPrevented, keys: keys() };
    run("S.formSpace = null; rendreCote(true); S.vue.champ.focus();");
    // agent bloqué : ni Ctrl+L ni touche de la palette ne partent
    appels.length = 0;
    run(`S.agents[0].state = "blocked"; S.agents[0].msg = "Allow Bash?";`);
    const l4 = touche("l", { ctrlKey: true });
    await souffle();
    out.bloqueCtrlL = { keys: keys(), statut: run("S.vue.statut.textContent"), prevenu: l4.defaultPrevented };
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "touche entree"; champ().declencher("input");
    const e = pal().querySelector(".palette-entree.sel");
    out.bloqueEntree = { titre: selTitre(), desactive: e.getAttribute("aria-disabled") };
    champ().declencher("keydown", { key: "Enter" });
    await souffle();
    out.bloquePalette = { keys: keys(), ouverte: !!pal(), note: pal().querySelector(".palette-note").textContent };
    champ().declencher("keydown", { key: "Escape" });
    run(`S.agents[0].state = "idle";`);
    // serveur qui répond 409 bloque (l'état a changé entre-temps) : message, pas de plantage
    reponses = (m, chemin) => (chemin.endsWith("/keys") ? { __erreur: { status: 409, code: "bloque", detail: "" } }
      : chemin.endsWith("/choices") ? { msg: "", choices: [] } : serveurDeBase(m, chemin));
    touche("l", { ctrlKey: true });
    await souffle();
    out.serveur409 = run("S.vue.statut.textContent");
    // réserve 3 : un envoi déjà en vol vers ce terminal ⇒ 409 occupe, dit à l'écran
    reponses = (m, chemin) => (chemin.endsWith("/keys") ? { __erreur: { status: 409, code: "occupe", detail: "" } }
      : chemin.endsWith("/choices") ? { msg: "", choices: [] } : serveurDeBase(m, chemin));
    touche("l", { ctrlKey: true });
    await souffle();
    out.occupe = run("S.vue.statut.textContent");
    reponses = (m, chemin) => (chemin.endsWith("/choices") ? { msg: "", choices: [] } : serveurDeBase(m, chemin));
    // fermer : la confirmation existante, aucun POST close
    appels.length = 0;
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "fermer"; champ().declencher("input");
    champ().declencher("keydown", { key: "Enter" });
    out.fermer = { confirmation: !!run("S.confirmation") && run("S.confirmation.querySelector(\".boite-titre\").textContent"),
      close: appels.filter((a) => a[1].endsWith("/close")).length };
    run("fermerConfirmation()");
    // créer : nouveau terminal dans le space de l'agent ouvert, route du menu « + »
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "nouveau terminal"; champ().declencher("input");
    champ().declencher("keydown", { key: "Enter" });
    await souffle();
    out.creer = appels.filter((a) => a[1].includes("/open")).map((a) => [a[1], a[2]]);
    // page : tri, réglages › rubrique
    touche("P", { ctrlKey: true, shiftKey: true });
    champ().value = "trier grouped"; champ().declencher("input");
    champ().declencher("keydown", { key: "Enter" });
    out.tri = ctx.localStorage.getItem("herdr.tri-agents");
    touche("P", { ctrlKey: true, shiftKey: true });
    // réserve 5 : sous Firefox, Ctrl+Shift+P n'arrive jamais à la page — les réglages le disent
    ctx.navigator.userAgent = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0";
    champ().value = "reglages affichage"; champ().declencher("input");
    champ().declencher("keydown", { key: "Enter" });
    await souffle();
    const rg = () => document.body.querySelector(".boite.reglages");
    const alerteDe = (action) => { // l'alerte suit sa ligne dans la carte
      const l = rg().querySelectorAll(".reglage-ligne").find((x) => x.dataset.raccourci === action);
      const voisin = l.parentNode.childNodes[l.parentNode.childNodes.indexOf(l) + 1];
      return voisin && voisin.classList.contains("alerte-raccourci") ? [voisin.textContent] : [];
    };
    out.firefox = alerteDe("palette");
    out.aideNettoyer = rg().querySelectorAll(".reglage-bascule").find((x) => x.querySelector(".intitule").textContent
      === "Intercepter Ctrl+L (nettoyer le terminal)").querySelector(".description").textContent;
    out.reglages = [!!rg(), ctx.localStorage.getItem("herdr.reglages-rubrique")];
    // réglages : capture du raccourci de la palette
    const ligne = (action) => rg().querySelectorAll(".reglage-ligne").find((x) => x.dataset.raccourci === action);
    const capture = (action, key, mods) => {
      const b = ligne(action).querySelectorAll("button").find((x) => x.textContent === "Modifier" || x.classList.contains("capture"));
      if (!b.classList.contains("capture")) b.click();
      const e = Object.assign({ key, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
        stopPropagation() {} }, mods);
      b.declencher("keydown", e);
      return ligne(action).parentNode.querySelectorAll(".note-cote").filter((x) => !x.classList.contains("alerte-raccourci"))
        .map((x) => x.textContent).filter(Boolean).join(" | ");
    };
    out.kbdAvant = ligne("palette").querySelector("kbd").textContent;
    out.refusW = [capture("palette", "w", { ctrlKey: true }), ctx.localStorage.getItem("herdr.raccourci-palette")];
    out.refusL = [capture("palette", "l", { ctrlKey: true }), ctx.localStorage.getItem("herdr.raccourci-palette")];
    out.pendantCapture = [touche("P", { ctrlKey: true, shiftKey: true }).defaultPrevented, !!pal()];
    out.accepte = [capture("palette", "k", { ctrlKey: true, altKey: true }), ctx.localStorage.getItem("herdr.raccourci-palette"),
      ligne("palette").querySelector("kbd").textContent];
    out.firefoxApres = alerteDe("palette"); // Ctrl+Alt+K choisi : plus d'alerte
    delete ctx.navigator.userAgent;
    out.nettoyerRefus = [capture("nettoyer", "k", { ctrlKey: true, altKey: true }), ctx.localStorage.getItem("herdr.raccourci-nettoyer")];
    capture("nettoyer", "Escape", {});
    run("fermerReglages()");
    // le nouveau raccourci ouvre ; l'ancien ne fait plus rien
    out.ancien = [touche("P", { ctrlKey: true, shiftKey: true }).defaultPrevented, !!pal()];
    out.nouveau = [touche("k", { ctrlKey: true, altKey: true }).defaultPrevented, !!pal()];
    champ().declencher("keydown", { key: "Escape" });
    // interception de Ctrl+L désactivée par l'interrupteur des réglages : Ctrl+L au navigateur
    run("ouvrirReglages()");
    await souffle();
    const sw = rg().querySelectorAll(".reglage-bascule").find((x) => x.querySelector(".intitule").textContent
      === "Intercepter Ctrl+L (nettoyer le terminal)").querySelector(".interrupteur");
    out.interrupteurAvant = sw.getAttribute("aria-checked");
    sw.click();
    out.interrupteurStocke = ctx.localStorage.getItem("herdr.intercepter-nettoyer");
    run("fermerReglages()");
    appels.length = 0;
    const l5 = touche("l", { ctrlKey: true });
    await souffle();
    out.desactive = { prevenu: l5.defaultPrevented, keys: keys() };
    // bouton de l'en-tête (téléphone)
    run("document.body.focus ? 0 : 0");
    S().panneau.querySelector(".entete .bouton-palette").click();
    out.bouton = !!pal();
    pal().querySelector(".palette-fermer").click();
    out.boutonFermer = !pal();
    out.fautes = fautes();
    return out;
  },
  async "suite-erreur"() {
    let repondu = false;
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) {
        if (repondu) return { __erreur: { status: 502, code: "core", detail: "" } };
        return { msg: "m", kind: null, menu: null, ...QUESTION("Quelle couleur ?", "Rouge", "Vert") };
      }
      if (chemin.endsWith("/answer")) { repondu = true; return { ok: true }; }
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    parTexte(v.carte, "button", "Rouge").click();
    await souffle();
    const lectures = () => appels.filter((a) => a[1].endsWith("/choices")).length;
    const avant = lectures();
    for (let k = 0; k < 60; k++) { feu(); await souffle(); }
    return { lectures: lectures() - avant, suiteVivante: !!v.suite, minuteurs: minuteurs.size };
  },

  // Réserve D2 de la PR #22 : terminal et page côte à côte ; Q1 répondue dans le terminal, l'écran
  // porte Q2 sans événement ; un clic sur la carte Q1 restée affichée ne doit pas répondre à Q2.
  // Le faux serveur fait comme le vrai : signature absente = garde d'avant (accepté).
  async "suite-perime"() {
    let ecran = QUESTION("←  ☐ Couleur  ☐ Taille  →\nQuelle couleur ?", "Rouge", "Vert");
    const acceptes = [];
    reponses = (m, chemin, corps) => {
      if (chemin.endsWith("/choices")) return { msg: "m", kind: null, menu: null, ...ecran };
      if (chemin.endsWith("/answer")) {
        const signe = "contexte" in corps || "libelles" in corps;
        if (signe && (corps.contexte !== ecran.contexte
            || JSON.stringify(corps.libelles) !== JSON.stringify(ecran.choices.map((c) => c.label)))) {
          return { __erreur: { status: 409, code: "perime", detail: "" } };
        }
        acceptes.push(corps.digit);
        return { ok: true };
      }
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    const boutons = () => (v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent));
    const out = { q1: boutons() };
    ecran = QUESTION("←  ☒ Couleur  ☐ Taille  →\nQuelle taille ?", "Petit", "Grand"); // réponse dans le terminal
    parTexte(v.carte, "button", "Rouge").click();
    await souffle();
    out.corps = appels.filter((a) => a[0] === "POST").map((a) => a[2]);
    out.acceptes = acceptes;
    out.apres = boutons();
    out.contexteApres = v.carte.textContent.includes("Quelle taille ?");
    return out;
  },

  // M5 de la revue de la PR #22 : l'agent repart (« working ») puis redemande la MÊME permission
  // (même commande) avant toute relecture ; la carte doit s'afficher tout de suite, pas après 3 s.
  async "suite-etat"() {
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) return { msg: "m", kind: null, menu: null, ...QUESTION("Bash command\nls", "Yes", "No") };
      if (chemin.endsWith("/answer")) return { ok: true };
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    parTexte(v.carte, "button", "Yes").click();
    await souffle();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working" }))}]);`);
    await souffle();
    run(`surAgents([${JSON.stringify(agent("t1", { state: "blocked", msg: "m" }))}]);`);
    await souffle(); // aucune minuterie ne part : seul l'événement compte
    return { visible: !v.carte.hidden, boutons: v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent) };
  },

  // M6 de la revue de la PR #22 : l'état connu de l'agent n'est plus « blocked » sans que la vue
  // ait été rafraîchie (surAgents sort avant majVueAgent quand le formulaire de connexion est
  // affiché) ; la relecture s'arrête d'elle-même, sans relire /choices ni remontrer une carte.
  async "suite-garde"() {
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) return { msg: "m", kind: null, menu: null, ...QUESTION("Quelle couleur ?", "Rouge", "Vert") };
      if (chemin.endsWith("/answer")) return { ok: true };
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    parTexte(v.carte, "button", "Rouge").click();
    await souffle();
    const connexion = new Element("div"); connexion.className = "connexion";
    document.body.append(connexion);
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working" }))}]);`);
    const lectures = () => appels.filter((a) => a[1].endsWith("/choices")).length;
    const avant = lectures();
    for (let k = 0; k < 15; k++) { feu(); await souffle(); }
    connexion.remove();
    return { lectures: lectures() - avant, carteVisible: !v.carte.hidden, suiteVivante: !!v.suite };
  },

  // M7 de la revue de la PR #22 : l'écran Submit rendu en menu à curseur (variante possible de
  // Claude Code) ; après la réponse au menu, la question suivante s'affiche aussi.
  async "suite-menu"() {
    let i = 0, retard = 0;
    const ecrans = [{ contexte: "", choices: [], menu: { options: ["Submit answers", "Cancel"], courant: 0 } },
      { menu: null, ...QUESTION("Encore une ?", "Oui", "Non") }];
    reponses = (m, chemin) => {
      if (chemin.endsWith("/choices")) {
        const e = ecrans[retard > 0 ? i - 1 : i];
        if (retard > 0) retard--;
        return { msg: "m", kind: null, ...e };
      }
      if (chemin.endsWith("/answer")) { i++; retard = 1; return { ok: true }; }
      return serveurDeBase(m, chemin);
    };
    const v = await ouvrirBloque();
    const boutons = () => (v.carte.hidden ? [] : v.carte.querySelectorAll("button").map((b) => b.textContent));
    const out = { menu: boutons() };
    parTexte(v.carte, "button", "Submit answers").click();
    await souffle();
    feu(); await souffle();
    out.pendant = boutons();
    feu(); await souffle();
    out.suivante = boutons();
    out.posts = appels.filter((a) => a[0] === "POST").map((a) => a[2]);
    return out;
  },

  // suggestion grisée de Claude Code (2026-11-15) : placeholder, puce, Tab et →, jamais envoyée
  async suggestion() {
    let sugg = { suggestion: "fais le test", turn: 2 };
    reponses = (m, c) => (c.endsWith("/suggestion") ? sugg : serveurDeBase(m, c));
    S().agents = [agent("t1", { state: "done", turn: 2 })];
    run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
    await souffle();
    const v = S().vue;
    const lectures = () => appels.filter((x) => x[1].endsWith("/suggestion")).length;
    const etat = () => ({ valeur: v.champ.value, placeholder: v.champ.placeholder,
      grise: v.champ.classList.contains("avec-suggestion"), puce: v.puceSugg ? !v.puceSugg.hidden : null });
    const touche = (key, plus = {}) => v.champ.declencher("keydown", Object.assign({ key }, plus)).defaultPrevented;
    const out = { avantLecture: etat(), lecturesAvant: lectures() };
    // « Live » et la puce partagent UNE ligne (demandé le 2026-11-15) : même conteneur, puce d'abord
    const barre = v.live.parentNode;
    out.ligne = { memeConteneur: v.puceSugg.parentNode === barre, classe: barre.className,
      ordre: barre.children.indexOf(v.puceSugg) < barre.children.indexOf(v.live) };
    feu(); await souffle();
    out.lue = etat();
    out.tab = touche("Tab");
    out.apresTab = etat();
    v.champ.value = ""; v.champ.declencher("input");
    out.videDeNouveau = etat();
    out.fleche = touche("ArrowRight");
    out.apresFleche = v.champ.value;
    out.tabSurBrouillon = touche("Tab"); // champ non vide : Tab garde son rôle
    v.champ.value = ""; v.champ.declencher("input");
    out.maj = touche("Tab", { shiftKey: true });
    v.puceSugg.click();
    out.apresPuce = etat();
    out.envois = appels.filter((x) => x[1].endsWith("/send")).length; // recopiée, jamais envoyée
    v.champ.value = ""; v.champ.declencher("input");
    feu(); await souffle();
    out.relectures = lectures(); // relue tant que l'agent est au repos
    sugg = { suggestion: null, turn: 2 }; // la suggestion a disparu (brouillon tapé dans le terminal)
    feu(); await souffle();
    out.disparue = etat();
    sugg = { suggestion: "autre", turn: 2 };
    feu(); await souffle();
    out.changee = etat().placeholder;
    // l'agent repart : plus de suggestion, plus de relecture
    run(`surAgents([${JSON.stringify(agent("t1", { state: "working", turn: 2 }))}])`);
    out.repart = etat();
    const n = lectures();
    feu(); await souffle();
    out.lecturesPendantTravail = lectures() - n;
    // nouveau tour : une réponse du tour d'avant (course) est ignorée
    sugg = { suggestion: "vieille", turn: 2 };
    run(`surAgents([${JSON.stringify(agent("t1", { state: "done", turn: 3 }))}])`);
    feu(); await souffle();
    out.reponsePerimee = etat();
    sugg = { suggestion: "neuve", turn: 3 };
    feu(); await souffle();
    out.nouveauTour = etat().placeholder;
    // début de la suggestion tapé, curseur en fin : → ou Tab complète, la puce porte le texte entier
    sugg = { suggestion: "lance le checker", turn: 3 };
    feu(); await souffle();
    const tape = (t, curseur = t.length) => { v.champ.value = t; v.champ.selectionStart = v.champ.selectionEnd = curseur;
      v.champ.declencher("input"); };
    const puceTexte = () => (v.puceSugg.hidden ? null : v.puceSugg.textContent);
    tape("lance");
    out.prefixe = { ...etat(), puceTexte: puceTexte() };
    out.prefixeFleche = touche("ArrowRight");
    out.prefixeFlecheValeur = v.champ.value;
    tape("lance", 2); // curseur au milieu : → déplace le curseur, rien d'autre
    out.prefixeMilieu = [touche("ArrowRight"), v.champ.value];
    tape("lance le");
    out.prefixeTab = [touche("Tab"), v.champ.value];
    tape("lan");
    v.puceSugg.click();
    out.prefixePuce = v.champ.value;
    tape("lancer"); // pas un début de la suggestion
    out.horsPrefixe = [puceTexte(), touche("ArrowRight"), v.champ.value];
    tape("");
    out.puceVide = puceTexte();
    // vignettes jointes : le champ est leur commentaire, la suggestion se retire
    v.pieces = [{ f: {}, url: null }];
    run("majPieces(S.vue)");
    out.vignettes = { ...etat(), tab: touche("Tab") };
    v.pieces = [];
    run("majPieces(S.vue)");
    // onglet caché : aucune lecture, la minuterie continue
    const nc = lectures();
    document.hidden = true;
    feu(); await souffle();
    out.lecturesCache = lectures() - nc;
    out.relanceCache = minuteurs.size;
    document.hidden = false;
    // envoi : l'ancienne suggestion ne revient pas en placeholder avant que l'agent reparte
    reponses = (m, c) => (m === "POST" ? { ok: true } : c.endsWith("/suggestion") ? sugg : serveurDeBase(m, c));
    tape("message");
    await ctx.envoyerTexte();
    out.apresEnvoi = etat();
    feu(); await souffle(); // relue : le cas tactile qui suit a besoin d'une suggestion à l'écran
    out.avantTactile = etat().puce;
    // téléphone : Tab non intercepté, la puce reste le geste
    ctx.matchMedia = () => ({ matches: true });
    out.tabTactile = touche("Tab");
    ctx.matchMedia = undefined;
    // Claude jamais lancé (tour 0) : placeholder « Try … », aucune lecture
    run(`surAgents([${JSON.stringify(agent("t1", { state: "idle", turn: 0 }))}])`);
    const n0 = lectures();
    feu(); await souffle();
    out.tour0 = { lectures: lectures() - n0, ...etat() };
    out.fautes = fautes();
    return out;
  },
};
function QUESTION(contexte, ...labels) {
  return { contexte, choices: labels.map((l, i) => ({ digit: String(i + 1), label: l, detail: "" })) };
}
async function ouvrirBloque() {
  S().agents = [agent("t1", { state: "blocked", msg: "m" })];
  S().spaces = SPACES;
  run(`squelette(); rendreCote(); ouvrirAgent("t1", null);`);
  await souffle();
  return S().vue;
}
if (scenario === "nuls") {
  // Agents aux champs absents ou nuls : l'ancien serveur n'envoie ni since_ms, ni model, ni dir…
  const etapes = [
    ["liste", `
      S.agents = [
        { address: "t1" },
        { address: "t2", name: null, title: null, model: null, since_ms: null, dir: null, branch: null,
          machine: null, space: null, tab: null, role: null, activity: null, cols: null, cwd: null,
          state: "working", program: "claude-code", subagents: [{ id: "a" }, { id: null, title: null, state: null, model: null }] },
        { address: "t3", state: "blocked", msg: null, since_ms: "x", subagents: null, space: "sp", tab: null, program: "claude-code" },
        { address: "t4", state: "unknown", program: "zsh", space: "sp", tab: "shell", machine: "my-laptop", since_ms: NaN },
      ];
      S.spaces = [{ id: "sp1", name: "sp", folder: null }];
      S.valsBarre = { cpu: null, mem: undefined, models: { opus: null, sonnet: 2 } };
      squelette(); rendreCote(); rendreBarre(); rendreAccueil();`],
    ["menus de space", `S.formSpace = true; rendreCote(true);
      for (const b of document.body.querySelectorAll(".bouton-plus")) b.click();`],
    ["menu des agents connus", `S.connus = [{ id: "claude", name: "Claude Code", installed: true },
        { id: "codex", name: "Codex", installed: false }, { id: "x", name: "X", installed: null }];
      for (const b of document.body.querySelectorAll(".bouton-plus")) { b.click(); b.click(); }`],
    ["confirmation de fermeture", `demanderFermeture("t2"); demanderFermeture("t1"); demanderFermeture("t3");`],
    ["fil d'agent", `ouvrirAgent("t2", null);
      ajouterItems(S.vue, [{ role: "outil" }, { role: "outil", tool: null, text: null, ref: null },
        { role: "resultat" }, { role: "resultat", text: null, error: true }, { role: "agent" },
        { role: "moi", text: null, image: null }, { role: "outil", tool: "Bash", text: null }]);`],
    ["commandes", `S.vue.commandes = [{ name: "model", description: null, args: null, interactive: true, source: null }];
      S.vue.champ.value = "/mo"; majCommandes(S.vue);`],
    ["agent bloqué", `ouvrirAgent("t3", null);`],
    ["sous-agent", `ouvrirAgent("t2", "a");`],
    ["sous-agent disparu", `ouvrirAgent("t2", "zz");`],
    ["zone Agents (spec 2026-11-15)", `S.menuTri = true; rendreCote(); basculerPli("sec-working");
      ecrireStockage(CLE_TRI, "grouped"); rendreCote();`],
  ];
  const out = [];
  for (const [nom, code] of etapes) {
    run(code);
    for (const f of fautes()) out.push(nom + " " + f);
  }
  process.stdout.write(JSON.stringify({ etapes: etapes.length, fautes: out, texte: document.body.textContent.length }));
} else if (SCENARIOS[scenario]) {
  SCENARIOS[scenario]().then((r) => process.stdout.write(JSON.stringify(r)),
    (e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
} else if (scenario === "--liste") { // tests/test_i18n.py : tous les scénarios, rejoués dans chaque langue
  process.stdout.write(JSON.stringify(["nuls", ...Object.keys(SCENARIOS)]));
} else if (scenario === "filtre") {
  const cas = JSON.parse(fs.readFileSync(0, "utf8"));
  const liste = cas.liste;
  process.stdout.write(JSON.stringify(cas.saisies.map((s) => ctx.COMMANDES.filtrer(liste, s).map((c) => c.name))));
} else {
  process.stderr.write("scénario inconnu\n");
  process.exit(2);
}
