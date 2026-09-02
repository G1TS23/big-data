#!/usr/bin/env python3
"""Écrit requirements.lock à partir de roues résolues, empreintes comprises.

Le piège que ce script existe pour éviter : une empreinte porte sur UN fichier,
et un paquet compilé publie une roue par architecture. Verrouiller depuis un
Mac Apple Silicon enregistrait les roues aarch64 ; la même image construite sur
une machine x86_64 téléchargeait d'autres fichiers, et pip refusait l'install
avec « THESE PACKAGES DO NOT MATCH THE HASHES ».

Le verrou liste donc, pour chaque version résolue, les empreintes de TOUTES les
roues que l'image cible pourrait légitimement choisir — x86_64 et aarch64. pip
accepte un paquet dès qu'une empreinte de sa liste correspond, si bien que le
verrou devient indépendant de la machine qui construit, sans rien relâcher :
seules les roues publiées pour cette version exacte sont acceptées.

    python docs/outils/verrouiller.py <dossier_de_roues> <requirements.lock>
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

# Architectures sur lesquelles l'image doit pouvoir se construire.
ARCHITECTURES = ("x86_64", "aarch64")

ENTETE = """# Dépendances d'exécution, versions RÉSOLUES et empreintes vérifiées.
#
# pip refuse d'installer un paquet dont le contenu ne correspond à aucune
# empreinte listée : la substitution d'un paquet en amont est fermée.
#
# Plusieurs empreintes par paquet, c'est normal : un paquet compilé publie une
# roue par architecture, et le verrou couvre x86_64 comme aarch64 pour qu'une
# même image se construise sur un PC comme sur un Mac Apple Silicon.
#
# Régénérer après toute modification de requirements.txt :  make verrou
"""


def normaliser(nom: str) -> str:
    return re.sub(r"[-_.]+", "-", nom).lower()


def empreintes(paquet: str, version: str, tag_python: str) -> list[str]:
    """Les roues de PyPI que l'image cible pourrait choisir, pour cette version."""
    url = f"https://pypi.org/pypi/{paquet}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as reponse:  # noqa: S310 (PyPI, en https)
        fichiers = json.load(reponse)["urls"]

    retenues = []
    for fichier in fichiers:
        if fichier["packagetype"] != "bdist_wheel":
            continue
        nom = fichier["filename"]
        pure = "-py3-none-any.whl" in nom or "-py2.py3-none-any.whl" in nom
        # manylinux seulement : l'image est une Debian, donc glibc, pas musl.
        compilee = (tag_python in nom and "manylinux" in nom
                    and any(a in nom for a in ARCHITECTURES))
        if pure or compilee:
            retenues.append(fichier["digests"]["sha256"])

    if not retenues:
        raise SystemExit(f"aucune roue utilisable trouvée pour {paquet}=={version}")
    return sorted(retenues)


def main() -> None:
    roues, sortie = Path(sys.argv[1]), Path(sys.argv[2])
    tag_python = f"cp{sys.version_info.major}{sys.version_info.minor}"

    blocs, total = [], 0
    for roue in sorted(roues.glob("*.whl"), key=lambda p: normaliser(p.name)):
        # Nom de fichier normalisé : paquet-version-python-abi-plateforme.whl
        paquet, version = roue.name.split("-")[:2]
        sha = empreintes(normaliser(paquet), version, tag_python)
        total += len(sha)
        suite = " \\\n".join(f"    --hash=sha256:{s}" for s in sha)
        blocs.append(f"{normaliser(paquet)}=={version} \\\n{suite}")

    sortie.write_text(ENTETE + "\n".join(blocs) + "\n", encoding="utf-8", newline="")
    print(f"{sortie} — {len(blocs)} paquets, {total} empreintes")


if __name__ == "__main__":
    main()
