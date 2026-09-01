"""Cloisonnement des usages : comptes, rôles, et vérification.

Le pilotage et la recherche ne voient pas les mêmes données. Ce cloisonnement
est appliqué par le moteur : un analyste qui ouvre une console SQL avec son
compte rencontre exactement les mêmes murs que dans son tableau de bord.

Les mots de passe ne transitent jamais en clair dans une requête : on envoie
l'empreinte SHA-256, ce qui les tient hors des journaux de requêtes du serveur.
"""
from __future__ import annotations

import logging
from hashlib import sha256

import clickhouse_connect

from eds import sql
from eds.config import Settings

log = logging.getLogger("eds.access")

# Un compte par usage, un rôle par usage, une base par usage.
COMPTES = {
    "bi_pilotage":    ("role_pilotage",     "gold_pilotage",  "ch_pilotage_password"),
    "bi_recherche":   ("role_recherche",    "gold_recherche", "ch_recherche_password"),
    "bi_exploitation": ("role_exploitation", "ops",           "ch_exploitation_password"),
}

# Ce à quoi aucun compte de restitution ne doit jamais accéder.
INTERDITS = ["silver.fait_sejour", "silver.dim_patient", "bronze.patients", "ops.rejects"]


def ensure_users(client, settings: Settings) -> list[str]:
    """Crée ou met à jour les deux comptes de restitution."""
    crees = []
    for compte, (role, _base, attribut) in COMPTES.items():
        mot_de_passe = getattr(settings, attribut)
        if not mot_de_passe:
            raise RuntimeError(f"mot de passe absent pour {compte} — voir .env")
        empreinte = sha256(mot_de_passe.encode("utf-8")).hexdigest()
        # L'empreinte n'est composée que de caractères hexadécimaux : aucune
        # interpolation dangereuse possible ici.
        client.command(
            f"CREATE USER IF NOT EXISTS {compte} "
            f"IDENTIFIED WITH sha256_hash BY '{empreinte}'")
        client.command(f"ALTER USER {compte} IDENTIFIED WITH sha256_hash BY '{empreinte}'")
        client.command(f"GRANT {role} TO {compte}")
        client.command(f"ALTER USER {compte} DEFAULT ROLE {role}")
        crees.append(compte)
    return crees


def _peut_lire(settings: Settings, compte: str, mot_de_passe: str, objet: str) -> tuple[bool, str]:
    try:
        client = clickhouse_connect.get_client(
            host=settings.ch_host, port=settings.ch_port,
            username=compte, password=mot_de_passe)
        client.command(f"SELECT count() FROM {objet}")
        return True, ""
    except Exception as exc:                      # noqa: BLE001
        message = str(exc)
        return False, "accès refusé" if "ACCESS_DENIED" in message else message[:120]


def verifier(settings: Settings) -> list[dict]:
    """Éprouve le cloisonnement, compte par compte et objet par objet.

    Rend une liste de constats : chacun dit ce qui était attendu et ce qui
    s'est produit. C'est la démonstration que le sujet réclame.
    """
    constats = []
    for compte, (_role, base_autorisee, attribut) in COMPTES.items():
        mot_de_passe = getattr(settings, attribut)
        autres = [b for c, (_r, b, _a) in COMPTES.items() if c != compte]

        cibles = [(f"{base_autorisee}.*", True)]
        cibles += [(f"{b}.*", False) for b in autres]
        cibles += [(objet, False) for objet in INTERDITS
                   if not objet.startswith(base_autorisee + ".")]

        for cible, attendu in cibles:
            objet = cible
            if cible.endswith(".*"):
                base = cible[:-2]
                objet = f"{base}." + _premier_objet(settings, base)
            autorise, motif = _peut_lire(settings, compte, mot_de_passe, objet)
            constats.append({
                "compte": compte, "objet": objet,
                "attendu": "autorisé" if attendu else "refusé",
                "obtenu": "autorisé" if autorise else "refusé",
                "conforme": autorise == attendu,
                "motif": motif,
            })
    return constats


def _premier_objet(settings: Settings, base: str) -> str:
    client = sql.connect(settings)
    rows = client.query(
        "SELECT name FROM system.tables WHERE database = {d:String} ORDER BY name LIMIT 1",
        parameters={"d": base}).result_rows
    return rows[0][0] if rows else "inexistant"
