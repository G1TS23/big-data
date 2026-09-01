"""Accès à ClickHouse.

ClickHouse n'accepte qu'une instruction par requête HTTP : les fichiers .sql
sont donc découpés avant envoi. Le découpage ignore les points-virgules situés
dans les commentaires et les chaînes, sans quoi une chaîne du genre 'a;b'
casserait le script.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from eds.config import Settings

log = logging.getLogger("eds.db")


def connect(settings: Settings, **overrides) -> Client:
    params = dict(
        host=settings.ch_host,
        port=settings.ch_port,
        username=settings.ch_user,
        password=settings.ch_password,
    )
    params.update(overrides)
    return clickhouse_connect.get_client(**params)


def split_statements(script: str) -> list[str]:
    """Découpe un script SQL en instructions."""
    statements, current = [], []
    in_single = in_double = in_backtick = in_line_comment = in_block_comment = False
    i, n = 0, len(script)

    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                current.append(ch)
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single or in_double or in_backtick:
            current.append(ch)
            if ch == "\\":
                if nxt:
                    current.append(nxt)
                    i += 2
                    continue
            elif (in_single and ch == "'") or (in_double and ch == '"') or (in_backtick and ch == "`"):
                in_single = in_double = in_backtick = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            in_backtick = True
        elif ch == ";":
            statements.append("".join(current).strip())
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def execute_script(client: Client, path: Path) -> int:
    """Exécute un fichier .sql, instruction par instruction."""
    statements = split_statements(path.read_text(encoding="utf-8"))
    for statement in statements:
        client.command(statement)
    log.debug("script exécuté", extra={"fichier": path.name, "instructions": len(statements)})
    return len(statements)


def depots_deja_ingeres(client: Client, dates: Iterable[str]) -> set[tuple[str, str]]:
    """Les couples (source, date de dépôt) déjà ingérés avec succès.

    UNE requête pour toute l'exécution, et non une par dépôt. L'ancienne version
    interrogeait la base à l'intérieur de la boucle : l'index faisait bien son
    travail — un granule lu, quelle que soit la taille de la table — mais chaque
    appel coûtait un aller-retour réseau. Mesuré à 2,5 ms l'unité, soit vingt-cinq
    secondes pour dix mille fichiers, contre trois millisecondes ici.

    La comparaison porte sur la DATE et non sur l'empreinte du fichier : le CHU
    garantit qu'un dossier de dépôt, une fois écrit, ne change plus. Inutile donc
    de relire chaque fichier pour décider de l'ignorer — c'était le poste de coût
    le plus lourd de l'étape, et il ne servait à rien.
    """
    dates = sorted(set(dates))
    if not dates:
        return set()
    resultat = client.query(
        "SELECT DISTINCT source, toString(deposit_date) FROM ops.ingestion_log "
        "WHERE status = 'OK' AND deposit_date IN {d:Array(Date)}",
        parameters={"d": [date.fromisoformat(j) for j in dates]},
    )
    return {(source, jour) for source, jour in resultat.result_rows}
