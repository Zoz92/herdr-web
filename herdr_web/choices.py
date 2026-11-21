"""Choix proposés à l'écran d'un agent bloqué, et réponse gardée."""
import json
import re
from dataclasses import dataclass

_CHOICE = re.compile(r"^\s*(?:❯\s*)?(\d)\.\s+(.+?)\s*$")


class ChoiceStale(Exception):
    """L'écran n'est plus celui que l'utilisateur a vu : on n'envoie rien."""


@dataclass
class Choice:
    digit: str
    label: str
    detail: str
    preview: str = ""  # aperçu de l'option (AskUserQuestion), lu dans le journal de la session


# Question à aperçu (AskUserQuestion dont une option porte `preview`), mesurée sur un vrai TUI
# 2.1.325 le 2026-11-15 (tests/fixtures/ecran_apercu_*.txt) : les options à gauche, la boîte de
# l'aperçu de l'option sous le curseur à droite, sur les mêmes lignes ; ni description ni
# « Type something. », « Notes: press n to add notes » dessous, « Chat about this » sans numéro.
# Dans ce dialogue un chiffre ne fait que déplacer le curseur ; seul Entrée répond.
_COLONNE = re.compile(r"\s{3,}|[│┌┐└┘├┤┬┴┼─╭╮╰╯║═]")
_NOTES = "n to add notes"


def _nettoyer(texte: str) -> str:
    """Le texte de la colonne de gauche : coupé au premier grand blanc ou trait de boîte."""
    return _COLONNE.split(texte.strip(), 1)[0].strip()


def est_apercu(screen: str) -> bool:
    """Le dialogue en bas de l'écran est celui à aperçu : sa ligne d'aide propose « n to add notes ».
    Cherchée seulement à partir du dernier bloc de choix, jamais dans le transcript au-dessus (qui
    peut citer cette chaîne : réserve P1 du checker de la PR #27)."""
    lignes = screen.splitlines()
    _, debut = _dernier_bloc(lignes)
    return debut >= 0 and any(_NOTES in l for l in lignes[debut:])


_SOUS_CURSEUR = re.compile(r"^\s*❯\s*(\d)\.")


def curseur(screen: str) -> str | None:
    """Le chiffre de la ligne sous le curseur (❯) dans le dernier bloc de choix, sinon None."""
    lignes = screen.splitlines()
    _, debut = _dernier_bloc(lignes)
    if debut < 0:
        return None
    for l in lignes[debut:]:
        m = _SOUS_CURSEUR.match(l)
        if m:
            return m.group(1)
    return None


# Panneau de /btw (question à côté), mesuré le 2026-11-17 sur Claude Code 2.1.326 : son pied, en
# dernière ligne, finit par « Esc to close » (« Esc to close » seul pendant la réponse, puis
# « c to copy · f to fork · Esc to close »). Un événement de fond pendant qu'il est ouvert passe
# l'agent en `blocked` (« dialog open ») ; la liste numérotée d'une réponse se lisait alors comme
# des choix, et Échap, seule sortie, était refusée.
_PIED_BTW = re.compile(r"(?:^|·)\s*Esc to close$")


def panneau_btw(screen: str) -> bool:
    lignes = [l.strip() for l in screen.splitlines() if l.strip()]
    return bool(lignes) and bool(_PIED_BTW.search(lignes[-1]))


def parse_choices(screen: str) -> list[Choice]:
    """Le dernier bloc de lignes numérotées 1, 2, 3… de l'écran.

    Entre deux choix : des lignes vides, et au plus une ligne indentée sans numéro posée
    juste sous le choix (son detail). Toute autre ligne non indentée ferme le bloc ; un
    nouveau « 1. » en ouvre un autre. Libellé et detail sont coupés à la colonne d'aperçu.
    """
    return _dernier_bloc(screen.splitlines())[0]


def _plat(t: str) -> str:
    return " ".join(t.split())


def question_affichee(questions: list[dict] | None, screen: str) -> dict | None:
    """La question du journal que l'écran montre : son texte (blancs aplatis, l'écran le replie)
    figure au-dessus du bloc de choix, ET ses options sont celles de l'écran (`choix_structures`).
    Plusieurs candidates aux options différentes (même texte : réserve P3 du checker) : None, la
    page se replie sur l'écran plutôt que de montrer les options d'une autre question."""
    if not questions:
        return None
    contexte = _plat(parse_contexte(screen, max_lignes=40))
    cs = parse_choices(screen)
    vues = [q for q in questions if q.get("question") and _plat(q["question"]) in contexte
            and choix_structures(q, cs) is not None]
    if not vues:
        return None
    longueur = max(len(q["question"]) for q in vues)
    vues = [q for q in vues if len(q["question"]) == longueur]  # la plus précise
    formes = {json.dumps([(o["label"], o.get("description", ""), o.get("preview", "")) for o in q["options"]])
              for q in vues}
    return vues[0] if len(formes) == 1 else None


def _meme_libelle(ecran: str, journal: str) -> bool:
    """Le libellé lu à l'écran (coupé à la colonne d'aperçu, parfois replié) et celui du journal."""
    e, j = _plat(ecran), _plat(journal)
    return bool(e) and bool(j) and (j.startswith(e) or e.startswith(j))


def choix_structures(question: dict, ecran: list[Choice]) -> list[Choice] | None:
    """Choix de la question du journal : libellé, description et aperçu exacts, chiffres de
    l'écran. Question à cases : la case lue à l'écran (« [ ] » / « [✔] ») précède le libellé. Les
    lignes de l'écran au-delà des options (« Type something. ») restent celles de l'écran. None si
    l'écran ne porte pas, pour chaque option, un chiffre au même libellé (ce n'est pas cette
    question)."""
    opts = question.get("options") or []
    par_chiffre = {c.digit: c for c in ecran}
    out = []
    for i, o in enumerate(opts, 1):
        vu = par_chiffre.get(str(i))
        if vu is None:
            return None
        label, lu = o["label"], vu.label
        if question.get("multiSelect"):
            case = re.match(r"\[.\] ", vu.label)
            if not case:
                return None
            label, lu = case.group(0) + label, vu.label[len(case.group(0)):]
        if not _meme_libelle(lu, o["label"]):
            return None
        out.append(Choice(str(i), label, o.get("description", ""), o.get("preview", "")))
    return out + [c for c in ecran if int(c.digit) > len(opts)]


def _dernier_bloc(lines: list[str]) -> tuple[list[Choice], int]:
    """(choix, rang de la ligne de son « 1. ») du dernier bloc ; ([], -1) sans bloc."""
    last: list[Choice] = []
    debut, debut_last = -1, -1
    block: list[Choice] = []
    directly_below = False  # la ligne précédente est un choix sans detail encore
    for n, line in enumerate(lines):
        m = _CHOICE.match(line)
        if m:
            digit, label = m.group(1), m.group(2)
            if digit == "1":
                block = []
                debut = n
            if digit == str(len(block) + 1):
                block.append(Choice(digit, _nettoyer(label), ""))
                last, debut_last = block, debut
                directly_below = True
                continue
            block = []  # numéro hors suite : le bloc est cassé
            directly_below = False
            continue
        if not line.strip():
            directly_below = False
            continue
        if line[0] in " \t":
            if directly_below and block and not block[-1].detail:
                block[-1].detail = _nettoyer(line)  # une ligne d'aperçu seule ne laisse rien
            directly_below = False
            continue
        block = []
        directly_below = False
    return last, debut_last


# Filet horizontal d'un dialogue de Claude Code (« ──── », « ╭──── ») : le contexte s'arrête là.
_FILET = re.compile(r"^\s*[╭╰├┌└]?─{8,}")


def parse_contexte(screen: str, max_lignes: int = 6) -> str:
    """Ce que l'écran dit au-dessus du dernier bloc de choix : la question, et pour un
    AskUserQuestion la barre d'onglets « ← ☒ A  ☐ B  ✔ Submit → » (☒ = déjà répondue). Remonte
    jusqu'au filet du dialogue, au plus `max_lignes` lignes non vides ; "" sans bloc.

    Sert aussi de repère à la page : d'une question à la suivante l'agent reste « blocked »
    sans événement, seul ce texte (avec les choix) dit que l'écran a tourné."""
    lines = screen.splitlines()
    _, debut = _dernier_bloc(lines)
    if debut < 0:
        return ""
    out: list[str] = []
    for line in reversed(lines[:debut]):
        if _FILET.match(line) or len(out) >= max_lignes:
            break
        if line.strip():
            out.append(line.strip())
    return "\n".join(reversed(out))


def answer(hc, address: str, digit: str, expected_msg: str, entree: bool = False) -> None:
    """`entree` : dialogue à aperçu, où le chiffre ne fait que poser le curseur — Entrée répond
    (mesuré sur un vrai TUI 2.1.325 le 2026-11-15 : chiffre seul ⇒ question toujours là)."""
    if len(digit) != 1 or not ("1" <= digit <= "9"):
        raise ValueError(f"touche de choix invalide : {digit!r}")
    _garde_statut(hc, address, expected_msg)
    hc.request("terminal.keys", {"terminal": address, "keys": [digit, "enter"] if entree else [digit]})


# Saisie libre d'un AskUserQuestion (« Type something. », dernière option avant « Chat about this »).
# Mesuré dans Claude Code 2.1.325 (Select, option `type: "input"`) : son chiffre, champ vide, ne fait
# que poser le curseur sur le champ (`focusOption`) — aucune réponse ; Entrée sur le champ non vide
# répond (`onSubmit` → `onChange("__other__")`), puis la question suivante vient comme après une
# autre option. Le texte ne passe pas par `terminal.keys` (noms de touches seulement : ni espace
# littéral, ni accent, ni ponctuation) : il est collé par `terminal.send` forcé, qui colle puis
# appuie sur Entrée. Un collage sur une autre ligne du dialogue va de toute façon au champ
# (`takesPasteFromOtherRows`).
#
# Question à cases (multiSelect), mesuré sur un vrai TUI 2.1.325 le 2026-11-15 : la ligne s'appelle
# « Type something » (sans point) et porte une case (`3. [ ] Type something`). Chiffre ⇒ la ligne
# est COCHÉE, le curseur reste où il était (un 2e chiffre la décocherait) ; texte collé + Entrée ⇒
# le texte remplace le libellé, curseur sur la ligne, case DÉCOCHÉE (`❯ 3. [ ] Zig`) ; un Entrée de
# plus ⇒ `❯ 3. [✔] Zig`. Les autres cases restent comme elles étaient. Choix unique : chiffre ⇒
# curseur sur `❯ 3. Type something.` ; un 2e chiffre s'écrirait dans le champ.
SAISIE_LIBRE = "Type something."
SAISIE_LIBRE_CASE = "[ ] Type something"
CASE_VIDE, CASE_COCHEE = "[ ] ", "[✔] "
TEXTE_MAX = 4000
SEND_TIMEOUT = 90  # comme actions.SEND_TIMEOUT : terminal.send peut attendre un brouillon (60 s)


def est_saisie_libre(c: Choice) -> bool:
    return c.label in (SAISIE_LIBRE, SAISIE_LIBRE_CASE)


def _garde_statut(hc, address: str, expected_msg: str) -> None:
    status = hc.request("terminal.get", {"terminal": address}).get("status")
    if not status or status.get("state") != "blocked" or status.get("msg") != expected_msg:
        raise ChoiceStale(address)


def answer_texte(hc, address: str, digit: str, expected_msg: str, texte: str, case: bool = False,
                 contexte: str | None = None) -> None:
    """Répond par du texte libre : chiffre de « Type something. » (le champ prend le curseur), puis
    le texte collé et Entrée. Même garde que `answer` : l'agent doit être `blocked` avec le msg vu.

    Le collage est FORCÉ (il répondrait à n'importe quel dialogue) : après le chiffre, l'écran est
    relu et doit montrer la même question (`contexte` de la page, s'il est donné), le curseur sur la
    ligne `digit`, et cette ligne doit être la saisie libre ; l'agent toujours `blocked` au même msg.
    Sinon ChoiceStale, rien n'est collé (réserve P2 du checker de la PR #27 : réponse donnée au
    terminal entre-temps, ou dialogue suivant déjà là — une permission « 1. Yes » focalisée).

    `case` (question à cases) : la ligne garde sa case vide après le collage ; un Entrée de plus la
    coche — seulement si l'écran relu montre encore cette ligne avec sa case VIDE (un Entrée sur une
    case déjà cochée la décocherait)."""
    if len(digit) != 1 or not ("1" <= digit <= "9"):
        raise ValueError(f"touche de choix invalide : {digit!r}")
    # une ligne : un saut de ligne collé hors du mode « bracketed paste » répondrait trop tôt
    texte = " ".join(texte.split())
    if not texte or len(texte) > TEXTE_MAX:
        raise ValueError("texte vide ou trop long")
    _garde_statut(hc, address, expected_msg)
    hc.request("terminal.keys", {"terminal": address, "keys": [digit]})
    apres = hc.request("terminal.read", {"terminal": address, "lines": 0})["text"]
    ligne = next((c for c in parse_choices(apres) if c.digit == digit), None)
    # ce que le chiffre a fait, mesuré sur un vrai TUI 2.1.325 (tests/fixtures/ecran_garde_*) :
    # choix unique ⇒ curseur sur « ❯ N. Type something. » ; à cases ⇒ « N. [✔] Type something »
    # cochée, curseur resté ailleurs (le collage la décoche puis y pose le curseur)
    if case:
        attendu = ligne is not None and ligne.label == CASE_COCHEE + SAISIE_LIBRE_CASE[len(CASE_VIDE):]
    else:
        attendu = ligne is not None and ligne.label == SAISIE_LIBRE and curseur(apres) == digit
    if not attendu or (contexte is not None and parse_contexte(apres) != contexte):
        raise ChoiceStale(address)
    _garde_statut(hc, address, expected_msg)
    hc.request("terminal.send", {"terminal": address, "text": texte, "force": True}, timeout=SEND_TIMEOUT)
    if not case:
        return
    ecran = hc.request("terminal.read", {"terminal": address, "lines": 0})["text"]
    ligne = next((c for c in parse_choices(ecran) if c.digit == digit), None)
    if ligne is not None and ligne.label.startswith(CASE_COCHEE):
        return  # déjà cochée : rien à faire
    if ligne is None or not ligne.label.startswith(CASE_VIDE):
        raise ChoiceStale(address)  # écran illisible ou parti : ne pas appuyer à l'aveugle
    hc.request("terminal.keys", {"terminal": address, "keys": ["enter"]})


# Menus à curseur sans numéros (dialogue de confiance de Claude Code, 2026-11-15) :
#   ❯ No, exit
#     Yes, I trust this folder
# une ligne marquée ❯, ses voisines indentées ; on y répond en déplaçant le curseur puis Entrée.
_CURSEUR = re.compile(r"^\s*❯\s+(\S.*?)\s*$")
_OPTION = re.compile(r"^\s{2,}(\S.*?)\s*$")


def parse_menu(screen: str) -> tuple[list[str], int] | None:
    """(options, rang du curseur) du dernier menu à curseur de l'écran, sinon None. Un menu a au
    moins deux options consécutives dont exactement une porte ❯ ; une option numérotée relève de
    parse_choices."""
    lignes = screen.splitlines()
    dernier = None
    i = 0
    while i < len(lignes):
        bloc, courant = [], None
        j = i
        while j < len(lignes):
            m = _CURSEUR.match(lignes[j])
            if m and courant is None:
                courant = len(bloc)
                bloc.append(m.group(1))
            elif (o := _OPTION.match(lignes[j])) and not _CURSEUR.match(lignes[j]):
                bloc.append(o.group(1))
            else:
                break
            j += 1
        if courant is not None and len(bloc) >= 2 and not any(re.match(r"\d\.\s", b) for b in bloc):
            dernier = (bloc, courant)
        i = j + 1 if j > i else i + 1
    return dernier


def answer_menu(hc, address: str, option: int, vues: list[str], screen: str) -> None:
    """Va de l'option sous le curseur à `option`, puis Entrée — seulement si l'écran porte encore
    exactement le menu que la personne a vu."""
    menu = parse_menu(screen)
    if menu is None or menu[0] != vues:
        raise ChoiceStale(address)
    options, courant = menu
    if not 0 <= option < len(options):
        raise ValueError(f"option hors menu : {option!r}")
    pas = option - courant
    touches = (["down"] * pas if pas > 0 else ["up"] * -pas) + ["enter"]
    hc.request("terminal.keys", {"terminal": address, "keys": touches})
