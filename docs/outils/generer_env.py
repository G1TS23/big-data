"""Fabrique un .env à partir de .env.example, secrets tirés au sort.

Les valeurs d'exemple sont des marqueurs : « changeme » est refusé par Metabase,
qui teste la trivialité du mot de passe à l'initialisation. Un secret tiré au
sort passe toujours, et évite au correcteur d'avoir à en inventer un.

    python docs/outils/generer_env.py [destination]
"""

import secrets
import sys
from pathlib import Path

# Les clés dont la valeur d'exemple est un marqueur, à remplacer.
SEL = "EDS_SALT"
MOTS_DE_PASSE = (
    "CLICKHOUSE_ADMIN_PASSWORD",
    "CLICKHOUSE_PILOTAGE_PASSWORD",
    "CLICKHOUSE_EXPLOITATION_PASSWORD",
    "CLICKHOUSE_RECHERCHE_PASSWORD",
    "METABASE_ADMIN_PASSWORD",
    "METABASE_DEMO_PASSWORD",
)


def mot_de_passe() -> str:
    """Suffixe imposé : Metabase exige majuscule, minuscule, chiffre et symbole."""
    return secrets.token_urlsafe(18) + "Aa1!"


def generer(exemple: Path, destination: Path) -> int:
    if destination.exists():
        print(f"{destination} existe déjà : rien n'est écrasé.", file=sys.stderr)
        return 1

    lignes = []
    for ligne in exemple.read_text(encoding="utf-8").splitlines(keepends=True):
        cle = ligne.split("=", 1)[0]
        if cle == SEL:
            ligne = f"{SEL}={secrets.token_hex(32)}\n"
        elif cle in MOTS_DE_PASSE:
            ligne = f"{cle}={mot_de_passe()}\n"
        lignes.append(ligne)

    destination.write_text("".join(lignes), encoding="utf-8")
    destination.chmod(0o600)
    print(f"{destination} écrit ({len(MOTS_DE_PASSE)} mots de passe + 1 sel tirés au sort).")
    return 0


if __name__ == "__main__":
    racine = Path(__file__).resolve().parents[2]
    cible = Path(sys.argv[1]) if len(sys.argv) > 1 else racine / ".env"
    raise SystemExit(generer(racine / ".env.example", cible))
