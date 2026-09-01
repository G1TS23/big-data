#!/usr/bin/env python3
"""Rend la sortie réelle d'une commande en image, pour le dossier.

Ce n'est pas une reconstitution : le texte vient d'une exécution effective,
redirigée dans un fichier. L'outil ne fait que le mettre en page.

    eds acces > /tmp/acces.txt
    python docs/outils/capture_terminal.py /tmp/acces.txt docs/img/cloisonnement.png

Dépendance : Pillow, utile à la documentation seulement — elle ne figure donc
pas dans requirements.txt, que le pipeline seul renseigne.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

POLICE = "/System/Library/Fonts/Menlo.ttc"
TAILLE = 22
INTERLIGNE = 32
MARGE = 34
BANDEAU = 52
GOUTTIERE = 60

C = {"fond": (20, 24, 29), "barre": (28, 34, 42), "regle": (46, 56, 67),
     "ink": (223, 228, 234), "muted": (141, 151, 163), "vert": (95, 179, 122),
     "bleu": (94, 176, 201), "invite": (127, 179, 213), "titre": (200, 135, 60)}


def _polices():
    return (ImageFont.truetype(POLICE, TAILLE, index=0),
            ImageFont.truetype(POLICE, TAILLE, index=1),
            ImageFont.truetype(POLICE, TAILLE - 4, index=1))


def _couleur(ligne: str) -> tuple[int, int, int]:
    if ligne.startswith("INFO"):
        return C["bleu"]
    if ligne.startswith(("WARNING", "ERROR")):
        return C["titre"]
    if ligne.strip().startswith("COMPTE"):
        return C["muted"]
    return C["ink"]


def rendre(sortie: Path, invite: str, titre: str,
           colonnes: list[tuple[str, list[str]]], pied: str) -> None:
    regulier, gras, petit = _polices()

    largeurs = [max(regulier.getlength(l) for l in lignes) for _, lignes in colonnes]
    hauteur_max = max(len(lignes) for _, lignes in colonnes)
    largeur = int(MARGE * 2 + sum(largeurs) + GOUTTIERE * (len(colonnes) - 1))
    hauteur = int(BANDEAU + INTERLIGNE * 3.1 + (hauteur_max + 1) * INTERLIGNE + 90)

    image = Image.new("RGB", (largeur, hauteur), C["fond"])
    d = ImageDraw.Draw(image)

    # Bandeau de fenêtre : trois pastilles et un titre centré.
    d.rectangle([(0, 0), (largeur, BANDEAU)], fill=C["barre"])
    for i, teinte in enumerate(((224, 87, 76), (217, 164, 65), (95, 179, 122))):
        cx = 30 + i * 28
        d.ellipse([(cx - 8, BANDEAU // 2 - 8), (cx + 8, BANDEAU // 2 + 8)], fill=teinte)
    d.text((largeur / 2, BANDEAU / 2), titre, font=petit, fill=C["muted"], anchor="mm")

    y = BANDEAU + 30
    d.text((MARGE, y), invite, font=regulier, fill=C["invite"])
    y_colonnes = y + int(INTERLIGNE * 2.6)

    x = MARGE
    for (entete, lignes), large in zip(colonnes, largeurs):
        d.text((x, y_colonnes - INTERLIGNE), entete, font=petit, fill=C["titre"])
        yy = y_colonnes + 8
        for ligne in lignes:
            texte = ligne.rstrip()
            if texte.endswith("ok"):
                corps = texte[:-2]
                d.text((x, yy), corps, font=regulier, fill=_couleur(texte))
                d.text((x + regulier.getlength(corps), yy), "ok", font=gras, fill=C["vert"])
            else:
                d.text((x, yy), texte, font=regulier, fill=_couleur(texte))
            yy += INTERLIGNE
        x += large + GOUTTIERE

    bas = y_colonnes + 8 + hauteur_max * INTERLIGNE + 18
    d.line([(MARGE, bas), (largeur - MARGE, bas)], fill=C["regle"], width=2)
    d.text((MARGE, bas + 18), pied, font=regulier, fill=C["bleu"])

    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    print(f"{sortie}  {largeur}×{hauteur} px")


PROJET = Path(__file__).resolve().parent.parent.parent
EXTENSIONS = {".png", ".jpg", ".webp"}


def _chemin_sous_le_projet(brut: str, doit_exister: bool) -> Path:
    """Résout un chemin et refuse tout ce qui sort du projet.

    L'outil reçoit ses chemins en ligne de commande et ÉCRIT à la destination
    demandée. Sans borne, « ../../.ssh/config » comme destination écraserait un
    fichier hors du dépôt. On résout donc les liens et les « .. » avant de
    vérifier que le résultat reste sous la racine du projet.
    """
    chemin = Path(brut).resolve()
    try:
        chemin.relative_to(PROJET)
    except ValueError:
        raise SystemExit(f"chemin hors du projet, refusé : {chemin}") from None
    if doit_exister and not chemin.is_file():
        raise SystemExit(f"fichier introuvable : {chemin}")
    if not doit_exister and chemin.suffix.lower() not in EXTENSIONS:
        raise SystemExit(f"extension inattendue pour une image : {chemin.suffix or 'aucune'}")
    return chemin


def main() -> None:
    analyseur = argparse.ArgumentParser(
        description="Met en page la sortie d'une commande eds pour le dossier.")
    analyseur.add_argument("source", help="fichier contenant la sortie capturée")
    analyseur.add_argument("sortie", help="image à produire, sous docs/img/")
    arguments = analyseur.parse_args()

    source = _chemin_sous_le_projet(arguments.source, doit_exister=True)
    sortie = _chemin_sous_le_projet(arguments.sortie, doit_exister=False)
    brut = source.read_text(encoding="utf-8").rstrip("\n").split("\n")

    # La sortie de `eds acces` comporte deux tableaux : moteur, puis restitution.
    debuts = [i for i, l in enumerate(brut) if l.strip().startswith("COMPTE")]
    garder = lambda bloc: [l for l in bloc if l.strip() and not l.startswith("INFO")]
    colonnes = [("1 · AU NIVEAU DU MOTEUR — comptes ClickHouse",
                 garder(brut[debuts[0]:debuts[1]])),
                ("2 · AU NIVEAU DE LA RESTITUTION — comptes Metabase",
                 garder(brut[debuts[1]:]))]
    pied = next(l for l in brut if "cloisonnement vérifié" in l).rstrip()

    rendre(sortie,
           invite="olivier@chu ~/bigdata/projet (.venv) $ eds acces",
           titre="eds — démonstration du cloisonnement des droits",
           colonnes=colonnes, pied=pied)


if __name__ == "__main__":
    main()
