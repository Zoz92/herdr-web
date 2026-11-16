"use strict";
// Langue d'affichage de la page : anglais par défaut, français au choix (Réglages › Affichage).
// Le choix est écrit côté serveur (~/.config/herdr-web/reglages.json), partagé entre les appareils ;
// le serveur le pose dans <html lang="…"> de la page qu'il sert, lu ici avant tout autre script.
// Les textes sont dans en.js et fr.js (mêmes clés) ; t(clé, params) remplace « {nom} » par params.nom.
// Sans DOM (node : tests/test_palette.py), la langue se pose par I18N.poser().
(function (racine) {
  const enModule = typeof module !== "undefined" && module.exports;
  const DICOS = enModule ? { en: require("./en.js"), fr: require("./fr.js") } : racine.HERDR_DICOS || {};
  const LANGUES = ["en", "fr"];
  const DEFAUT = "en";
  let langue = DEFAUT;

  function poser(l) {
    langue = LANGUES.includes(l) ? l : DEFAUT;
    return langue;
  }
  const courante = () => langue;

  // une clé absente de la langue choisie retombe sur l'anglais, puis sur la clé elle-même (visible : à corriger)
  function t(cle, params) {
    const d = DICOS[langue] || {};
    let s = Object.prototype.hasOwnProperty.call(d, cle) ? d[cle] : (DICOS[DEFAUT] || {})[cle];
    if (typeof s !== "string") return cle;
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] == null ? m : String(params[k])));
    return s;
  }
  const existe = (cle) => typeof (DICOS[langue] || {})[cle] === "string" || typeof (DICOS[DEFAUT] || {})[cle] === "string";
  // nombre : « .1 » ou « .n » selon la règle de la langue (français : 0 et 1 au singulier)
  function tn(cle, n, params) {
    const pluriel = langue === "fr" ? Math.abs(n) >= 2 : n !== 1;
    return t(cle + (pluriel ? ".n" : ".1"), Object.assign({ n }, params || {}));
  }
  // locale des nombres et des heures (heure sur 24 h dans les deux langues)
  const locale = () => (langue === "fr" ? "fr-FR" : "en-GB");

  // index.html : data-i18n (texte), data-i18n-title, data-i18n-aria-label, data-i18n-placeholder
  const ATTRS = [["i18nTitle", "title"], ["i18nAriaLabel", "aria-label"], ["i18nPlaceholder", "placeholder"]];
  function traduireDom(r) {
    if (!r || typeof r.querySelectorAll !== "function") return;
    for (const n of r.querySelectorAll("[data-i18n], [data-i18n-title], [data-i18n-aria-label], [data-i18n-placeholder]")) {
      const ds = n.dataset || {};
      if (ds.i18n) n.textContent = t(ds.i18n);
      for (const [k, attr] of ATTRS) if (ds[k]) n.setAttribute(attr, t(ds[k]));
    }
  }

  if (!enModule && racine.document && racine.document.documentElement) {
    poser(racine.document.documentElement.getAttribute("lang"));
  }
  const API = { LANGUES, DEFAUT, poser, courante, t, tn, existe, locale, traduireDom, DICOS };
  if (enModule) module.exports = API;
  else racine.I18N = API;
})(typeof window !== "undefined" ? window : this);
