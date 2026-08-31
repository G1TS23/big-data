"""Point d'entrée en ligne de commande de l'orchestrateur.

    eds init                    crée la couche d'exploitation (ops.*)
    eds lake                    recopie dans le lake tous les dépôts non traités
    eds lake --date 2026-08-27  rejoue une journée précise (idempotent)
    eds status                  état des ingestions et des derniers runs

Chaque exécution porte un identifiant (run_id) inscrit sur toutes les lignes
qu'elle produit : c'est le fil qui relie un chiffre de tableau de bord au
fichier source dont il provient.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

from eds import config, db, lake, logging_setup

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


def _open_run(client, run_id: str, command: str) -> datetime:
    started = datetime.now()
    client.insert(
        "ops.run_log",
        [[run_id, command, started, None, "RUNNING", 0, 0, 0, 0, "", datetime.now()]],
        column_names=["run_id", "command", "started_at", "finished_at", "status",
                      "deposits_seen", "deposits_ingested", "deposits_skipped",
                      "deposits_quarantined", "message", "updated_at"],
    )
    return started


def _close_run(client, run_id, command, started, status, counts, message=""):
    client.insert(
        "ops.run_log",
        [[run_id, command, started, datetime.now(), status,
          counts.get("seen", 0), counts.get("ingested", 0),
          counts.get("skipped", 0), counts.get("quarantined", 0),
          message[:2000], datetime.now()]],
        column_names=["run_id", "command", "started_at", "finished_at", "status",
                      "deposits_seen", "deposits_ingested", "deposits_skipped",
                      "deposits_quarantined", "message", "updated_at"],
    )


def cmd_init(settings, args, log) -> int:
    client = db.connect(settings)
    count = db.execute_script(client, SQL_DIR / "40_ops.sql")
    log.info("couche d'exploitation créée", extra={"instructions": count})
    tables = client.query("SELECT name FROM system.tables WHERE database = 'ops' ORDER BY name")
    log.info("tables ops", extra={"tables": ", ".join(r[0] for r in tables.result_rows)})
    return 0


def cmd_lake(settings, args, log) -> int:
    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "lake")

    counts = {"seen": 0, "ingested": 0, "skipped": 0, "quarantined": 0}
    rows: list[list] = []
    failures: list[str] = []
    status = "FAILED"

    try:
        sources = config.load_sources()
        deposits = lake.discover(settings.source_path, sources)
        if args.date:
            deposits = [d for d in deposits if d.deposit_date == args.date]
        if args.source:
            deposits = [d for d in deposits if d.source.split("/", 1)[0] == args.source]

        counts["seen"] = len(deposits)
        log.info("dépôts à examiner", extra={"run_id": run_id, "nb": len(deposits)})

        for dep in deposits:
            try:
                src_sha = lake.sha256_file(dep.src_path)
                if not args.force and db.already_ingested(client, dep.source, dep.deposit_date, src_sha):
                    counts["skipped"] += 1
                    log.info("déjà ingéré, ignoré",
                             extra={"source": dep.source, "date": dep.deposit_date})
                    continue

                result = lake.ingest(dep, settings.lake_path, settings.salt)

                if result.status == "QUARANTINE":
                    counts["quarantined"] += 1
                    log.warning("mis en quarantaine", extra={"source": dep.source,
                                "date": dep.deposit_date, "motif": result.reason})
                else:
                    counts["ingested"] += 1
                    log.info("ingéré", extra={"source": dep.source, "date": dep.deposit_date,
                             "lignes": result.rows_out or "-", "octets": result.bytes_in})

                rows.append([
                    run_id, dep.source, date.fromisoformat(dep.deposit_date), str(dep.src_path),
                    result.src_sha256 or src_sha,
                    str(result.lake_path or ""), result.lake_sha256,
                    result.rows_in, result.rows_out, result.bytes_in,
                    result.status, result.reason, result.ingested_at,
                ])
            except Exception as exc:              # noqa: BLE001 — un flux ne doit pas tuer le run
                failures.append(f"{dep.source}/{dep.deposit_date}: {exc}")
                log.error("échec du dépôt", exc_info=True,
                          extra={"source": dep.source, "date": dep.deposit_date})

        status = ("FAILED" if failures and not counts["ingested"]
                  else "PARTIAL" if failures or counts["quarantined"]
                  else "OK")
        return 1 if status == "FAILED" else 0

    except Exception as exc:                      # noqa: BLE001
        failures.append(f"erreur fatale : {exc}")
        raise

    finally:
        # Le run est clôturé quoi qu'il arrive : un incident ne doit jamais
        # laisser une exécution éternellement « RUNNING » dans le journal.
        try:
            if rows:
                client.insert("ops.ingestion_log", rows, column_names=[
                    "run_id", "source", "deposit_date", "src_path", "src_sha256",
                    "lake_path", "lake_sha256", "rows_in", "rows_out", "bytes_in",
                    "status", "reason", "ingested_at"])
        except Exception:                         # noqa: BLE001
            log.error("journal d'ingestion non écrit", exc_info=True, extra={"run_id": run_id})
            failures.append("journal d'ingestion non écrit")
            status = "FAILED"
        try:
            _close_run(client, run_id, "lake", started, status, counts, " | ".join(failures))
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status, **counts})


def cmd_status(settings, args, log) -> int:
    client = db.connect(settings)

    print("\nDerniers runs")
    print(_table(client, """
        SELECT substring(run_id, 1, 8) AS run, command AS commande,
               toString(started_at) AS debut, toString(status) AS statut,
               deposits_ingested AS ingeres, deposits_skipped AS ignores,
               deposits_quarantined AS quarantaine
        FROM ops.run_log FINAL ORDER BY started_at DESC LIMIT 5"""))

    print("\nÉtat par dépôt (dernière ingestion connue)")
    print(_table(client, """
        SELECT source, toString(deposit_date) AS depot,
               toString(argMax(status, ingested_at)) AS statut,
               if(max(rows_out) = 0, '-', toString(max(rows_out))) AS lignes,
               formatReadableSize(max(bytes_in)) AS taille,
               count() AS ingestions,
               toString(max(ingested_at)) AS derniere
        FROM ops.ingestion_log
        GROUP BY source, deposit_date
        ORDER BY deposit_date, source"""))

    bloques = client.query(
        "SELECT count() FROM ops.run_log FINAL WHERE status = 'RUNNING'").result_rows[0][0]
    if bloques:
        print(f"\n⚠ {bloques} run(s) resté(s) à l'état RUNNING — exécution interrompue "
              "avant clôture. Voir logs/ pour la cause.")
    return 0


def _table(client, query: str) -> str:
    """Rend un résultat sous forme de table ASCII, mise en forme par ClickHouse."""
    return client.raw_query(query + " FORMAT PrettyCompactMonoBlock").decode("utf-8").rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eds", description="Pipeline de l'entrepôt de données de santé")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="crée la couche d'exploitation (ops.*)")

    p_lake = sub.add_parser("lake", help="recopie les dépôts du CHU dans le lake")
    p_lake.add_argument("--date", help="ne traiter que cette date de dépôt (AAAA-MM-JJ)")
    p_lake.add_argument("--source", help="ne traiter que ce flux (patients, sejours, ...)")
    p_lake.add_argument("--force", action="store_true", help="réingérer même si déjà traité")

    sub.add_parser("status", help="état des ingestions et des derniers runs")

    args = parser.parse_args(argv)

    try:
        settings = config.load_settings()
    except config.ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    log = logging_setup.setup(ROOT / "logs")
    handlers = {"init": cmd_init, "lake": cmd_lake, "status": cmd_status}
    try:
        return handlers[args.command](settings, args, log)
    except Exception:                                  # noqa: BLE001
        log.critical("échec de la commande", exc_info=True, extra={"commande": args.command})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
