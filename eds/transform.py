"""Étape 3 du pipeline : bronze → silver → gold, en SQL.

Python ne transforme rien. Il lit les règles métier, les passe en paramètres
aux requêtes, et enregistre quelles valeurs ont été appliquées — un indicateur
n'est reproductible que si l'on sait avec quels seuils il a été calculé.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

from eds import db
from eds.config import ROOT

log = logging.getLogger("eds.transform")

REGLES = ROOT / "config" / "regles.yml"


def load_regles(path: Path | None = None) -> dict:
    with open(path or REGLES, encoding="utf-8") as fh:
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


def snapshot_parametres(client, run_id: str, parameters: dict) -> None:
    """Consigne les règles appliquées par cette exécution."""
    now = datetime.now()
    client.insert(
        "ops.parametres",
        [[run_id, nom, str(valeur), now] for nom, valeur in sorted(parameters.items())],
        column_names=["run_id", "nom", "valeur", "applique_at"],
    )


def run_script(client, path: Path, run_id: str, parameters: dict,
               substitutions: dict | None = None) -> int:
    """Exécute un script SQL, instruction par instruction, avec les règles.

    `parameters` alimente les marqueurs {nom:Type}, résolus par le moteur à
    chaque exécution. `substitutions` remplace des jetons $$NOM$$ dans le texte
    AVANT envoi : c'est réservé à ce qui doit être scellé dans un objet
    persistant — le seuil d'anonymat d'une vue, par exemple, qui ne doit
    surtout pas devenir un paramètre que l'appelant pourrait régler.
    """
    params = {**parameters, "b": run_id}
    sql = path.read_text(encoding="utf-8")
    for nom, valeur in (substitutions or {}).items():
        sql = sql.replace(f"$${nom}$$", str(valeur))
    statements = db.split_statements(sql)
    for index, statement in enumerate(statements, 1):
        try:
            client.command(statement, parameters=params)
        except Exception as exc:
            raise RuntimeError(
                f"{path.name}, instruction {index}/{len(statements)} : {exc}\n"
                f"--- SQL ---\n{statement[:600]}"
            ) from exc
    return len(statements)
