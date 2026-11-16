// Banc du rendu Markdown de static/md.js, sous node, avec un faux DOM minimal.
// Entrée : un tableau JSON de textes sur stdin. Sortie : un tableau JSON des rendus sérialisés.
// Le faux DOM lève sur innerHTML / outerHTML / insertAdjacentHTML : le rendu ne peut pas y passer.
"use strict";
const fs = require("fs");
const path = require("path");

const echapper = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");
const interdit = (nom) => () => { throw new Error(nom + " interdit"); };

class Texte {
  constructor(t) { this.nodeType = 3; this.data = String(t); }
  ser() { return echapper(this.data); }
  get textContent() { return this.data; }
}

class Element {
  constructor(tag) { this.nodeType = 1; this.tagName = tag.toUpperCase(); this.childNodes = []; this.attrs = {}; }
  appendChild(c) { this.childNodes.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? new Texte(c) : c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  set textContent(t) { this.childNodes = [new Texte(t)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set innerHTML(_) { interdit("innerHTML")(); }
  set outerHTML(_) { interdit("outerHTML")(); }
  ser() {
    const t = this.tagName.toLowerCase();
    const a = Object.keys(this.attrs).sort().map((k) => ` ${k}="${echapper(this.attrs[k])}"`).join("");
    if (t === "br" || t === "hr") return `<${t}${a}>`;
    return `<${t}${a}>${this.childNodes.map((c) => c.ser()).join("")}</${t}>`;
  }
}
Element.prototype.insertAdjacentHTML = interdit("insertAdjacentHTML");

const doc = {
  createElement: (t) => new Element(t),
  createTextNode: (t) => new Texte(t),
  write: interdit("document.write"),
};

const MD = require(path.join(__dirname, "..", "static", "md.js"));
const entrees = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(entrees.map((t) => MD.rendre(t, doc).map((n) => n.ser()).join(""))));
