"use strict";
// Rendu Markdown par construction de nœuds DOM : createElement, createTextNode, setAttribute,
// textContent. Jamais de HTML interprété : une balise dans le texte reste du texte.
// Couvre : paragraphes, titres, gras, italique, barré, `code`, blocs ```, listes (puces, numéros,
// imbriquées), citations, filets, liens http(s) seulement, tableaux simples. Le reste : du texte.
(function (racine) {
  const LISTE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const CLOTURE = /^\s{0,3}(`{3,}|~{3,})\s*[\w+#.-]*\s*$/;
  const TITRE = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
  const FILET = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const CITATION = /^\s{0,3}>\s?(.*)$/;
  const SEPARATEUR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

  const INLINE = [
    ["code", /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/],
    ["lien", /\[([^\]\n]+)\]\((\S+?)\)/],
    ["gras", /\*\*([^\s*](?:[\s\S]*?[^\s])?)\*\*|__([^\s_](?:[\s\S]*?[^\s])?)__/],
    ["ital", /\*([^\s*](?:[\s\S]*?[^\s*])?)\*|(?<![\p{L}\p{N}_])_([^\s_](?:[\s\S]*?[^\s_])?)_(?![\p{L}\p{N}_])/u],
    ["barre", /~~([^\s~](?:[\s\S]*?[^\s~])?)~~/],
    ["url", /https?:\/\/[^\s<>"'`]+/],
  ];

  function urlSure(u) {
    try {
      const p = new URL(u).protocol;
      return p === "http:" || p === "https:";
    } catch (_) { return false; }
  }

  function lien(doc, href, enfants) {
    const a = doc.createElement("a");
    a.setAttribute("href", href);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    for (const e of enfants) a.appendChild(e);
    return a;
  }

  function finUrl(u) { // ponctuation finale hors du lien, parenthèse fermante si non ouverte
    for (;;) {
      const d = u.slice(-1);
      if (".,;:!?»'\"".includes(d)) u = u.slice(0, -1);
      else if (d === ")" && (u.split("(").length < u.split(")").length)) u = u.slice(0, -1);
      else return u;
    }
  }

  function inline(doc, texte) {
    const out = [];
    let reste = texte;
    while (reste) {
      let mieux = null;
      for (const [nom, re] of INLINE) {
        const m = re.exec(reste);
        if (m && (!mieux || m.index < mieux.m.index)) mieux = { nom, m };
      }
      if (!mieux) { out.push(doc.createTextNode(reste)); break; }
      const { nom, m } = mieux;
      let brut = m[0];
      let noeud;
      if (nom === "code") {
        noeud = doc.createElement("code");
        noeud.textContent = m[2];
      } else if (nom === "lien") {
        noeud = urlSure(m[2]) ? lien(doc, m[2], inline(doc, m[1])) : doc.createTextNode(brut);
      } else if (nom === "url") {
        brut = finUrl(brut);
        noeud = urlSure(brut) ? lien(doc, brut, [doc.createTextNode(brut)]) : doc.createTextNode(brut);
      } else {
        noeud = doc.createElement(nom === "gras" ? "strong" : nom === "ital" ? "em" : "del");
        for (const e of inline(doc, m[1] !== undefined ? m[1] : m[2])) noeud.appendChild(e);
      }
      if (m.index) out.push(doc.createTextNode(reste.slice(0, m.index)));
      out.push(noeud);
      reste = reste.slice(m.index + brut.length);
    }
    return out;
  }

  function remplir(doc, parent, texte) {
    for (const e of inline(doc, texte)) parent.appendChild(e);
    return parent;
  }

  const cellules = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const estTableau = (lignes, i) => lignes[i].includes("|") && i + 1 < lignes.length
    && SEPARATEUR.test(lignes[i + 1]) && lignes[i + 1].includes("-");
  const debutDeBloc = (lignes, i) => CLOTURE.test(lignes[i]) || TITRE.test(lignes[i])
    || FILET.test(lignes[i]) || CITATION.test(lignes[i]) || LISTE.test(lignes[i]) || estTableau(lignes, i);

  function tableau(doc, lignes, i) {
    const t = doc.createElement("table");
    const tete = doc.createElement("thead");
    const tr = doc.createElement("tr");
    for (const c of cellules(lignes[i])) tr.appendChild(remplir(doc, doc.createElement("th"), c));
    tete.appendChild(tr);
    t.appendChild(tete);
    const corps = doc.createElement("tbody");
    i += 2;
    while (i < lignes.length && lignes[i].includes("|") && lignes[i].trim()) {
      const r = doc.createElement("tr");
      for (const c of cellules(lignes[i])) r.appendChild(remplir(doc, doc.createElement("td"), c));
      corps.appendChild(r);
      i++;
    }
    if (corps.childNodes.length) t.appendChild(corps);
    return [t, i];
  }

  function liste(doc, lignes, i) {
    const m0 = lignes[i].match(LISTE);
    const retrait = m0[1].length;
    const ordonnee = /\d/.test(m0[2]);
    const l = doc.createElement(ordonnee ? "ol" : "ul");
    if (ordonnee && parseInt(m0[2], 10) !== 1) l.setAttribute("start", parseInt(m0[2], 10));
    let li = null;
    while (i < lignes.length) {
      const ligne = lignes[i];
      const m = ligne.match(LISTE);
      if (m && m[1].length === retrait && /\d/.test(m[2]) === ordonnee) {
        li = remplir(doc, doc.createElement("li"), m[3].trim());
        l.appendChild(li);
        i++;
      } else if (m && m[1].length > retrait && li) {
        const [sous, j] = liste(doc, lignes, i);
        li.appendChild(sous);
        i = j;
      } else if (!m && ligne.trim() && li && /^\s+/.test(ligne)
                 && ligne.match(/^\s*/)[0].length > retrait) {
        li.appendChild(doc.createElement("br"));
        remplir(doc, li, ligne.trim());
        i++;
      } else if (!ligne.trim()) {
        let j = i;
        while (j < lignes.length && !lignes[j].trim()) j++;
        const s = j < lignes.length && lignes[j].match(LISTE);
        if (!s || s[1].length < retrait || (s[1].length === retrait && /\d/.test(s[2]) !== ordonnee)) break;
        i = j;
      } else break;
    }
    return [l, i];
  }

  function rendre(texte, doc) {
    const lignes = String(texte == null ? "" : texte).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lignes.length) {
      const l = lignes[i];
      let m;
      if (!l.trim()) { i++; continue; }
      if ((m = l.match(CLOTURE))) {
        const marque = m[1];
        const corps = [];
        i++;
        while (i < lignes.length && !(lignes[i].trim().startsWith(marque[0].repeat(marque.length))
                                      && /^\s{0,3}(`{3,}|~{3,})\s*$/.test(lignes[i]))) {
          corps.push(lignes[i]);
          i++;
        }
        i++; // la clôture (ou la fin du texte)
        const pre = doc.createElement("pre");
        const code = doc.createElement("code");
        code.textContent = corps.join("\n");
        pre.appendChild(code);
        out.push(pre);
      } else if ((m = l.match(TITRE))) {
        out.push(remplir(doc, doc.createElement("h" + m[1].length), m[2]));
        i++;
      } else if (FILET.test(l)) {
        out.push(doc.createElement("hr"));
        i++;
      } else if (estTableau(lignes, i)) {
        const [t, j] = tableau(doc, lignes, i);
        out.push(t);
        i = j;
      } else if (CITATION.test(l)) {
        const corps = [];
        while (i < lignes.length && (m = lignes[i].match(CITATION))) { corps.push(m[1]); i++; }
        const q = doc.createElement("blockquote");
        for (const e of rendre(corps.join("\n"), doc)) q.appendChild(e);
        out.push(q);
      } else if (LISTE.test(l)) {
        const [li, j] = liste(doc, lignes, i);
        out.push(li);
        i = j;
      } else {
        const p = doc.createElement("p");
        let premiere = true;
        while (i < lignes.length && lignes[i].trim() && (premiere || !debutDeBloc(lignes, i))) {
          if (!premiere) p.appendChild(doc.createElement("br"));
          remplir(doc, p, lignes[i].trim());
          premiere = false;
          i++;
        }
        out.push(p);
      }
    }
    return out;
  }

  const API = { rendre };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else racine.MD = API;
})(typeof window !== "undefined" ? window : this);
