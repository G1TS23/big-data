"""Couche silver : bronze devient un modèle métier fiable.

Ce module est court, et c'est le sujet même du projet. Contrairement à lake et
bronze, qui déplacent des octets et ont donc besoin de Python, silver ne fait
que du calcul sur des données déjà dans l'entrepôt : sa transformation est
écrite en SQL, dans sql/20_silver.sql. Il ne reste ici qu'à désigner ce script,
l'exécuter avec les règles métier, et rendre compte de ce qu'il a produit.

Un module de trente lignes en face de trois cents lignes de SQL est la preuve
que le calcul est resté dans le moteur.
"""
from __future__ import annotations

import logging

from eds import sql
from eds.config import Settings

SCRIPTS = ["20_silver.sql"]


def construire(run, settings: Settings, log: logging.Logger) -> None:
    """Reconstruit intégralement la couche, puis rend compte."""
    parametres = sql.executer_avec_regles(run, SCRIPTS, log)
    log.info("règles appliquées", extra={"run_id": run.run_id, **parametres})

    for nom, lignes in run.client.query(
            "SELECT name, total_rows FROM system.tables "
            "WHERE database = 'silver' ORDER BY name").result_rows:
        log.info("table construite", extra={"table": f"silver.{nom}", "lignes": lignes})

    # Ce qui a été écarté doit se voir dans la console, pas seulement en base.
    for table, regle, nb in run.client.query(
            "SELECT table_source, regle, count() FROM ops.rejects "
            "WHERE run_id = {r:String} GROUP BY table_source, regle "
            "ORDER BY table_source, regle", parameters={"r": run.run_id}).result_rows:
        log.warning("lignes écartées", extra={"table": table, "regle": regle, "lignes": nb})
