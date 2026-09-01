"""Couche gold : les indicateurs, cloisonnés par usage.

Comme silver, ce module est mince parce que le travail est en SQL —
sql/30_gold_pilotage.sql pour les tables pré-agrégées du pilotage,
sql/31_gold_recherche.sql pour les vues de la recherche.

Il porte en revanche une responsabilité qu'on ne peut pas laisser au SQL : le
SCELLEMENT du seuil de diffusion et du définisseur dans les vues de recherche.
Écrits en dur au moment de la création, ils ne peuvent pas être fournis par
l'appelant — un garde-fou que celui qu'il protège peut régler n'en est pas un.
"""
from __future__ import annotations

import logging

from eds import sql

SCRIPTS = ["30_gold_pilotage.sql", "31_gold_recherche.sql"]

BASES = ("gold_pilotage", "gold_recherche")


def construire(run, log: logging.Logger) -> None:
    # L'exécution porte déjà la configuration : inutile de la repasser.
    substitutions = {"K_ANONYMITE": sql.to_parameters(sql.load_regles())["k"],
                     "DEFINER": run.settings.ch_user}
    sql.executer_avec_regles(run, SCRIPTS, log, substitutions)

    for base in BASES:
        for nom, moteur in run.client.query(
                "SELECT name, engine FROM system.tables WHERE database = {d:String} "
                "ORDER BY name", parameters={"d": base}).result_rows:
            log.info("objet gold", extra={"objet": f"{base}.{nom}", "type": moteur})
