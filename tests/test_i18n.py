"""Langue d'affichage (brief langue-web, 2026-11-15) : anglais par défaut, français au choix.

Gardes :
- les deux dictionnaires (static/i18n/en.js, fr.js) ont les MÊMES clés et les mêmes paramètres « {x} » ;
  idem pour les textes du serveur (herdr_web/textes.py) ;
- chaque clé citée par t("…") / tn("…") dans app.js et palette.js existe ;
- sonde statique : aucun texte visible écrit en dur dans app.js / palette.js (enfant d'el(), title,
  aria-label, placeholder, alt, textContent, statut(), helpers de rendu…) ;
- sonde dynamique : chaque scénario du banc rejoué dans une langue, relevé de chaque texte créé — aucune
  phrase propre à l'autre langue, aucune clé brute (une clé absente s'affiche telle quelle).
Chaque garde a sa preuve rouge (mutation) plus bas."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from herdr_web import textes

ICI = Path(__file__).parent
STATIC = ICI.parent / "static"
I18N = STATIC / "i18n"


def node(code):
    n = shutil.which("node")
    assert n, "node absent : il est dans le devShell (flake.nix), lancer via nix develop"
    r = subprocess.run([n, "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def dicos(dossier=I18N):
    return node(f"process.stdout.write(JSON.stringify({{ en: require({json.dumps(str(dossier / 'en.js'))}), "
                f"fr: require({json.dumps(str(dossier / 'fr.js'))}) }}))")


PARAM = re.compile(r"\{(\w+)\}")


def ecarts_cles(d):
    """Liste des écarts entre les langues : clés absentes d'un côté, paramètres différents, valeur vide."""
    out = []
    langues = list(d)
    toutes = set().union(*(set(d[l]) for l in langues))
    for k in sorted(toutes):
        for l in langues:
            if k not in d[l]:
                out.append(f"{k} absente de {l}")
            elif not isinstance(d[l][k], str) or not d[l][k].strip():
                out.append(f"{k} vide en {l}")
        if all(k in d[l] for l in langues):
            params = {l: sorted(PARAM.findall(d[l][k])) for l in langues}
            if len({tuple(p) for p in params.values()}) > 1:
                out.append(f"{k} : paramètres {params}")
    return out


def test_memes_cles_et_memes_parametres_dans_les_deux_dictionnaires():
    d = dicos()
    assert len(d["en"]) > 300 and list(d["en"]) == list(d["fr"])  # même ordre : un diff se lit ligne à ligne
    assert ecarts_cles(d) == []


def test_preuve_rouge_cle_manquante_ou_parametre_different(tmp_path):
    for f in ("en.js", "fr.js", "i18n.js"):
        shutil.copy(I18N / f, tmp_path / f)
    en = (tmp_path / "en.js").read_text()
    (tmp_path / "en.js").write_text(en.replace('  "carte.plier": "Collapse",\n', "")
                                    .replace('"Close {nom}"', '"Close {name}"'))
    e = ecarts_cles(dicos(tmp_path))
    assert "carte.plier absente de en" in e
    assert any(x.startswith("carte.fermerNom : paramètres") for x in e)


def test_textes_du_serveur_memes_cles_et_parametres():
    assert set(textes.TEXTES) == set(textes.LANGUES) and textes.DEFAUT == "en"
    assert ecarts_cles(textes.TEXTES) == []
    assert ecarts_cles({"en": {"a": "x {n}"}, "fr": {"a": "x"}}) != []  # la garde mord


# ---------- clés citées dans le code ----------
CITE = re.compile(r'\b(tn?)\(\s*"([^"]+)"')


def cles_citees(src):
    out = set()
    for f, k in CITE.findall(src):
        out |= {k + ".1", k + ".n"} if f == "tn" else {k}
    return out


def manquantes(cles, connues):
    """Clés citées absentes ; « etat. » (t("etat." + k)) : au moins une clé connue sous ce préfixe."""
    return sorted(k for k in cles if (not any(c.startswith(k) for c in connues) if k.endswith(".") else k not in connues))


def test_chaque_cle_citee_existe():
    d = dicos()
    for f in ("app.js", "palette.js"):
        cles = cles_citees((STATIC / f).read_text())
        assert len(cles) > (100 if f == "app.js" else 20), f
        assert manquantes(cles, set(d["en"])) == [], f
    assert manquantes({"etat.", "zzz."}, set(d["en"])) == ["zzz."]
    assert cles_citees('t("pas.une.cle") + tn("x.y", 2)') - set(d["en"]) == {"pas.une.cle", "x.y.1", "x.y.n"}


# ---------- sonde statique : aucun texte visible en dur ----------
def code_seul(src):
    """Le source sans commentaires (chaînes et expressions régulières gardées telles quelles)."""
    out, i, n = [], 0, len(src)
    dernier = ""
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i, dernier = j + 1, c
        elif src.startswith("//", i):
            i = src.find("\n", i) if src.find("\n", i) >= 0 else n
        elif src.startswith("/*", i):
            i = src.index("*/", i) + 2
        elif c == "/" and (dernier in "(,=:[!&|?{};+" or dernier == ""):  # expression régulière
            j, classe = i + 1, False
            while j < n and (src[j] != "/" or classe):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    classe = True
                elif src[j] == "]":
                    classe = False
                j += 1
            out.append(src[i:j + 1])
            i, dernier = j + 1, "/"
        else:
            out.append(c)
            if not c.isspace():
                dernier = c
            i += 1
    return "".join(out)


def arguments(src, debut):
    """Les arguments (texte source) de l'appel dont la parenthèse ouvrante est à `debut`."""
    args, prof, i, cour = [], 0, debut + 1, []
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
            cour.append(src[i:j + 1])
            i = j + 1
            continue
        if c in "([{":
            prof += 1
        elif c in ")]}":
            if prof == 0:
                args.append("".join(cour).strip())
                return args
            prof -= 1
        elif c == "," and prof == 0:
            args.append("".join(cour).strip())
            cour = []
            i += 1
            continue
        cour.append(c)
        i += 1
    raise ValueError("appel non fermé")


LITTERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`')
MOT = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


def sans_imbrications(a):
    """L'argument sans ses appels imbriqués (el() est sondé à part ; un helper qui rend du texte est un puits
    de PUITS ; les autres reçoivent des classes, des états, des noms de touches) ni ses objets { … }
    (propriétés : sondées par PROPRIETES). t("clé") part avec eux."""
    while True:
        m = re.search(r"(?<![\w$.])[\w$.]+\(|\{", a)
        if not m:
            return a
        debut = m.end() - 1
        prof, i = 0, debut
        while i < len(a):
            c = a[i]
            if c in "\"'`":
                j = i + 1
                while a[j] != c:
                    j += 2 if a[j] == "\\" else 1
                i = j + 1
                continue
            prof += c in "([{"
            prof -= c in ")]}"
            i += 1
            if prof == 0:
                break
        a = a[:m.start()] + " _ " + a[i:]


CLE_POINTEE = re.compile(r'^"[a-z][\w-]*(\.[\w+-]+)+"$')


def textes_en_dur(arg):
    """Littéraux porteurs d'un mot dans un argument rendu à l'écran, hors clés de t() (« a.b ») et opérandes de
    comparaison (« a.state === "blocked" »), hors appels el()/svg() imbriqués (sondés à part)."""
    a = sans_imbrications(arg)
    a = re.sub(r'\b(tn?|existe|I18N\.existe)\(\s*"[^"]*"', "", a)
    a = re.sub(r'(===|!==|==|!=)\s*"[^"]*"|"[^"]*"\s*(===|!==|==|!=)', "", a)
    a = re.sub(r'\.(includes|startsWith|endsWith|getAttribute|querySelector|querySelectorAll|closest|contains)\(\s*"[^"]*"', "", a)
    return [m for m in LITTERAL.findall(a) if MOT.search(m) and not CLE_POINTEE.match(m)]


# fonction -> positions des arguments rendus à l'écran (None : toutes à partir de la 3e, les enfants d'el())
PUITS = {
    "app.js": {"el": None, "statut": [0], "envoi": [0], "ligneMenu": [1, 2], "ligneReglage": [0, 1],
               "interrupteur": [0, 1], "segmente": [0], "titreCarte": [0], "boutonPlus": [0], "valeurReglage": [0, 1],
               "ligneChemin": [0, 2], "teteGroupe": [1], "groupe": [1], "remplacer": "reste", "annoncer": [], "kbd": [0]},
    "palette.js": {"ajout": [1, 2]},
}
PROPRIETES = [
    re.compile(r'\b(title|ariaLabel|placeholder|alt|label|raison)\s*:\s*("(?:[^"\\]|\\.)*")'),
    re.compile(r'\.(textContent|title|placeholder|alt)\s*=\s*("(?:[^"\\]|\\.)*")'),
    re.compile(r'setAttribute\(\s*"(title|aria-label|placeholder|alt)"\s*,\s*("(?:[^"\\]|\\.)*")'),
    re.compile(r'\b(return)\s+("(?:[^"\\]|\\.)*"\s*\+)'),  # « return "Sous Firefox, " + … »
]


def sonde(src, puits):
    code = code_seul(src)
    fautes = []
    for nom, pos in puits.items():
        for m in re.finditer(r"(?<![\w.$])" + re.escape(nom) + r"\(", code):
            args = arguments(code, m.end() - 1)
            if pos is None:
                vus = args[2:]
            elif pos == "reste":
                vus = args[1:]
            else:
                vus = [args[i] for i in pos if i < len(args)]
            for a in vus:
                fautes += [f"{nom}(…{x}…)" for x in textes_en_dur(a)]
    for motif in PROPRIETES:
        fautes += [f"{m.group(1)} {m.group(2)}" for m in motif.finditer(code) if MOT.search(m.group(2))]
    return fautes


@pytest.mark.parametrize("fichier", ["app.js", "palette.js"])
def test_aucun_texte_visible_en_dur(fichier):
    assert sonde((STATIC / fichier).read_text(), PUITS[fichier]) == []


@pytest.mark.parametrize("fichier,avant,apres,attendu", [
    ("app.js", 't("commun.annuler"))', '"Annuler")', 'el(…"Annuler"…)'),                       # enfant d'el()
    ("app.js", 'title: t("carte.fermer"),', 'title: "Close…",', 'title "Close…"'),             # propriété
    ("app.js", 'statut(t("micro.refuse"), true)', 'statut("micro refusé", true)', 'statut(…"micro refusé"…)'),
    ("app.js", 'noteEl.textContent = t("fil.terminalTermine");', 'noteEl.textContent = "terminal ended";',
     'textContent "terminal ended"'),
    ("app.js", 'groupe("pinned", t("groupe.pinned"),', 'groupe("pinned", "Pinned",', 'groupe(…"Pinned"…)'),
    ("app.js", '"↹ " + t("sugg.puce"));', '"↹ suggestion");', 'el(…"↹ suggestion"…)'),        # concaténation
    ("palette.js", 't("palette.nouveauTerminal")', '"New terminal"', 'ajout(…"New terminal"…)'),
    ("palette.js", 'raison: t("raccourci.navigateur")', 'raison: "taken by the browser"', 'raison "taken by the browser"'),
])
def test_preuve_rouge_de_la_sonde_statique(fichier, avant, apres, attendu):
    src = (STATIC / fichier).read_text()
    assert avant in src, avant
    assert attendu in sonde(src.replace(avant, apres, 1), PUITS[fichier])


def test_sonde_ignore_cles_comparaisons_et_commentaires():
    src = '// el("div", {}, "Commentaire")\nel("span", {}, a === "blocked" ? t("etat.blocked") : "", "! ", x.includes("Type something."));'
    assert sonde(src, PUITS["app.js"]) == []


# ---------- sonde dynamique : tous les scénarios du banc, dans chaque langue ----------
def releve(scenario, langue, tmp_path, app=None):
    """Textes créés pendant le scénario (le scénario peut échouer : ses assertions visent souvent le français)."""
    f = tmp_path / f"releve-{scenario}-{langue}.json"
    argv = [shutil.which("node"), str(ICI / "page_banc.js"), scenario] + ([str(app)] if app else [])
    subprocess.run(argv, capture_output=True, text=True, timeout=60,
                   env=dict(os.environ, HERDR_LANGUE=langue, HERDR_RELEVE=str(f)))
    return json.loads(f.read_text()) if f.exists() else []


def scenarios():
    r = subprocess.run([shutil.which("node") or "node", str(ICI / "page_banc.js"), "--liste"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else []


def propres(d, langue, autre):
    """Phrases propres à une langue : absentes des valeurs de l'autre (« New space » est aussi un libellé
    français), sans paramètre, assez longues pour ne pas se croiser par hasard."""
    return {v.strip() for v in d[langue].values() if v not in d[autre].values() and len(v.strip()) >= 6
            and not PARAM.search(v)}


def trouve(phrase, texte):
    """Une phrase de plusieurs mots : n'importe où, entre bornes de mots. Un mot seul : tout le texte, et
    7 lettres au moins — sinon un mot d'agent (« Cancel » d'un menu de Claude) se lirait comme une fuite."""
    if " " not in phrase:
        return texte.strip() == phrase and len(phrase) >= 7
    return re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", texte) is not None


def fautes_releve(textes_vus, d, langue):
    autre = "fr" if langue == "en" else "en"
    etrangers = propres(d, autre, langue)
    cles = set(d["en"])
    out = []
    for x in textes_vus:
        out += [f"« {e} » dans « {x[:80]} »" for e in etrangers if trouve(e, x)]
        if x.strip() in cles:
            out.append(f"clé brute « {x} »")
    return sorted(set(out))


@pytest.fixture(scope="module")
def d():
    return dicos()


@pytest.mark.parametrize("langue", ["en", "fr"])
def test_aucun_texte_de_l_autre_langue_dans_aucun_scenario(langue, d, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("releve-" + langue)
    noms = scenarios()
    assert len(noms) > 30
    vus, total = [], 0
    for s in noms:
        r = releve(s, langue, tmp)
        total += len(r)
        vus += [f"{s} : {f}" for f in fautes_releve(r, d, langue)]
    assert total > 2000  # le relevé a bien vu les scénarios
    assert vus == []


def test_preuve_rouge_de_la_sonde_dynamique(d, tmp_path):
    src = (STATIC / "app.js").read_text()
    mute = tmp_path / "app.js"
    mute.write_text(src.replace('t("reglages.titre")', '"Réglages"').replace('t("live.suivi")', '"live.suivi"'))
    vus = releve("reglages", "en", tmp_path, mute) + releve("vu", "en", tmp_path, mute)
    f = fautes_releve(vus, d, "en")
    assert any("« Réglages »" in x for x in f), f
    assert "clé brute « live.suivi »" in f, f
    assert fautes_releve(releve("reglages", "en", tmp_path) + releve("vu", "en", tmp_path), d, "en") == []  # témoin


# ---------- la page en anglais (langue par défaut) et en français ----------
def banc(scenario, langue):
    r = subprocess.run([shutil.which("node"), str(ICI / "page_banc.js"), scenario], capture_output=True, text=True,
                       timeout=60, env=dict(os.environ, HERDR_LANGUE=langue))
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def test_textes_calcules_en_anglais():
    x = banc("textes", "en")
    assert x["depuis"] == ["42 s ago", "3 min ago", "2 h ago", "3 d ago"]
    assert x["ctx"] == "context: 123,456 tokens — compaction at 200k (62%)"
    assert x["terminaux"] == ["0 terminals", "1 terminal", "2 terminals"] and x["outils"] == ["1 tool", "3 tools"]
    assert x["erreurs"] == ["folder missing or outside /home/x", "voice model: invalid identifier", "unknown tab", "agent gone",
                            "the request has changed", "hc kill failed: terminal occupé", "network unreachable", "error 418",
                            "texte d'un serveur d'avant", "x"]  # 502 : la sortie du programme suit telle quelle ;
    # serveur d'avant (sans motif) ou motif inconnu : son texte, faute de mieux
    assert x["binaire"] == ["binary not found: handy", "texte d'avant"]
    assert x["avertissement"] == "model “whisper” refused by Handy: transcribed with the model chosen in Handy"
    assert x["nomParDefaut"] == "You" and x["heure"] == "15:05"  # 24 h, heure de Paris, dans les deux langues


def test_textes_calcules_en_francais_comme_avant():
    x = banc("textes", "fr")
    assert x["depuis"] == ["depuis 42 s", "depuis 3 min", "depuis 2 h", "depuis 3 j"]
    assert x["ctx"] == "contexte : 123 456 tokens — compaction à 200k (62 %)"
    assert x["terminaux"] == ["0 terminal", "1 terminal", "2 terminaux"] and x["outils"] == ["1 outil", "3 outils"]
    assert x["erreurs"][:3] == ["dossier absent ou hors de /home/x", "voix.modele : identifiant invalide", "onglet inconnu"]
    assert x["erreurs"][5] == "hc kill a échoué : terminal occupé"
    assert x["binaire"][0] == "binaire introuvable : handy" and x["nomParDefaut"] == "Vous"


def test_reglages_en_anglais():
    r = banc("reglages", "en")
    assert r["titres"] == ["Voice", "Notifications", "Display", "Devices", "About"]
    lg = r["lignes"]
    assert lg["Page language"] == "segmente" and lg["Voice enabled"] == "switch" and lg["Default sort"] == "segmente"
    assert lg["Cookie lifetime"] == ["365 days", "one shared token; no list of sessions on the server"]
    assert (lg["Handy 0.9.8"], lg["Maximum delay"], lg["ffmpeg"]) == ("● ready", "90 s", "✓ found")
    assert r["pastille"] == "○ disabled" and r["notifs"] == [["Needs you: agent-t1", "t1"]]
    assert r["question"] == "Sign out of this device?" and r["focusAnnuler"] == "Cancel"
    assert r["logout"] == ["POST /api/logout"] and r["ancien"] == "settings unreadable (server too old or unreachable)"
    assert r["fautesVide"] == [] and r["versionVide"] == "unknown version"


def test_fermer_et_appairage_en_anglais():
    f = banc("fermer", "en")
    assert f["texte"].startswith("Close “maker-1”?") and "It is still working: its current turn will be lost." in f["texte"]
    assert f["apresFermer"] == {"voile": False, "hash": "#/"}
    a = banc("appairage", "en")
    assert a["qr"] is True and a["expire"] == "Link expired: create a new one."


def test_selecteur_de_langue_et_rechargement():
    x = banc("langue", "fr")
    assert x["ligne"] == ["Langue de la page", "Vaut pour tous les appareils."]
    # chaque langue par son propre nom, la langue en cours cochée
    assert x["options"] == [["en", "English", "false"], ["fr", "Français", "true"]]
    assert x["memeLangue"] == 0                      # même langue : pas de rechargement
    assert x["appel"] == [{"langue": "en"}]          # écrit au serveur (partagé entre appareils)
    assert x["message"] == "enregistré : la page se recharge" and x["apresClic"] == 1
    assert x["pendantSaisie"] == 0 and x["apresEnvoi"] == 1  # brouillon : rechargé après l'envoi seulement
    assert x["inconnue"] == 0                        # langue inconnue : ignorée
    assert banc("langue", "en")["ligne"] == ["Page language", "Applies to all devices."]


def test_preuve_rouge_rechargement_garde_le_brouillon(tmp_path):
    src = (STATIC / "app.js").read_text()
    avant = "  const brouillon = S.vue && S.vue.champ && S.vue.champ.value.trim();\n  if (!brouillon) location.reload();\n}"
    assert src.count(avant) == 1
    mute = tmp_path / "app.js"
    mute.write_text(src.replace(avant, "  location.reload();\n}"))
    r = subprocess.run([shutil.which("node"), str(ICI / "page_banc.js"), "langue", str(mute)], capture_output=True,
                       text=True, timeout=60, env=dict(os.environ, HERDR_LANGUE="fr"))
    assert json.loads(r.stdout)["pendantSaisie"] == 1  # la mutation recharge sur le brouillon : la garde mord


def test_index_html_sans_texte_et_scripts_dans_l_ordre():
    html = (STATIC / "index.html").read_text()
    assert '<html lang="en">' in html  # le serveur y pose la langue choisie
    corps = re.sub(r"<svg.*?</svg>|<script.*?</script>|<[^>]+>", " ", html.split("<body>")[1], flags=re.S)
    assert not MOT.search(corps), corps  # aucun texte : data-i18n
    ordre = [html.index(f'/static/i18n/{f}"') for f in ("en.js", "fr.js", "i18n.js")]
    assert ordre == sorted(ordre) and ordre[-1] < html.index("/static/md.js") < html.index("/static/palette.js")
    assert re.findall(r'(?<![-\w])(title|aria-label|placeholder)="[^"]*"', html) == []
    for k in re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html):
        assert k in dicos()["en"], k
    sw = (STATIC / "sw.js").read_text()
    assert all(f'"/static/i18n/{f}"' in sw for f in ("en.js", "fr.js", "i18n.js"))  # coquille hors ligne
