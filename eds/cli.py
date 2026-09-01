"""Point d'entrée en ligne de commande de l'orchestrateur.

    eds init                    crée la traçabilité, puis les tables
    eds lake                    recopie dans le lake les dépôts non traités
    eds lake --date 2026-08-27  rejoue une journée précise (idempotent)
    eds bronze                  charge le lake dans les tables typées
    eds silver                  reconstruit le modèle métier, en SQL
    eds gold                    reconstruit les indicateurs, cloisonnés
    eds acces                   crée les comptes et éprouve le cloisonnement
    eds metabase                provisionne les tableaux de bord
    eds status                  état des dépôts et des dernières exécutions

Chaque exécution porte un identifiant inscrit sur toutes les lignes qu'elle
produit : c'est le fil qui relie un chiffre de tableau de bord au fichier
source dont il provient. Le cycle de vie de cet identifiant — ouverture,
compteurs, clôture même en cas d'incident — vit dans eds/execution.py, ce qui
laisse ici la seule logique de chaque commande.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

from eds import access, config, db, lake, loader, metabase, transform
from eds.execution import Execution, journaliser

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

# Joués dans cet ordre : la traçabilité existe avant la donnée.
SCRIPTS_INIT = ["40_ops.sql", "10_bronze.sql", "50_acces.sql"]

COLONNES_INGESTION = ["run_id", "source", "deposit_date", "src_path", "lake_path",
                      "rows_in", "rows_out", "bytes_in", "status", "reason", "ingested_at"]
COLONNES_CHARGEMENT = ["run_id", "source", "deposit_date", "target_table", "lake_path",
                       "rows_loaded", "bytes_read", "duration_ms", "status", "message",
                       "loaded_at"]


# ─── Création du socle ──────────────────────────────────────────────────────

def cmd_init(settings, args, log) -> int:
    client = db.connect(settings)
    for script in SCRIPTS_INIT:
        nb = db.execute_script(client, SQL_DIR / script)
        log.info("script exécuté", extra={"fichier": script, "instructions": nb})
    tables = client.query("SELECT database, name FROM system.tables "
                          "WHERE database IN ('ops', 'bronze') ORDER BY database, name")
    for base, nom in tables.result_rows:
        log.info("table prête", extra={"table": f"{base}.{nom}"})
    return 0


# ─── Ingestion ──────────────────────────────────────────────────────────────

def cmd_lake(settings, args, log) -> int:
    """Recopie les dépôts du CHU dans le lake, pseudonymisés à la porte."""
    with Execution(settings, "lake", "dépôt", log) as run:
        depots = lake.discover(settings.source_path, config.load_sources())
        if args.date:
            depots = [d for d in depots if d.deposit_date == args.date]
        if args.source:
            depots = [d for d in depots if d.source.split("/", 1)[0] == args.source]
        run.vus = len(depots)
        log.info("dépôts à examiner", extra={"run_id": run.run_id, "nb": len(depots)})

        # Reprise : une exécution interrompue laisse des écritures inachevées.
        # La source étant immuable, il suffit de les effacer et de recommencer.
        residus = lake.nettoyer_residus(settings.lake_path)
        if residus:
            log.warning("écritures inachevées effacées",
                        extra={"nb": len(residus),
                               "fichiers": ", ".join(r.name for r in residus[:5])})

        # Une seule requête pour toute l'exécution, et non une par dépôt.
        deja = set() if args.force else db.depots_deja_ingeres(
            run.client, [d.deposit_date for d in depots])

        for depot in depots:
            with run.etape(f"{depot.source}/{depot.deposit_date}"):
                # Ignorer suppose que le fichier est TOUJOURS dans le lake. Un
                # journal qui affirme « ingéré » alors que la copie a disparu
                # ferait échouer l'étape suivante sans rien expliquer.
                if (depot.source, depot.deposit_date) in deja and lake.est_publie(
                        depot, settings.lake_path):
                    run.ignores += 1
                    log.info("déjà ingéré, ignoré",
                             extra={"source": depot.source, "date": depot.deposit_date})
                    continue

                resultat = lake.ingest(depot, settings.lake_path, settings.salt)
                if resultat.status == "QUARANTINE":
                    run.quarantaine += 1
                    log.warning("mis en quarantaine",
                                extra={"source": depot.source, "date": depot.deposit_date,
                                       "motif": resultat.reason})
                else:
                    run.traites += 1
                    log.info("ingéré", extra={"source": depot.source,
                             "date": depot.deposit_date,
                             "lignes": resultat.rows_out or "-", "octets": resultat.bytes_in})

                run.journaliser("ops.ingestion_log", COLONNES_INGESTION, [
                    run.run_id, depot.source, date.fromisoformat(depot.deposit_date),
                    str(depot.src_path), str(resultat.lake_path or ""),
                    resultat.rows_in, resultat.rows_out, resultat.bytes_in,
                    resultat.status, resultat.reason, resultat.ingested_at])
    return run.code_retour


def cmd_bronze(settings, args, log) -> int:
    """Charge dans bronze les dépôts que le lake contient réellement."""
    with Execution(settings, "bronze", "dépôt", log) as run:
        cibles = {spec["_name"]: spec["bronze"]
                  for nom, source in config.load_sources().items()
                  for spec in lake._file_specs(nom, source) if "bronze" in spec}

        # La liste vient du journal d'ingestion, pas d'un parcours de répertoire :
        # on ne charge que ce qui a été effectivement publié dans le lake.
        requete = ("SELECT source, toString(deposit_date), argMax(lake_path, ingested_at) "
                   "FROM ops.ingestion_log WHERE status = 'OK' ")
        params = {}
        if args.date:
            requete += "AND deposit_date = {d:Date} "
            params["d"] = args.date
        if args.source:
            requete += "AND splitByChar('/', source)[1] = {s:String} "
            params["s"] = args.source
        requete += "GROUP BY source, deposit_date ORDER BY deposit_date, source"

        depots = run.client.query(requete, parameters=params).result_rows
        run.vus = len(depots)
        log.info("dépôts à charger", extra={"run_id": run.run_id, "nb": len(depots)})

        for source, jour, chemin in depots:
            bronze = cibles.get(source)
            if bronze is None:
                run.incidents.append(f"{source} : aucune cible bronze déclarée")
                log.warning("aucune cible bronze déclarée", extra={"source": source})
                continue

            with run.etape(f"{source}/{jour}"):
                try:
                    # Rejeu : la partition du jour est effacée avant réinsertion.
                    loader.drop_partition(run.client, bronze["table"], jour)
                    resultat = loader.load_file(settings, bronze, Path(chemin), jour, run.run_id)
                    run.traites += 1
                    log.info("chargé", extra={"source": source, "date": jour,
                             "table": resultat.table, "lignes": resultat.rows_loaded,
                             "ms": resultat.duration_ms})
                    run.journaliser("ops.load_log", COLONNES_CHARGEMENT, [
                        run.run_id, source, date.fromisoformat(jour), resultat.table,
                        chemin, resultat.rows_loaded, resultat.bytes_read,
                        resultat.duration_ms, "OK", "", datetime.now()])
                except Exception as exc:
                    run.journaliser("ops.load_log", COLONNES_CHARGEMENT, [
                        run.run_id, source, date.fromisoformat(jour), bronze["table"],
                        chemin, 0, 0, 0, "FAILED", str(exc)[:1000], datetime.now()])
                    raise                          # run.etape enregistre l'incident
    return run.code_retour


# ─── Transformations ────────────────────────────────────────────────────────

def _executer_scripts(run, settings, scripts, log, substitutions=None):
    """Joue des scripts SQL avec les règles métier, et consigne lesquelles."""
    regles = transform.load_regles()
    parametres = transform.to_parameters(regles)
    transform.snapshot_parametres(run.client, run.run_id, parametres)
    for script in scripts:
        nb = transform.run_script(run.client, SQL_DIR / script, run.run_id,
                                  parametres, substitutions)
        run.traites += nb
        log.info("script exécuté", extra={"fichier": script, "instructions": nb})
    run.vus = run.traites
    return parametres


def cmd_silver(settings, args, log) -> int:
    """Reconstruit la couche silver depuis bronze, en SQL."""
    with Execution(settings, "silver", "instruction", log) as run:
        parametres = _executer_scripts(run, settings, ["20_silver.sql"], log)
        log.info("règles appliquées", extra={"run_id": run.run_id, **parametres})

        for nom, lignes in run.client.query(
                "SELECT name, total_rows FROM system.tables "
                "WHERE database = 'silver' ORDER BY name").result_rows:
            log.info("table construite", extra={"table": f"silver.{nom}", "lignes": lignes})

        for table, regle, nb in run.client.query(
                "SELECT table_source, regle, count() FROM ops.rejects "
                "WHERE run_id = {r:String} GROUP BY table_source, regle "
                "ORDER BY table_source, regle", parameters={"r": run.run_id}).result_rows:
            log.warning("lignes écartées",
                        extra={"table": table, "regle": regle, "lignes": nb})
    return run.code_retour


def cmd_gold(settings, args, log) -> int:
    """Reconstruit les deux couches gold, cloisonnées par usage."""
    with Execution(settings, "gold", "instruction", log) as run:
        regles = transform.load_regles()
        # Le seuil et le définisseur sont SCELLÉS dans les vues de recherche :
        # ni l'un ni l'autre ne doit pouvoir être fourni par l'appelant.
        substitutions = {"K_ANONYMITE": transform.to_parameters(regles)["k"],
                         "DEFINER": settings.ch_user}
        _executer_scripts(run, settings,
                          ["30_gold_pilotage.sql", "31_gold_recherche.sql"],
                          log, substitutions)

        for base in ("gold_pilotage", "gold_recherche"):
            for nom, moteur in run.client.query(
                    "SELECT name, engine FROM system.tables WHERE database = {d:String} "
                    "ORDER BY name", parameters={"d": base}).result_rows:
                log.info("objet gold", extra={"objet": f"{base}.{nom}", "type": moteur})
    return run.code_retour


# ─── Restitution ────────────────────────────────────────────────────────────

def cmd_metabase(settings, args, log) -> int:
    """Provisionne la restitution : connexions, cloisonnement, tableaux de bord."""
    with Execution(settings, "metabase", "carte", log) as run:
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
            collections[tableau["collection"]] = collection_id
            database_id = bases[tableau["connexion"]]

            posees = []
            for carte in tableau["cartes"]:
                if "texte" in carte:
                    posees.append(metabase.carte_texte(carte))
                    continue
                requete = (metabase.SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8")
                complete = {**carte,
                            "affichage": metabase.affichage(carte, requete, spec["couleurs"])}
                posees.append({"card_id": mb.ensure_card(complete, database_id, collection_id),
                               "row": carte["row"], "col": carte["col"],
                               "size_x": carte["size_x"], "size_y": carte["size_y"],
                               "series": [], "parameter_mappings": [],
                               "visualization_settings": {}})
                run.traites += 1

            dashboard_id = mb.ensure_dashboard(tableau["nom"], tableau["description"],
                                               collection_id)
            mb.poser_cartes(dashboard_id, posees)
            log.info("tableau de bord", extra={"nom": tableau["nom"], "cartes": len(posees),
                     "url": f"{settings.metabase_url}/dashboard/{dashboard_id}"})

        # Les collections ne se cloisonnent qu'une fois créées.
        mb.cloisonner_collections({groupes[c["groupe"]]: collections[t["collection"]]
                                   for c, t in zip(spec["connexions"], spec["tableaux"])})
        log.info("cloisonnement des collections appliqué")

        for connexion in spec["connexions"]:
            demo = connexion.get("compte_demo")
            if demo:
                mb.ensure_utilisateur(demo["email"], demo["prenom"], demo["nom"],
                                      settings.metabase_demo_password,
                                      groupes[connexion["groupe"]])
                log.info("compte de démonstration",
                         extra={"email": demo["email"], "groupe": connexion["groupe"]})
        run.vus = run.traites
    return run.code_retour


# ─── Contrôles et état ──────────────────────────────────────────────────────

def cmd_acces(settings, args, log) -> int:
    """Crée les comptes cloisonnés puis éprouve le cloisonnement, aux deux niveaux."""
    client = db.connect(settings)
    log.info("comptes de restitution",
             extra={"comptes": ", ".join(access.ensure_users(client, settings))})

    moteur = access.verifier(settings)
    _tableau_constats(moteur, "COMPTE", "OBJET", "objet")
    manquements = [c for c in moteur if not c["conforme"]]

    # Second niveau : l'interface ne doit pas contourner le moteur.
    try:
        restitution = metabase.verifier_cloisonnement(settings)
    except Exception as exc:                       # noqa: BLE001
        log.warning("cloisonnement Metabase non vérifié", extra={"motif": str(exc)[:120]})
        restitution = []

    if restitution:
        _tableau_constats(restitution, "COMPTE METABASE", "ACTION", "action")
        # Une base injoignable n'est pas un défaut de cloisonnement.
        indisponibles = [c for c in restitution if c.get("indisponible")]
        if indisponibles:
            log.warning("source de données injoignable — contrôle non concluant",
                        extra={"controles": len(indisponibles)})
        manquements += [c for c in restitution
                        if not c["conforme"] and not c.get("indisponible")]

    print()
    if manquements:
        log.error("cloisonnement non conforme", extra={"manquements": len(manquements)})
        return 1
    log.info("cloisonnement vérifié",
             extra={"moteur": len(moteur), "restitution": len(restitution)})
    return 0


def _tableau_constats(constats, titre_compte: str, titre_objet: str, cle: str) -> None:
    largeur = max(len(c[cle]) for c in constats)
    print(f"\n  {titre_compte:<24} {titre_objet:<{largeur}}  {'ATTENDU':<9} {'OBTENU':<9}")
    for c in constats:
        print(f"  {c['compte']:<24} {c[cle]:<{largeur}}  "
              f"{c['attendu']:<9} {c['obtenu']:<9} {'ok ' if c['conforme'] else 'ÉCHEC'}")


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
        print(f"\n⚠ {bloques} exécution(s) restée(s) à l'état RUNNING — interrompue(s) "
              "avant clôture. Voir logs/ pour la cause.")
    return 0


def _table(client, requete: str) -> str:
    """Rend un résultat en table ASCII, mise en forme par le moteur."""
    return client.raw_query(requete + " FORMAT PrettyCompactMonoBlock").decode("utf-8").rstrip()


# ─── Ligne de commande ──────────────────────────────────────────────────────

COMMANDES = {"init": cmd_init, "lake": cmd_lake, "bronze": cmd_bronze,
             "silver": cmd_silver, "gold": cmd_gold, "acces": cmd_acces,
             "metabase": cmd_metabase, "status": cmd_status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eds", description="Pipeline de l'entrepôt de données de santé")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="crée la traçabilité, puis les tables")

    p_lake = sub.add_parser("lake", help="recopie les dépôts du CHU dans le lake")
    p_lake.add_argument("--date", help="ne traiter que cette date de dépôt (AAAA-MM-JJ)")
    p_lake.add_argument("--source", help="ne traiter que ce flux (patients, sejours, ...)")
    p_lake.add_argument("--force", action="store_true", help="réingérer même si déjà traité")

    p_bronze = sub.add_parser("bronze", help="charge le lake dans les tables bronze")
    p_bronze.add_argument("--date", help="ne charger que cette date de dépôt (AAAA-MM-JJ)")
    p_bronze.add_argument("--source", help="ne charger que ce flux")

    sub.add_parser("silver", help="reconstruit le modèle métier depuis bronze")
    sub.add_parser("gold", help="reconstruit les indicateurs, cloisonnés par usage")
    sub.add_parser("acces", help="crée les comptes cloisonnés et vérifie le cloisonnement")
    sub.add_parser("metabase", help="provisionne connexions et tableaux de bord")
    sub.add_parser("status", help="état des dépôts et des dernières exécutions")

    args = parser.parse_args(argv)

    try:
        settings = config.load_settings()
    except config.ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    log = journaliser(ROOT / "logs")
    try:
        return COMMANDES[args.command](settings, args, log)
    except Exception:                              # noqa: BLE001
        log.critical("échec de la commande", exc_info=True, extra={"commande": args.command})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
