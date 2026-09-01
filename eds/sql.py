"""Tout ce qui parle SQL à ClickHouse : la connexion, les scripts, les règles.

Python ne transforme rien. Il ouvre une connexion, lit des fichiers .sql, y
injecte les règles métier et transmet les instructions. Le calcul appartient au
moteur.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import clickhouse_connect
import yaml
from clickhouse_connect.driver.client import Client

from eds.config import ROOT, Settings

log = logging.getLogger("eds.sql")

REGLES = ROOT / "config" / "regles.yml"


# ─── Connexion ──────────────────────────────────────────────────────────────

def connect(settings: Settings, **options) -> Client:
    return clickhouse_connect.get_client(
        host=settings.ch_host, port=settings.ch_port,
        username=settings.ch_user, password=settings.ch_password, **options)


# ─── Découpage des scripts ──────────────────────────────────────────────────

# ClickHouse n'accepte qu'une instruction par requête : il faut donc découper
# les fichiers .sql. Deux contextes seulement protègent un point-virgule d'être
# pris pour un séparateur — un commentaire de ligne et une chaîne. Nos scripts
# n'utilisent rien d'autre, et un test le vérifie, ce qui autorise ce découpeur
# à rester aussi simple que son usage.
_JETONS = re.compile(r"--[^\n]*|'(?:[^']|'')*'|;")


def split_statements(script: str) -> list[str]:
    """Découpe un script en instructions, commentaires retirés."""
    instructions: list[str] = []
    courante: list[str] = []
    fin = 0

    for jeton in _JETONS.finditer(script):
        courante.append(script[fin:jeton.start()])
        fin = jeton.end()
        texte = jeton.group()
        if texte == ";":
            instructions.append("".join(courante).strip())
            courante = []
        elif not texte.startswith("--"):
            courante.append(texte)      # une chaîne : on la garde telle quelle
        # un commentaire : on le laisse tomber

    courante.append(script[fin:])
    instructions.append("".join(courante).strip())
    return [i for i in instructions if i]


def execute_script(client: Client, chemin: Path) -> int:
    """Exécute un fichier .sql, instruction par instruction."""
    instructions = split_statements(chemin.read_text(encoding="utf-8"))
    for instruction in instructions:
        client.command(instruction)
    return len(instructions)


# ─── Règles métier ──────────────────────────────────────────────────────────

def load_regles(chemin: Path | None = None) -> dict:
    with open(chemin or REGLES, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def to_parameters(regles: dict) -> dict:
    """Aplatit les règles en paramètres de requête.

    Les noms correspondent aux marqueurs {nom:Type} des fichiers .sql.
    """
    bornes, alertes = regles["bornes"], regles["alertes"]
    return {
        "fc_min":   bornes["heart_rate"]["min"],
        "fc_max":   bornes["heart_rate"]["max"],
        "spo2_min": bornes["spo2"]["min"],
        "spo2_max": bornes["spo2"]["max"],
        "temp_min": bornes["temp_c"]["min"],
        "temp_max": bornes["temp_c"]["max"],

        "a_fc_bas":    alertes["heart_rate"]["bas"],
        "a_fc_haut":   alertes["heart_rate"]["haut"],
        "a_spo2_bas":  alertes["spo2"]["bas"],
        "a_temp_haut": alertes["temp_c"]["haut"],

        "annee_min": regles["annee_naissance_min"],
        "fenetre":   regles["readmission_fenetre_jours"],
        "k":         regles["k_anonymite"],
    }


def snapshot_parametres(client: Client, run_id: str, parametres: dict) -> None:
    """Consigne les règles appliquées : un indicateur n'est reproductible que
    si l'on sait avec quels seuils il a été calculé."""
    maintenant = datetime.now()
    client.insert("ops.parametres",
                  [[run_id, nom, str(valeur), maintenant]
                   for nom, valeur in sorted(parametres.items())],
                  column_names=["run_id", "nom", "valeur", "applique_at"])


def run_script(client: Client, chemin: Path, run_id: str, parametres: dict,
               substitutions: dict | None = None) -> int:
    """Exécute un script avec les règles métier.

    `parametres` alimente les marqueurs {nom:Type}, résolus par le moteur à
    chaque exécution. `substitutions` remplace des jetons $$NOM$$ dans le texte
    AVANT envoi : c'est réservé à ce qui doit être scellé dans un objet
    persistant — le seuil d'anonymat d'une vue, par exemple, qui ne doit surtout
    pas devenir un paramètre que l'appelant pourrait régler.
    """
    valeurs = {**parametres, "b": run_id}
    texte = chemin.read_text(encoding="utf-8")
    for nom, valeur in (substitutions or {}).items():
        texte = texte.replace(f"$${nom}$$", str(valeur))

    instructions = split_statements(texte)
    for rang, instruction in enumerate(instructions, 1):
        try:
            client.command(instruction, parameters=valeurs)
        except Exception as exc:
            raise RuntimeError(
                f"{chemin.name}, instruction {rang}/{len(instructions)} : {exc}\n"
                f"--- SQL ---\n{instruction[:600]}") from exc
    return len(instructions)


# ─── Interrogations du journal ──────────────────────────────────────────────

def depots_deja_ingeres(client: Client, dates: Iterable[str]) -> set[tuple[str, str]]:
    """Les couples (source, date de dépôt) déjà ingérés avec succès.

    UNE requête pour toute l'exécution, et non une par dépôt : l'index faisait
    bien son travail — un granule lu quelle que soit la taille de la table —
    mais chaque appel coûtait un aller-retour réseau.

    La comparaison porte sur la DATE et non sur une empreinte : le CHU garantit
    qu'un dossier de dépôt, une fois écrit, ne change plus. Inutile donc de
    relire chaque fichier pour décider de l'ignorer.
    """
    jours = sorted(set(dates))
    if not jours:
        return set()
    resultat = client.query(
        "SELECT DISTINCT source, toString(deposit_date) FROM ops.ingestion_log "
        "WHERE status = 'OK' AND deposit_date IN {d:Array(Date)}",
        parameters={"d": [date.fromisoformat(j) for j in jours]})
    return {(source, jour) for source, jour in resultat.result_rows}
