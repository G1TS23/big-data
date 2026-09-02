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


def convertir(source: str, prefixe_ancre: str = "") -> str:
    """Rend le HTML. `prefixe_ancre` évite les collisions entre documents."""
    lignes = source.splitlines()
    out: list[str] = []
    i = 0

    def ancre(titre: str) -> str:
        brut = re.sub(r"[^\w\s-]", "", titre.lower()).strip().replace(" ", "-")
        return f"{prefixe_ancre}{brut}"

    while i < len(lignes):
        ligne = lignes[i]

        if ligne.startswith("```"):
            langage = ligne[3:].strip()
            i += 1
            bloc = []
            while i < len(lignes) and not lignes[i].startswith("```"):
                bloc.append(lignes[i])
                i += 1
            i += 1
            contenu = "\n".join(bloc)
            if langage == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(contenu)}</pre>')
            else:
                out.append(f"<pre><code>{html.escape(contenu)}</code></pre>")
            continue

        if re.match(r"^\s*$", ligne):
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,})\s*$", ligne):
            out.append('<hr class="separateur">')
            i += 1
            continue

        titre = re.match(r"^(#{1,6})\s+(.*)$", ligne)
        if titre:
            n = len(titre.group(1))
            texte = titre.group(2)
            out.append(f'<h{n} id="{ancre(texte)}">{_en_ligne(texte)}</h{n}>')
            i += 1
            continue

        # Tableau : une ligne de cellules suivie d'une ligne d'alignement.
        if ligne.lstrip().startswith("|") and i + 1 < len(lignes) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lignes[i + 1]):
            bloc = []
            while i < len(lignes) and lignes[i].lstrip().startswith("|"):
                bloc.append(lignes[i])
                i += 1
            out.append(_tableau(bloc))
            continue

        if ligne.lstrip().startswith("> "):
            bloc = []
            while i < len(lignes) and lignes[i].lstrip().startswith(">"):
                bloc.append(lignes[i].lstrip()[1:].lstrip())
                i += 1
            out.append(f"<blockquote>{_en_ligne(' '.join(bloc))}</blockquote>")
            continue

        puce = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ligne)
        if puce:
            ordonnee = puce.group(2).endswith(".")
            balise = "ol" if ordonnee else "ul"
            out.append(f"<{balise}>")
            while i < len(lignes):
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lignes[i])
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
            continue

        # Paragraphe : jusqu'à la ligne vide.
        bloc = []
        while i < len(lignes) and lignes[i].strip() and not lignes[i].startswith(("#", "|", ">", "```", "- ", "* ")):
            bloc.append(lignes[i].strip())
            i += 1
        if bloc:
            out.append(f"<p>{_en_ligne(' '.join(bloc))}</p>")
        else:
            i += 1

    return "\n".join(out)
