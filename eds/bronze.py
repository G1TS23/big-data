"""Étape 2 du pipeline : lake → bronze.

Le fichier est envoyé tel quel au moteur, en flux, et c'est ClickHouse qui le
parse et le type via la fonction table `input()`. Python n'ouvre jamais une
ligne de données : il construit une requête et pousse des octets. C'est la
traduction concrète du principe « le moteur transforme, l'orchestrateur pilote ».

L'idempotence tient au partitionnement : chaque table bronze est partitionnée
par date de dépôt, donc recharger une journée revient à supprimer sa partition
puis à la réinsérer. Aucun doublon n'est possible, et les autres jours ne sont
pas touchés.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import urllib3

from eds.config import Settings

log = logging.getLogger("eds.loader")

_http = urllib3.PoolManager()


@dataclass
class LoadResult:
    table: str
    rows_loaded: int
    bytes_read: int
    duration_ms: int


def split_columns(schema: str) -> list[str]:
    """Extrait les noms de colonnes d'un schéma ClickHouse.

    Le découpage tient compte des parenthèses : `Array(Tuple(a String, b String))`
    contient des virgules qui ne séparent pas des colonnes.
    """
    names, depth, current = [], 0, []
    for char in schema:
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth -= 1
        if char == "," and depth == 0:
            names.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        names.append("".join(current).strip())
    return [decl.split()[0] for decl in names]


def build_insert(bronze: dict) -> str:
    """Compose l'INSERT ... SELECT ... FROM input() ... FORMAT."""
    columns = split_columns(bronze["schema"])
    casts = bronze.get("cast") or {}
    projected = [casts.get(name, name) for name in columns]

    target_cols = ", ".join(columns + ["_source_file", "_ingestion_date", "_batch_id"])
    select_cols = ", ".join(projected + ["{f:String}", "{d:Date}", "{b:String}"])

    return (
        f"INSERT INTO {bronze['table']} ({target_cols}) "
        f"SELECT {select_cols} "
        f"FROM input('{bronze['schema']}') "
        f"FORMAT {bronze['format']}"
    )


def drop_partition(client, table: str, deposit_date: str) -> None:
    """Efface le dépôt précédemment chargé pour cette journée."""
    client.command(f"ALTER TABLE {table} DROP PARTITION {{d:String}}",
                   parameters={"d": deposit_date})


def load_file(settings: Settings, bronze: dict, lake_file: Path,
              deposit_date: str, run_id: str) -> LoadResult:
    """Pousse un fichier du lake vers sa table bronze, en flux."""
    url = f"http://{settings.ch_host}:{settings.ch_port}/?" + urlencode({
        "user": settings.ch_user,
        "password": settings.ch_password,
        "query": build_insert(bronze),
        "param_f": str(lake_file),
        "param_d": deposit_date,
        "param_b": run_id,
        # Le résumé revient en en-tête : le nombre de lignes écrites est donné
        # par le moteur, sans requête de comptage supplémentaire.
        "wait_end_of_query": "1",
    })

    size = lake_file.stat().st_size
    started = time.perf_counter()
    with open(lake_file, "rb") as body:
        response = _http.request("POST", url, body=body, preload_content=False)
        payload = response.read()
        response.release_conn()
    elapsed = int((time.perf_counter() - started) * 1000)

    if response.status != 200:
        raise RuntimeError(payload.decode("utf-8", "replace").strip()[:500])

    summary = json.loads(response.headers.get("X-ClickHouse-Summary", "{}"))
    return LoadResult(
        table=bronze["table"],
        rows_loaded=int(summary.get("written_rows", 0)),
        bytes_read=size,
        duration_ms=elapsed,
    )
