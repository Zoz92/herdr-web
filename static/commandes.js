"use strict";
// Filtrage des commandes « / » de Claude Code pour l'autocomplétion du champ de saisie.
// Pur : aucune dépendance au DOM (testé sous node par tests/test_page.py).
(function (racine) {
  // Rend les commandes qui correspondent à la saisie, les meilleures d'abord ; [] hors « / ».
  function filtrer(liste, saisie, max) {
    max = max || 50;
    if (typeof saisie !== "string" || saisie[0] !== "/" || saisie.includes("\n")) return [];
    const q = saisie.slice(1).toLowerCase();
    if (/\s/.test(q) && !/^model\s/.test(q)) return []; // après un espace : les arguments, sauf /model
    const notes = [];
    for (const c of liste || []) {
      const n = String((c && c.name) || "").toLowerCase();
      if (!n) continue;
      let note;
      if (!q || n.startsWith(q)) note = 0;
      else if (n.split(/[-_: ]/).some((m) => m.startsWith(q))) note = 1;
      else if (n.includes(q)) note = 2;
      else continue;
      notes.push([note, n.length, n, c]);
    }
    notes.sort((a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0));
    return notes.slice(0, max).map((x) => x[3]);
  }

  const API = { filtrer };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else racine.COMMANDES = API;
})(typeof window !== "undefined" ? window : this);
