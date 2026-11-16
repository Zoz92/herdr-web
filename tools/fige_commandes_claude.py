"""Fige le tableau « All commands » de code.claude.com/docs/en/commands.md dans claude_commands.json."""
import json, re, sys
src = open(sys.argv[1]).read()
debut = src.index("## All commands")
fin = src.index("## How the command menu matches")
INTER = re.compile(r"\b(?:opens?|opening|shows?)\b(?: an?| the| your)?(?: \w+){0,3} (picker|dialog|menu|wizard|interface|panel|view)\b|\bpicker\b|\bwizard\b|\binteractive (?:list|lessons|picker|version picker|wizard)\b", re.I)


def interactif(texte_brut):
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", texte_brut)
    t = re.sub(r"`/[\w-]+` picker", "", t)  # mention du sélecteur d'une AUTRE commande
    t = " ".join(ph for ph in re.split(r"(?<=[.!?])\s", t) if not ph.startswith("On v"))  # versions passées
    return bool(INTER.search(t))


out = []
for l in src[debut:fin].splitlines():
    m = re.match(r"^\| `(/[^`]+)`(.*?) \| (.*) \|\s*$", l)
    if not m:
        continue
    cmd, purpose = m.group(1).replace(chr(92) + "|", "|"), m.group(3)
    name, _, args = cmd.partition(" ")
    texte = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", purpose)  # liens -> texte
    texte = texte.replace("`", "").replace("**", "")
    texte = re.sub(r"<[^>]+>", "", texte).strip()
    phrases = re.split(r"(?<=[.!?])\s", texte)
    kind = "builtin"
    if phrases[0] in ("Skill.", "Workflow."):
        kind = phrases.pop(0)[:-1].lower()
    phrase = phrases[0] if phrases else ""
    if phrase.startswith("Removed"):
        continue
    alias = re.findall(r"Aliases?:\s*([^.]+)", texte)
    out.append({"name": name[1:], "args": args.strip(), "description": phrase,
                "kind": kind, "interactive": interactif(purpose),
                "aliases": [a.strip().lstrip("/") for a in re.findall(r"/[\w-]+", alias[0])] if alias else []})
print(len(out), file=sys.stderr)
json.dump({"claude_version": "2.1.325", "measured": "2026-11-14",
           "source": "https://code.claude.com/docs/en/commands.md (tableau « All commands », lu le 2026-11-14)",
           "interactive_rule": "texte de la doc de la commande : « opens/shows … picker|dialog|menu|wizard|interface|panel|view », « picker », « wizard », « interactive list|lessons|picker|wizard » (hors mention du picker d'une autre commande et hors phrases « On v… » sur des versions passées)",
           "model_forms": ["opus", "sonnet", "haiku"],
           "commands": out}, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
