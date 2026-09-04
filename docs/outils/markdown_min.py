"""Conversion Markdown → HTML, limitée au sous-ensemble employé par le dossier.

Écrite à la main, et sans dépendance : le projet n'a pas à gagner une
bibliothèque pour fabriquer un PDF une fois. Le sous-ensemble couvert est celui
que les documents utilisent réellement — titres, paragraphes, listes, tableaux,
blocs de code, citations, images, liens, gras, italique, code en ligne.
"""
from __future__ import annotations

import html
import re

_LIEN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_GRAS = re.compile(r"\*\*([^*]+)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")


def _en_ligne(texte: str) -> str:
    """Le code en ligne est protégé AVANT l'échappement, sinon ses chevrons
    seraient échappés deux fois."""
    jetons: list[str] = []

    def garder(m: re.Match) -> str:
        jetons.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(jetons) - 1}\x00"

    texte = _CODE.sub(garder, texte)
    texte = html.escape(texte)
    texte = _IMAGE.sub(r'<img src="\2" alt="\1">', texte)
    texte = _LIEN.sub(r'<a href="\2">\1</a>', texte)
    texte = _GRAS.sub(r"<strong>\1</strong>", texte)
    texte = _ITAL.sub(r"<em>\1</em>", texte)
    return re.sub(r"\x00(\d+)\x00", lambda m: jetons[int(m.group(1))], texte)


def _tableau(lignes: list[str]) -> str:
    def cellules(ligne: str) -> list[str]:
        return [c.strip() for c in ligne.strip().strip("|").split("|")]

    entete = cellules(lignes[0])
    # La ligne d'alignement porte le fer : ---: à droite, :---: au centre.
    alignements = []
    for spec in cellules(lignes[1]):
        droite, gauche = spec.endswith(":"), spec.startswith(":")
        alignements.append("center" if droite and gauche else "right" if droite else "left")

    out = ["<table>", "<thead><tr>"]
    for cellule, al in zip(entete, alignements):
        out.append(f'<th style="text-align:{al}">{_en_ligne(cellule)}</th>')
    out.append("</tr></thead><tbody>")
    for ligne in lignes[2:]:
        out.append("<tr>")
        for cellule, al in zip(cellules(ligne), alignements):
            out.append(f'<td style="text-align:{al}">{_en_ligne(cellule)}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)

# Chaque règle reçoit les lignes et la position courante, et répond :
#   None                 — « ce n'est pas mon bloc », on essaie la suivante
#   (html, position)     — le bloc rendu, et où reprendre
#   (None, position)     — le bloc consommé sans rien produire
# L'ordre du tuple _REGLES est significatif : la première qui reconnaît gagne.

_SEPARATEUR = re.compile(r"^(-{3,}|\*{3,})\s*$")
# Quantificateurs possessifs : « \s++ » ne rend jamais ce qu'il a pris, ce qui
# supprime le retour arrière entre l'espace et le reste de la ligne — les deux
# peuvent matcher un espace, et l'analyseur y voit un coût super-linéaire.
# Exige Python 3.11, que le projet impose déjà.
_TITRE = re.compile(r"^(#{1,6})\s++(.*)$")
_ALIGNEMENT = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_PUCE = re.compile(r"^(\s*+)([-*]|\d+\.)\s++(.*)$")

# Un paragraphe s'arrête devant ce qui ouvre un autre bloc.
_ARRETS = ("#", "|", ">", "```", "- ", "* ")


def _bloc_code(lignes: list[str], i: int, _ancre) -> tuple[str, int] | None:
    if not lignes[i].startswith("```"):
        return None
    langage = lignes[i][3:].strip()
    i += 1
    bloc = []
    while i < len(lignes) and not lignes[i].startswith("```"):
        bloc.append(lignes[i])
        i += 1
    contenu = html.escape("\n".join(bloc))
    if langage == "mermaid":
        return f'<pre class="mermaid">{contenu}</pre>', i + 1
    return f"<pre><code>{contenu}</code></pre>", i + 1


def _separateur(lignes: list[str], i: int, _ancre) -> tuple[str, int] | None:
    if not _SEPARATEUR.match(lignes[i]):
        return None
    return '<hr class="separateur">', i + 1


def _titre(lignes: list[str], i: int, ancre) -> tuple[str, int] | None:
    m = _TITRE.match(lignes[i])
    if not m:
        return None
    n, texte = len(m.group(1)), m.group(2)
    return f'<h{n} id="{ancre(texte)}">{_en_ligne(texte)}</h{n}>', i + 1


def _bloc_tableau(lignes: list[str], i: int, _ancre) -> tuple[str, int] | None:
    """Un tableau se reconnaît à sa SECONDE ligne, celle des alignements."""
    if not (lignes[i].lstrip().startswith("|") and i + 1 < len(lignes)
            and _ALIGNEMENT.match(lignes[i + 1])):
        return None
    bloc = []
    while i < len(lignes) and lignes[i].lstrip().startswith("|"):
        bloc.append(lignes[i])
        i += 1
    return _tableau(bloc), i


def _citation(lignes: list[str], i: int, _ancre) -> tuple[str, int] | None:
    if not lignes[i].lstrip().startswith("> "):
        return None
    bloc = []
    while i < len(lignes) and lignes[i].lstrip().startswith(">"):
        bloc.append(lignes[i].lstrip()[1:].lstrip())
        i += 1
    return f"<blockquote>{_en_ligne(' '.join(bloc))}</blockquote>", i


def _liste(lignes: list[str], i: int, _ancre) -> tuple[str, int] | None:
    premiere = _PUCE.match(lignes[i])
    if not premiere:
        return None
    balise = "ol" if premiere.group(2).endswith(".") else "ul"
    out = [f"<{balise}>"]
    while i < len(lignes):
        m = _PUCE.match(lignes[i])
        if not m:
            # Une ligne indentée prolonge la puce précédente.
            if lignes[i].startswith("  ") and lignes[i].strip():
                out[-1] = out[-1][:-5] + " " + _en_ligne(lignes[i].strip()) + "</li>"
                i += 1
                continue
            break
        out.append(f"<li>{_en_ligne(m.group(3))}</li>")
        i += 1
    out.append(f"</{balise}>")
    return "\n".join(out), i


def _paragraphe(lignes: list[str], i: int, _ancre) -> tuple[str | None, int]:
    """Le cas par défaut, qui ne refuse jamais un bloc."""
    bloc = []
    while i < len(lignes) and lignes[i].strip() and not lignes[i].startswith(_ARRETS):
        bloc.append(lignes[i].strip())
        i += 1
    if not bloc:
        # La ligne ouvre un bloc qu'aucune règle n'a su lire — un « | » sans
        # ligne d'alignement, par exemple. Elle est consommée sans rien produire,
        # donc elle DISPARAÎT du rendu sans le moindre avertissement.
        return None, i + 1
    return f"<p>{_en_ligne(' '.join(bloc))}</p>", i


_REGLES = (_bloc_code, _separateur, _titre, _bloc_tableau, _citation, _liste, _paragraphe)


def convertir(source: str, prefixe_ancre: str = "") -> str:
    """Rend le HTML. `prefixe_ancre` évite les collisions entre documents."""
    lignes = source.splitlines()
    out: list[str] = []
    i = 0

    def ancre(titre: str) -> str:
        brut = re.sub(r"[^\w\s-]", "", titre.lower()).strip().replace(" ", "-")
        return f"{prefixe_ancre}{brut}"

    while i < len(lignes):
        if not lignes[i].strip():
            i += 1
            continue
        for regle in _REGLES:
            resultat = regle(lignes, i, ancre)
            if resultat is None:
                continue
            texte, i = resultat
            if texte is not None:
                out.append(texte)
            break

    return "\n".join(out)
