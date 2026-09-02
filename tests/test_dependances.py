"""Contrôles de requirements.lock.

Deux régressions vécues, qu'aucun test ne voyait :

1. Le verrou n'a été généré que pour l'architecture de la machine qui l'a
   produit. L'image se construisait sur un Mac Apple Silicon et échouait sur un
   PC, avec « THESE PACKAGES DO NOT MATCH THE HASHES ».
2. La cible make appelait pip download avec --no-deps : seules les six
   dépendances directes auraient été figées, pas leurs transitives.

Ces contrôles sont hors ligne — ils lisent le fichier, ils n'interrogent pas
PyPI. Ils ne remplacent pas une construction réelle, ils rendent l'erreur
visible sans attendre qu'un binôme la rencontre.
"""
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
LOCK = RACINE / "requirements.lock"

# Paquets contenant du code compilé : ils publient une roue par architecture,
# donc leur verrou DOIT porter plusieurs empreintes.
COMPILES = ("pyarrow", "lz4", "backports-zstd", "clickhouse-connect", "pyyaml")


def entrees() -> dict[str, list[str]]:
    """{ 'paquet==version': [empreintes] }, lu depuis le verrou."""
    trouvees: dict[str, list[str]] = {}
    courant = None
    for ligne in LOCK.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if empreinte := re.fullmatch(r"--hash=sha256:([0-9a-f]{64})\s*\\?", ligne):
            assert courant, f"empreinte orpheline : {ligne}"
            trouvees[courant].append(empreinte.group(1))
        elif nom := re.match(r"([A-Za-z0-9._-]+)==([^\s\\]+)", ligne):
            courant = nom.group(1).lower()
            trouvees[courant] = []
    return trouvees


@pytest.fixture(scope="module")
def verrou():
    return entrees()


def test_chaque_paquet_porte_au_moins_une_empreinte(verrou):
    sans = [p for p, h in verrou.items() if not h]
    assert not sans, f"paquets sans empreinte : {sans}"


def test_les_dependances_directes_sont_toutes_figees(verrou):
    """Garde contre un --no-deps qui ne figerait que requirements.txt."""
    directes = {
        re.split(r"[<>=!~]", ligne)[0].strip().lower()
        for ligne in (RACINE / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.startswith("#")
    }
    assert directes <= set(verrou), f"absentes du verrou : {directes - set(verrou)}"


def test_les_transitives_sont_figees_aussi(verrou):
    """Six dépendances directes : un verrou qui n'en compte que six a été
    produit avec --no-deps."""
    assert len(verrou) > 10, f"{len(verrou)} paquets seulement — --no-deps ?"


@pytest.mark.parametrize("paquet", COMPILES)
def test_les_paquets_compiles_couvrent_les_deux_architectures(paquet, verrou):
    """Une empreinte seule signifie une seule roue, donc une seule architecture :
    l'image ne se construirait que sur la machine qui a généré le verrou."""
    assert paquet in verrou, f"{paquet} absent du verrou"
    assert len(verrou[paquet]) >= 2, (
        f"{paquet} n'a qu'une empreinte : le verrou est lié à une architecture. "
        "Régénérer avec `make verrou`.")


# ── Portabilité des modules ──────────────────────────────────────────────────
# Le pipeline se lance depuis un poste Windows comme depuis un Mac. Un import
# de module POSIX au premier niveau casse TOUTES les commandes, y compris celles
# qui n'ont rien à voir : « eds init » est mort sur un import fcntl fait au
# chargement de eds.verrou, lui-même importé par eds.cli.
POSIX_SEULEMENT = {"fcntl", "pwd", "grp", "termios", "resource", "posix", "syslog"}

EDS = RACINE / "eds"


def imports_au_premier_niveau(source: str) -> set[str]:
    """Les modules importés hors de toute condition — donc sur tout système."""
    import ast

    trouves: set[str] = set()
    for noeud in ast.parse(source).body:          # body seul : pas les if/try
        if isinstance(noeud, ast.Import):
            trouves.update(alias.name.split(".")[0] for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            trouves.add(noeud.module.split(".")[0])
    return trouves


@pytest.mark.parametrize("module", sorted(p.name for p in EDS.glob("*.py")))
def test_aucun_module_posix_importe_sans_condition(module):
    source = (EDS / module).read_text(encoding="utf-8")
    fautifs = imports_au_premier_niveau(source) & POSIX_SEULEMENT
    assert not fautifs, (
        f"eds/{module} importe {sorted(fautifs)} au premier niveau : "
        "le module doit être importable sous Windows. Placer l'import dans une "
        "branche conditionnelle, comme le fait eds/verrou.py.")
