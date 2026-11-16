"use strict";
// Palette de commandes (comme celle de herdr-app) : entrées, filtre flou, raccourcis clavier.
// Pur : aucune dépendance au DOM ni au stockage (testé sous node par tests/test_palette.py).
// app.js lui passe l'état de la page en objets simples et exécute l'action de l'entrée choisie.
(function (racine) {
  const enModule = typeof module !== "undefined" && module.exports;
  const CMD = enModule ? require("./commandes.js") : racine.COMMANDES;
  // textes : static/i18n/{en,fr}.js, lus à chaque appel (node : I18N.poser() après le require)
  const I18N = enModule ? require("./i18n/i18n.js") : racine.I18N;
  const t = I18N.t, tn = I18N.tn;

  const CLES_SECTIONS = ["aller", "ouvert", "creer", "page"];
  const sections = () => CLES_SECTIONS.map((k) => t("palette.section." + k));
  const [ALLER, OUVERT, CREER, PAGE] = CLES_SECTIONS;
  // touches de la section « Agent ouvert » : les noms de `hc keys`, même liste blanche que le serveur
  // (herdr_web/actions.py TOUCHES) ; jamais quand l'agent est `blocked` (le serveur refuse aussi)
  // [nom hc, combinaison affichée] : le libellé passe par libelle() (« Échap » / « Esc »)
  const TOUCHES = [["esc", "escape"], ["ctrl+c", "ctrl+c"], ["ctrl+l", "ctrl+l"], ["enter", "enter"],
    ["up", "arrowup"], ["down", "arrowdown"]];

  // ---------- raccourcis : deux actions, une combinaison chacune, choisie par appareil ----------
  // palette : ouvrir/fermer la palette ; nettoyer : envoyer Ctrl+L au terminal ouvert (§1), si
  // l'interception est active (réglages ; désactivée, la combinaison va au navigateur comme avant).
  const DEFAUTS = { palette: "ctrl+shift+p", nettoyer: "ctrl+l" };
  const DEFAUT = DEFAUTS.palette;

  // pris par le navigateur avant la page : aucun preventDefault n'y peut rien
  const RESERVES = ["ctrl+w", "ctrl+t", "ctrl+n", "ctrl+tab", "ctrl+shift+tab", "ctrl+shift+w", "ctrl+shift+t",
    "ctrl+shift+n", "ctrl+q", "ctrl+shift+q", "meta+w", "meta+t", "meta+n", "meta+q"];
  // Firefox : `key_privatebrowsing` (accel+shift+P) porte reserved="true" dans browser-sets.inc —
  // une touche réservée n'est jamais envoyée au contenu, la page ne peut pas l'intercepter. Le défaut
  // ne change pas : sous Firefox, les réglages le disent et proposent d'en choisir une autre.
  const RESERVES_FIREFOX = ["ctrl+shift+p", "meta+shift+p"];
  const estFirefox = (ua) => /\bFirefox\//.test(String(ua || ""));
  function avertissement(c, ua) {
    return estFirefox(ua) && RESERVES_FIREFOX.includes(c)
      ? t("raccourci.firefox", { combo: libelle(c) })
      : "";
  }
  const MODIFS = ["ctrl", "alt", "shift", "meta"];
  const SEULS = ["control", "alt", "shift", "meta", "os", "altgraph", "capslock", "dead", "unidentified", "process"];

  // La combinaison d'un keydown, forme canonique « ctrl+alt+shift+meta+touche » ; null pour un
  // modificateur seul ou une saisie en cours de composition.
  function combinaison(ev) {
    if (!ev || ev.isComposing || typeof ev.key !== "string" || !ev.key) return null;
    let k = ev.key.toLowerCase();
    if (SEULS.includes(k)) return null;
    if (k === " ") k = "space";
    else if (k === "+") k = "plus";
    else if (/^key[a-z]$/.test(String(ev.code || "").toLowerCase()) && !/^[a-z]$/.test(k) && (ev.altKey || ev.ctrlKey)) {
      k = ev.code.slice(3).toLowerCase(); // Alt+P sur macOS rend « π » : la lettre de la touche
    }
    const mods = [ev.ctrlKey && "ctrl", ev.altKey && "alt", ev.shiftKey && "shift", ev.metaKey && "meta"].filter(Boolean);
    return [...mods, k].join("+");
  }

  // « palette », « nettoyer » ou null pour un keydown. `combos` : { palette, nettoyer } — absent :
  // la valeur par défaut ; `nettoyer: null` : interception de Ctrl+L désactivée.
  function raccourci(ev, combos) {
    const c = combinaison(ev);
    if (!c) return null;
    const o = combos || {};
    const palette = o.palette || DEFAUTS.palette;
    const nettoyer = o.nettoyer === null ? null : o.nettoyer || DEFAUTS.nettoyer;
    if (c === palette) return "palette";
    if (nettoyer && c === nettoyer) return "nettoyer";
    return null;
  }

  // Une combinaison choisie dans les réglages : { ok, raison }. `autre` : { action, combo } de l'autre
  // action, qu'on ne peut pas reprendre (Ctrl+L, par défaut, pour la palette).
  function raccourciValide(c, autre) {
    if (typeof c !== "string" || !c) return { ok: false, raison: t("raccourci.aucune") };
    const parts = c.split("+");
    const touche = parts[parts.length - 1];
    const mods = parts.slice(0, -1);
    if (!touche || mods.some((m) => !MODIFS.includes(m))) return { ok: false, raison: t("raccourci.illisible") };
    if (RESERVES.includes(c)) return { ok: false, raison: t("raccourci.navigateur") };
    if (!mods.some((m) => m !== "shift")) return { ok: false, raison: t("raccourci.modif") };
    if (autre && c === autre.combo) {
      const action = I18N.existe("action." + autre.action) ? t("action." + autre.action) : autre.action;
      return { ok: false, raison: t("raccourci.dejaPris", { action }) };
    }
    return { ok: true, raison: "" };
  }

  // noms de touches : symboles et sigles tels quels, les mots dans la langue de la page
  const NOMS = { plus: "+", arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→" };
  const NOMS_TRADUITS = { meta: "touche.meta", space: "touche.space", escape: "touche.esc", enter: "touche.enter" };
  function libelle(c) {
    return String(c || "").split("+").filter(Boolean)
      .map((p) => (NOMS_TRADUITS[p] ? t(NOMS_TRADUITS[p]) : NOMS[p])
        || (p.length === 1 ? p.toUpperCase() : p[0].toUpperCase() + p.slice(1))).join("+");
  }

  // Le raccourci « nettoyer » va au terminal ouvert seulement si rien d'autre ne le réclame : une vue
  // d'agent (pas un sous-agent), aucune boîte ouverte, et le focus hors de tout champ — sauf le champ
  // de message vide, ou l'écran xterm (lecture seule). Champ non vide : la touche reste au navigateur.
  function ctrlLPermis(o) {
    if (!o || !o.vue || o.sous || o.modal) return false;
    return o.focus === "aucun" || o.focus === "terminal" || (o.focus === "message" && !o.champRempli);
  }

  // ---------- filtre flou ----------
  const plier = (t) => String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  // Note d'un mot de la requête dans un texte : plus bas = meilleur ; null s'il n'y est pas.
  // `flou` : la sous-suite (« rlct » pour « relecteur ») n'est permise que sur le titre — sur un détail,
  // elle trouvait « ctrl » dans « space · 1 terminal ».
  function noteMot(t, q, flou) {
    if (!q) return 0;
    if (t.startsWith(q)) return 0;
    if (t.split(/[\s\-_/:·.,()«»›+]+/).some((m) => m.startsWith(q))) return 1;
    if (t.includes(q)) return 2;
    if (!flou) return null;
    let i = 0, debut = -1, trous = 0, prec = -1;
    for (const c of q) {
      const j = t.indexOf(c, i);
      if (j < 0) return null;
      if (debut < 0) debut = j;
      if (prec >= 0) trous += j - prec - 1;
      prec = j;
      i = j + 1;
    }
    return 3 + Math.min(trous, 50) / 100;
  }

  // Note d'une entrée pour une requête (mots séparés par des espaces, tous requis) : titre d'abord,
  // le détail et la section valent moins ; null si un mot manque.
  function note(e, requete) {
    const mots = plier(requete).split(/\s+/).filter(Boolean);
    const titre = plier(e.titre), reste = plier([e.detail, e.section, e.motsCles].filter(Boolean).join(" "));
    let total = 0;
    for (const m of mots) {
      const a = noteMot(titre, m, true);
      const b = noteMot(reste, m, false);
      if (a === null && b === null) return null;
      total += a === null ? 4 + b : b === null ? a : Math.min(a, 4 + b);
    }
    return total;
  }

  // Les entrées qui correspondent, les meilleures d'abord dans chaque section ; sans requête, les
  // sections dans l'ordre de SECTIONS, avec une requête, celle qui porte la meilleure note d'abord. « /… » : les commandes de Claude seules, rangées par commandes.js (même ordre que
  // l'autocomplétion du champ de message).
  function filtrer(entrees, requete) {
    const q = typeof requete === "string" ? requete.trim() : "";
    const liste = Array.isArray(entrees) ? entrees : [];
    if (q[0] === "/") {
      const cmds = liste.filter((e) => e.cmd);
      if (!CMD) return [];
      const ordre = CMD.filtrer(cmds.map((e) => e.cmd), q, 50);
      return ordre.map((c) => cmds.find((e) => e.cmd === c));
    }
    const notes = [];
    const ordre = sections();
    liste.forEach((e, i) => {
      if (!q && e.cache) return; // les commandes « / » n'encombrent pas la liste vide
      const n = note(e, q);
      if (n !== null) notes.push([ordre.indexOf(e.section), n, i, e]);
    });
    const meilleure = new Map();
    for (const [s, n] of notes) if (!meilleure.has(s) || n < meilleure.get(s)) meilleure.set(s, n);
    const rang = (s) => (q ? meilleure.get(s) * 10 + s : s);
    notes.sort((a, b) => rang(a[0]) - rang(b[0]) || a[1] - b[1] || a[2] - b[2]);
    return notes.map((x) => x[3]);
  }

  // ---------- entrées ----------
  // ctx : {
  //   agents: [{ addr, nom, role, modele, etat, space, terminal }],
  //   spaces: [{ nom, addr }]                       (addr : l'agent le plus urgent du space, sinon absent)
  //   ouvert: { addr, nom, claude, bloque, epinglable, epingle } | null,
  //   commandes: [{ name, description, args }],
  //   creer: { spaces: [{ id, nom }], connus: [{ id, nom }] },
  //   rubriques: [[cle, nom]], tri: "priority" | "grouped", tris: [[cle, nom]],
  //   groupes: [{ cle, titre, plie }],
  // }
  function construire(ctx) {
    const c = ctx || {};
    const out = [];
    const ajout = (cle, titre, detail, action, plus) => out.push(Object.assign({ section: t("palette.section." + cle), titre: String(titre || ""),
      detail: String(detail || ""), action }, plus || {}));
    const ouvert = c.ouvert || null;
    // Aller à
    for (const a of c.agents || []) {
      if (!a || !a.addr || (ouvert && a.addr === ouvert.addr)) continue;
      ajout(ALLER, a.nom || a.addr, [a.terminal ? t("palette.terminal") : a.role, a.modele, a.etat, a.space].filter(Boolean).join(" · "),
        { type: "aller", addr: a.addr }, { motsCles: a.addr, genre: a.terminal ? "terminal" : "agent" });
    }
    for (const sp of c.spaces || []) {
      if (!sp || !sp.nom || !sp.addr) continue;
      ajout(ALLER, sp.nom, t("palette.space") + (sp.n ? " · " + tn("cote.terminaux", sp.n) : ""),
        { type: "aller", addr: sp.addr }, { genre: "space" });
    }
    // Agent ouvert
    if (ouvert && ouvert.addr) {
      const motif = ouvert.bloque ? t("palette.attend") : "";
      for (const [k, combo] of TOUCHES) {
        const nom = libelle(combo);
        ajout(OUVERT, t("palette.touche", { nom }), motif || t("aide." + k), { type: "touche", addr: ouvert.addr, key: k, nom },
          { inactif: !!ouvert.bloque, genre: "touche" });
      }
      if (ouvert.claude) {
        ajout(OUVERT, t("palette.cmdClaude"), t("palette.cmdFiltrer"), { type: "prefixe", texte: "/" },
          { genre: "commande" });
        for (const cmd of c.commandes || []) {
          if (!cmd || !cmd.name) continue;
          ajout(OUVERT, "/" + cmd.name, cmd.description, { type: "commande", addr: ouvert.addr, cmd },
            { cmd, cache: true, genre: "commande" });
        }
      }
      if (ouvert.epinglable) {
        ajout(OUVERT, ouvert.epingle ? t("carte.desepingler") : t("carte.epingler"), ouvert.nom, { type: "epingler", addr: ouvert.addr },
          { genre: "epingle" });
      }
      ajout(OUVERT, t("carte.fermer"), t("palette.confirmation", { nom: ouvert.nom }), { type: "fermer", addr: ouvert.addr },
        { genre: "fermer" });
    }
    // Créer : le menu « + », mêmes routes ; dans le space de l'agent ouvert s'il en a un
    const cr = c.creer || {};
    for (const sp of cr.spaces || []) {
      if (!sp || !sp.id) continue;
      const dans = t("palette.dans", { nom: sp.nom });
      ajout(CREER, t("palette.nouveauTerminal"), dans, { type: "ouvrir", space: sp, kind: "terminal" }, { genre: "terminal" });
      for (const a of cr.connus || []) {
        if (!a || !a.id) continue;
        if (a.id === "claude") {
          for (const [m, nm] of [["default", ""], ["opus", " (opus)"], ["sonnet", " (sonnet)"]]) {
            ajout(CREER, t("palette.demarrer", { nom: a.nom }) + nm, dans, { type: "ouvrir", space: sp, kind: a.id, model: m }, { genre: "agent" });
          }
        } else ajout(CREER, t("palette.demarrer", { nom: a.nom }), dans, { type: "ouvrir", space: sp, kind: a.id }, { genre: "agent" });
      }
    }
    ajout(CREER, t("palette.nouveauSpace"), t("palette.nomDossier"), { type: "space" }, { genre: "space" });
    // Page
    ajout(PAGE, t("palette.reglages"), "", { type: "reglages", rubrique: null }, { genre: "reglages" });
    for (const [k, nom] of c.rubriques || []) ajout(PAGE, t("palette.reglagesRub", { nom }), "", { type: "reglages", rubrique: k }, { genre: "reglages" });
    for (const [k, nom] of c.tris || []) {
      ajout(PAGE, t("tri.bouton", { nom }), k === c.tri ? t("palette.actuel") : "", { type: "tri", mode: k }, { genre: "tri" });
    }
    const groupes = c.groupes || [];
    for (const g of groupes) {
      ajout(PAGE, t(g.plie ? "palette.deplier" : "palette.plier", { titre: g.titre }), t("palette.sectionListe"), { type: "pli", cle: g.cle },
        { genre: "pli" });
    }
    if (groupes.length) {
      ajout(PAGE, t("palette.plierTout"), "", { type: "plis", plie: true }, { genre: "pli" });
      ajout(PAGE, t("palette.deplierTout"), "", { type: "plis", plie: false }, { genre: "pli" });
    }
    ajout(PAGE, t("palette.recharger"), t("palette.purge"), { type: "recharger" }, { genre: "recharger" });
    return out;
  }

  const API = { sections, TOUCHES, DEFAUT, DEFAUTS, RESERVES, construire, filtrer, note, combinaison, raccourci,
    raccourciValide, libelle, ctrlLPermis, avertissement };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else racine.PALETTE = API;
})(typeof window !== "undefined" ? window : this);
