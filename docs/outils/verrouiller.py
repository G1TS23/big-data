#!/usr/bin/env python3
"""Écrit requirements.lock à partir de roues téléchargées, empreintes comprises.

Les empreintes doivent être calculées sur les roues de la PLATEFORME CIBLE :
une roue Linux et son équivalent macOS n'ont pas le même contenu, et pip
refuserait l'installation. Ce script s'exécute donc dans l'image de destination
— voir la cible `verrou` du Makefile.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ENTETE = """# Dépendances d'exécution, versions RÉSOLUES et empreintes vérifiées.
#
# Les empreintes portent sur les roues Linux de l'image cible : pip refuse
# d'installer un paquet dont le contenu ne correspond pas, ce qui ferme la
# porte à la substitution d'un paquet en amont.
#
# Régénérer après toute modification de requirements.txt :  make verrou
"""


def main() -> None:
    roues, sortie = Path(sys.argv[1]), Path(sys.argv[2])
    lignes = []
    for roue in sorted(roues.glob("*.whl")):
        # Nom de fichier normalisé : paquet-version-python-abi-plateforme.whl
        paquet, version = roue.name.split("-")[:2]
        paquet = re.sub(r"[-_.]+", "-", paquet)
        empreinte = hashlib.sha256(roue.read_bytes()).hexdigest()
        lignes.append(f"{paquet}=={version} \\\n    --hash=sha256:{empreinte}")

    sortie.write_text(ENTETE + "\n".join(lignes) + "\n", encoding="utf-8")
    print(f"{sortie} — {len(lignes)} paquets")


if __name__ == "__main__":
    main()
