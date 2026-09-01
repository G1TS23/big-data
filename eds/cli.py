"""Point d'entrée en ligne de commande de l'orchestrateur.

    eds init                    crée la couche d'exploitation (ops.*)
    eds lake                    recopie dans le lake tous les dépôts non traités
    eds lake --date 2026-08-27  rejoue une journée précise (idempotent)
    eds bronze                  charge le lake dans les tables typées
    eds silver                  reconstruit le modèle métier, en SQL
    eds gold                    reconstruit les indicateurs, cloisonnés
    eds acces                   crée les comptes et éprouve le cloisonnement
    eds metabase                provisionne les tableaux de bord
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

from eds import access, config, db, lake, loader, logging_setup, metabase, transform

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


_COLONNES_RUN = ["run_id", "command", "started_at", "finished_at", "status",
                 "unite", "objets_vus", "objets_traites", "objets_ignores",
                 "objets_quarantaine", "message", "updated_at"]


def _open_run(client, run_id: str, command: str, unite: str) -> datetime:
    """Ouvre une exécution. `unite` dit ce que ses compteurs dénombreront."""
    started = datetime.now()
    client.insert("ops.run_log",
                  [[run_id, command, started, None, "RUNNING", unite,
                    0, 0, 0, 0, "", datetime.now()]],
                  column_names=_COLONNES_RUN)
    return started


def _close_run(client, run_id, command, started, status, unite, counts, message=""):
    client.insert("ops.run_log",
                  [[run_id, command, started, datetime.now(), status, unite,
                    counts.get("vus", 0), counts.get("traites", 0),
                    counts.get("ignores", 0), counts.get("quarantaine", 0),
                    message[:2000], datetime.now()]],
                  column_names=_COLONNES_RUN)


# Les scripts sont joués dans cet ordre : la traçabilité existe avant la donnée.
INIT_SCRIPTS = ["40_ops.sql", "10_bronze.sql", "50_acces.sql"]


def cmd_init(settings, args, log) -> int:
    client = db.connect(settings)
    for script in INIT_SCRIPTS:
        count = db.execute_script(client, SQL_DIR / script)
        log.info("script exécuté", extra={"fichier": script, "instructions": count})
    tables = client.query(
        "SELECT database, name FROM system.tables "
        "WHERE database IN ('ops', 'bronze') ORDER BY database, name")
    for database, name in tables.result_rows:
        log.info("table prête", extra={"table": f"{database}.{name}"})
    return 0


def cmd_lake(settings, args, log) -> int:
    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "lake", "dépôt")

    counts = {"vus": 0, "traites": 0, "ignores": 0, "quarantaine": 0}
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

        counts["vus"] = len(deposits)
        log.info("dépôts à examiner", extra={"run_id": run_id, "nb": len(deposits)})

        # Reprise : une exécution interrompue laisse des écritures inachevées.
        # Elles sont effacées avant de recommencer — la source étant immuable,
        # il suffit de les réécrire.
        residus = lake.nettoyer_residus(settings.lake_path)
        if residus:
            log.warning("écritures inachevées effacées",
                        extra={"nb": len(residus),
                               "fichiers": ", ".join(r.name for r in residus[:5])})

        # Une seule requête pour toute l'exécution.
        deja = set() if args.force else db.depots_deja_ingeres(
            client, [d.deposit_date for d in deposits])

        for dep in deposits:
            try:
                if (dep.source, dep.deposit_date) in deja:
                    counts["ignores"] += 1
                    log.info("déjà ingéré, ignoré",
                             extra={"source": dep.source, "date": dep.deposit_date})
                    continue

                result = lake.ingest(dep, settings.lake_path, settings.salt)

                if result.status == "QUARANTINE":
                    counts["quarantaine"] += 1
                    log.warning("mis en quarantaine", extra={"source": dep.source,
                                "date": dep.deposit_date, "motif": result.reason})
                else:
                    counts["traites"] += 1
                    log.info("ingéré", extra={"source": dep.source, "date": dep.deposit_date,
                             "lignes": result.rows_out or "-", "octets": result.bytes_in})

                rows.append([
                    run_id, dep.source, date.fromisoformat(dep.deposit_date), str(dep.src_path),
                    result.src_sha256,
                    str(result.lake_path or ""), result.lake_sha256,
                    result.rows_in, result.rows_out, result.bytes_in,
                    result.status, result.reason, result.ingested_at,
                ])
            except Exception as exc:              # noqa: BLE001 — un flux ne doit pas tuer le run
                failures.append(f"{dep.source}/{dep.deposit_date}: {exc}")
                log.error("échec du dépôt", exc_info=True,
                          extra={"source": dep.source, "date": dep.deposit_date})

        status = ("FAILED" if failures and not counts["traites"]
                  else "PARTIAL" if failures or counts["quarantaine"]
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
            _close_run(client, run_id, "lake", started, status, "dépôt", counts, " | ".join(failures))
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status, **counts})


def cmd_bronze(settings, args, log) -> int:
    """Charge dans bronze les dépôts présents dans le lake."""
    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "bronze", "dépôt")

    counts = {"vus": 0, "traites": 0, "ignores": 0, "quarantaine": 0}
    rows: list[list] = []
    failures: list[str] = []
    status = "FAILED"

    try:
        sources = config.load_sources()
        specs = {}
        for name, spec in sources.items():
            for file_spec in lake._file_specs(name, spec):
                if "bronze" in file_spec:
                    specs[file_spec["_name"]] = file_spec["bronze"]

        # On ne charge que ce que le lake contient réellement : la liste des
        # dépôts vient du journal d'ingestion, pas d'un parcours de répertoire.
        query = ("SELECT source, toString(deposit_date), argMax(lake_path, ingested_at) "
                 "FROM ops.ingestion_log WHERE status = 'OK' ")
        params = {}
        if args.date:
            query += "AND deposit_date = {d:Date} "
            params["d"] = args.date
        if args.source:
            query += "AND splitByChar('/', source)[1] = {s:String} "
            params["s"] = args.source
        query += "GROUP BY source, deposit_date ORDER BY deposit_date, source"

        deposits = client.query(query, parameters=params).result_rows
        counts["vus"] = len(deposits)
        log.info("dépôts à charger", extra={"run_id": run_id, "nb": len(deposits)})

        for source, deposit_date, lake_path in deposits:
            bronze = specs.get(source)
            if bronze is None:
                counts["ignores"] += 1
                log.warning("aucune cible bronze déclarée", extra={"source": source})
                continue
            try:
                # Rejeu : la partition du jour est effacée avant réinsertion.
                loader.drop_partition(client, bronze["table"], deposit_date)
                result = loader.load_file(settings, bronze, Path(lake_path),
                                          deposit_date, run_id)
                counts["traites"] += 1
                log.info("chargé", extra={"source": source, "date": deposit_date,
                         "table": result.table, "lignes": result.rows_loaded,
                         "ms": result.duration_ms})
                rows.append([run_id, source, date.fromisoformat(deposit_date),
                             result.table, lake_path, result.rows_loaded,
                             result.bytes_read, result.duration_ms, "OK", "",
                             datetime.now()])
            except Exception as exc:              # noqa: BLE001 — un flux ne doit pas tuer le run
                failures.append(f"{source}/{deposit_date}: {exc}")
                log.error("échec du chargement", exc_info=True,
                          extra={"source": source, "date": deposit_date})
                rows.append([run_id, source, date.fromisoformat(deposit_date),
                             bronze["table"], lake_path, 0, 0, 0, "FAILED",
                             str(exc)[:1000], datetime.now()])

        status = ("FAILED" if failures and not counts["traites"]
                  else "PARTIAL" if failures or counts["ignores"]
                  else "OK")
        return 1 if status == "FAILED" else 0

    except Exception as exc:                      # noqa: BLE001
        failures.append(f"erreur fatale : {exc}")
        raise

    finally:
        try:
            if rows:
                client.insert("ops.load_log", rows, column_names=[
                    "run_id", "source", "deposit_date", "target_table", "lake_path",
                    "rows_loaded", "bytes_read", "duration_ms", "status", "message",
                    "loaded_at"])
        except Exception:                         # noqa: BLE001
            log.error("journal de chargement non écrit", exc_info=True,
                      extra={"run_id": run_id})
        try:
            _close_run(client, run_id, "bronze", started, status, "dépôt", counts,
                       " | ".join(failures))
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status, **counts})


def cmd_silver(settings, args, log) -> int:
    """Reconstruit la couche silver depuis bronze, en SQL."""
    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "silver", "instruction")
    counts = {"vus": 0, "traites": 0, "ignores": 0, "quarantaine": 0}
    status, message = "FAILED", ""

    try:
        regles = transform.load_regles()
        parameters = transform.to_parameters(regles)
        transform.snapshot_parametres(client, run_id, parameters)
        log.info("règles appliquées", extra={"run_id": run_id, **parameters})

        n = transform.run_script(client, SQL_DIR / "20_silver.sql", run_id, parameters)
        counts["traites"] = n
        counts["vus"] = n

        tables = client.query(
            "SELECT name, total_rows FROM system.tables "
            "WHERE database = 'silver' ORDER BY name").result_rows
        for name, rows in tables:
            log.info("table construite", extra={"table": f"silver.{name}", "lignes": rows})

        rejets = client.query(
            "SELECT table_source, regle, count() FROM ops.rejects "
            "WHERE run_id = {r:String} GROUP BY table_source, regle "
            "ORDER BY table_source, regle", parameters={"r": run_id}).result_rows
        for table_source, regle, n_rejets in rejets:
            log.warning("lignes écartées", extra={"table": table_source,
                        "regle": regle, "lignes": n_rejets})

        status = "OK"
        return 0

    except Exception as exc:                      # noqa: BLE001
        message = str(exc)
        raise

    finally:
        try:
            _close_run(client, run_id, "silver", started, status, "instruction", counts, message)
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status})


def cmd_gold(settings, args, log) -> int:
    """Reconstruit les deux couches gold, cloisonnées par usage."""
    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "gold", "instruction")
    counts = {"vus": 0, "traites": 0, "ignores": 0, "quarantaine": 0}
    status, message = "FAILED", ""

    try:
        regles = transform.load_regles()
        parameters = transform.to_parameters(regles)
        transform.snapshot_parametres(client, run_id, parameters)

        # Le seuil et le définisseur sont SCELLÉS dans les vues de recherche :
        # ni l'un ni l'autre ne doit pouvoir être fourni par l'appelant.
        substitutions = {"K_ANONYMITE": parameters["k"], "DEFINER": settings.ch_user}

        for script in ("30_gold_pilotage.sql", "31_gold_recherche.sql"):
            n = transform.run_script(client, SQL_DIR / script, run_id,
                                     parameters, substitutions)
            counts["traites"] += n
            log.info("script exécuté", extra={"fichier": script, "instructions": n})

        for base in ("gold_pilotage", "gold_recherche"):
            objets = client.query(
                "SELECT name, engine FROM system.tables WHERE database = {d:String} "
                "ORDER BY name", parameters={"d": base}).result_rows
            for name, engine in objets:
                log.info("objet gold", extra={"objet": f"{base}.{name}", "type": engine})

        counts["vus"] = counts["traites"]
        status = "OK"
        return 0

    except Exception as exc:                      # noqa: BLE001
        message = str(exc)
        raise
    finally:
        try:
            _close_run(client, run_id, "gold", started, status, "instruction", counts, message)
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status})


def cmd_acces(settings, args, log) -> int:
    """Crée les comptes cloisonnés puis éprouve le cloisonnement."""
    client = db.connect(settings)
    comptes = access.ensure_users(client, settings)
    log.info("comptes de restitution", extra={"comptes": ", ".join(comptes)})

    constats = access.verifier(settings)
    largeur = max(len(c["objet"]) for c in constats)
    print()
    print(f"  {'COMPTE':<14} {'OBJET':<{largeur}}  {'ATTENDU':<9} {'OBTENU':<9} ")
    for c in constats:
        marque = "ok " if c["conforme"] else "ÉCHEC"
        print(f"  {c['compte']:<14} {c['objet']:<{largeur}}  "
              f"{c['attendu']:<9} {c['obtenu']:<9} {marque}")

    manquements = [c for c in constats if not c["conforme"]]

    # Second niveau : l'interface de restitution ne doit pas contourner le moteur.
    try:
        restitution = metabase.verifier_cloisonnement(settings)
    except Exception as exc:                      # noqa: BLE001
        log.warning("cloisonnement Metabase non vérifié",
                    extra={"motif": str(exc)[:120]})
        restitution = []

    if restitution:
        largeur = max(len(c["action"]) for c in restitution)
        print()
        print(f"  {'COMPTE METABASE':<24} {'ACTION':<{largeur}}  {'ATTENDU':<9} {'OBTENU':<9}")
        for c in restitution:
            print(f"  {c['compte']:<24} {c['action']:<{largeur}}  "
                  f"{c['attendu']:<9} {c['obtenu']:<9} {'ok ' if c['conforme'] else 'ÉCHEC'}")
        # Une base injoignable n'est pas un défaut de cloisonnement : on le dit,
        # sans transformer une panne en alerte de conformité.
        indisponibles = [c for c in restitution if c.get("indisponible")]
        if indisponibles:
            log.warning("source de données injoignable — cloisonnement de la "
                        "restitution non concluant",
                        extra={"controles": len(indisponibles)})
        manquements += [c for c in restitution
                        if not c["conforme"] and not c.get("indisponible")]

    print()
    if manquements:
        log.error("cloisonnement non conforme", extra={"manquements": len(manquements)})
        return 1
    log.info("cloisonnement vérifié",
             extra={"moteur": len(constats), "restitution": len(restitution)})
    return 0


def cmd_metabase(settings, args, log) -> int:
    """Provisionne la restitution : connexions, cloisonnement, tableaux de bord."""
    import time

    client = db.connect(settings)
    run_id = uuid.uuid4().hex
    started = _open_run(client, run_id, "metabase", "carte")
    counts = {"vus": 0, "traites": 0, "ignores": 0, "quarantaine": 0}
    status, message = "FAILED", ""

    try:
        spec = metabase.charger_specification()
        mb = metabase.Metabase(settings)
        mb.connect()

        # Une connexion par usage, chacune avec SON compte ClickHouse restreint :
        # le cloisonnement du moteur se propage jusqu'à l'outil de restitution.
        bases, groupes = {}, {}
        for connexion in spec["connexions"]:
            bases[connexion["nom"]] = mb.ensure_database(
                connexion["nom"], connexion["base"], connexion["compte"],
                getattr(settings, connexion["secret"]))
            groupes[connexion["groupe"]] = mb.ensure_group(connexion["groupe"])
            log.info("connexion", extra={"nom": connexion["nom"],
                     "base": connexion["base"], "compte": connexion["compte"]})

        mb.cloisonner({groupes[c["groupe"]]: bases[c["nom"]] for c in spec["connexions"]})
        log.info("cloisonnement des bases appliqué", extra={"groupes": len(groupes)})

        for base_id in bases.values():
            mb.post(f"/api/database/{base_id}/sync_schema", {})
        time.sleep(5)

        collections = {}
        for tableau in spec["tableaux"]:
            collection_id = mb.ensure_collection(tableau["collection"], tableau["description"])
            database_id = bases[tableau["connexion"]]
            posees = []
            for carte in tableau["cartes"]:
                if "texte" in carte:
                    posees.append(metabase.carte_texte(carte))
                    continue
                sql = (metabase.SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8")
                carte_complete = {**carte,
                                  "affichage": metabase.affichage(carte, sql, spec["couleurs"])}
                card_id = mb.ensure_card(carte_complete, database_id, collection_id)
                posees.append({"card_id": card_id, "row": carte["row"], "col": carte["col"],
                               "size_x": carte["size_x"], "size_y": carte["size_y"],
                               "series": [], "parameter_mappings": [],
                               "visualization_settings": {}})
                counts["traites"] += 1

            dashboard_id = mb.ensure_dashboard(tableau["nom"], tableau["description"],
                                               collection_id)
            mb.poser_cartes(dashboard_id, posees)
            collections[tableau["collection"]] = collection_id
            log.info("tableau de bord", extra={"nom": tableau["nom"],
                     "cartes": len(posees), "url": f"{settings.metabase_url}/dashboard/{dashboard_id}"})

        # Les collections ne se cloisonnent qu'une fois créées.
        par_groupe = {}
        for connexion, tableau in zip(spec["connexions"], spec["tableaux"]):
            par_groupe[groupes[connexion["groupe"]]] = collections[tableau["collection"]]
        mb.cloisonner_collections(par_groupe)
        log.info("cloisonnement des collections appliqué")

        for connexion in spec["connexions"]:
            demo = connexion.get("compte_demo")
            if not demo:
                continue
            mb.ensure_utilisateur(demo["email"], demo["prenom"], demo["nom"],
                                  settings.metabase_demo_password,
                                  groupes[connexion["groupe"]])
            log.info("compte de démonstration", extra={"email": demo["email"],
                     "groupe": connexion["groupe"]})

        counts["vus"] = counts["traites"]
        status = "OK"
        return 0

    except Exception as exc:                      # noqa: BLE001
        message = str(exc)
        raise
    finally:
        try:
            _close_run(client, run_id, "metabase", started, status, "carte", counts, message)
        except Exception:                         # noqa: BLE001
            log.error("run non clôturé", exc_info=True, extra={"run_id": run_id})
        log.info("run terminé", extra={"run_id": run_id, "statut": status})


def cmd_status(settings, args, log) -> int:
    client = db.connect(settings)

    print("\nDerniers runs")
    print(_table(client, """
        SELECT substring(run_id, 1, 8) AS run, command AS commande,
               toString(started_at) AS debut, toString(status) AS statut,
               concat(toString(objets_traites), ' ', toString(unite),
                      if(objets_traites > 1, 's', '')) AS traites,
               objets_ignores AS ignores, objets_quarantaine AS quarantaine
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

    p_bronze = sub.add_parser("bronze", help="charge le lake dans les tables bronze")
    p_bronze.add_argument("--date", help="ne charger que cette date de dépôt (AAAA-MM-JJ)")
    p_bronze.add_argument("--source", help="ne charger que ce flux")

    sub.add_parser("silver", help="reconstruit la couche silver depuis bronze")

    sub.add_parser("gold", help="reconstruit les indicateurs, cloisonnés par usage")

    sub.add_parser("acces", help="crée les comptes cloisonnés et vérifie le cloisonnement")

    sub.add_parser("metabase", help="provisionne connexions et tableaux de bord")

    sub.add_parser("status", help="état des ingestions et des derniers runs")

    args = parser.parse_args(argv)

    try:
        settings = config.load_settings()
    except config.ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    log = logging_setup.setup(ROOT / "logs")
    handlers = {"init": cmd_init, "lake": cmd_lake, "bronze": cmd_bronze,
                "silver": cmd_silver, "gold": cmd_gold, "acces": cmd_acces, "metabase": cmd_metabase,
                "status": cmd_status}
    try:
        return handlers[args.command](settings, args, log)
    except Exception:                                  # noqa: BLE001
        log.critical("échec de la commande", exc_info=True, extra={"commande": args.command})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
