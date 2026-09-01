"""Point d'entrée en ligne de commande de l'orchestrateur.

    eds init                    crée la traçabilité, puis les tables
    eds lake                    recopie dans le lake les dépôts non traités
    eds lake --date 2026-08-27  rejoue une journée précise (idempotent)
    eds bronze                  charge le lake dans les tables typées
    eds run                     enchaîne lake → bronze → silver → gold
    eds scheduler               déclenche le pipeline à intervalle régulier
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
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eds import access, bronze, config, gold, lake, metabase, silver, sql, verrou
from eds.execution import Execution, journaliser

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
FUSEAU = "Europe/Paris"

# Joués dans cet ordre : la traçabilité existe avant la donnée.
SCRIPTS_INIT = ["40_ops.sql", "10_bronze.sql", "50_acces.sql"]

def _relatif(chemin, racine) -> str:
    """Chemin relatif à sa racine, pour que le journal reste portable."""
    if not chemin:
        return ""
    chemin = Path(chemin)
    try:
        return str(chemin.relative_to(racine))
    except ValueError:
        return str(chemin)          # hors racine : on garde tel quel, c'est un signal


COLONNES_INGESTION = ["run_id", "source", "deposit_date", "src_path", "lake_path",
                      "rows_in", "rows_out", "bytes_in", "status", "reason", "ingested_at"]
COLONNES_CHARGEMENT = ["run_id", "source", "deposit_date", "target_table", "lake_path",
                       "rows_loaded", "bytes_read", "duration_ms", "status", "message",
                       "loaded_at"]


# ─── Création du socle ──────────────────────────────────────────────────────

def cmd_init(settings, args, log) -> int:
    client = sql.connect(settings)
    for script in SCRIPTS_INIT:
        nb = sql.execute_script(client, SQL_DIR / script)
        log.info("script exécuté", extra={"fichier": script, "instructions": nb})
    tables = client.query("SELECT database, name FROM system.tables "
                          "WHERE database IN ('ops', 'bronze') ORDER BY database, name")
    for base, nom in tables.result_rows:
        log.info("table prête", extra={"table": f"{base}.{nom}"})
    return 0


# ─── Ingestion ──────────────────────────────────────────────────────────────

def cmd_lake(settings, args, log) -> int:
    """Recopie les dépôts du CHU dans le lake, pseudonymisés à la porte."""
    with Execution(settings, "lake", "dépôt") as run:
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
        deja = set() if args.force else sql.depots_deja_ingeres(
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
                    _relatif(depot.src_path, settings.source_path),
                    _relatif(resultat.lake_path, settings.lake_path),
                    resultat.rows_in, resultat.rows_out, resultat.bytes_in,
                    resultat.status, resultat.reason, resultat.ingested_at])
    return run.code_retour


def cmd_bronze(settings, args, log) -> int:
    """Charge dans bronze les dépôts que le lake contient réellement."""
    with Execution(settings, "bronze", "dépôt") as run:
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
            # `cible` et non `bronze` : le module porte déjà ce nom.
            cible = cibles.get(source)
            if cible is None:
                run.incidents.append(f"{source} : aucune cible bronze déclarée")
                log.warning("aucune cible bronze déclarée", extra={"source": source})
                continue

            with run.etape(f"{source}/{jour}"):
                try:
                    # Rejeu : la partition du jour est effacée avant réinsertion.
                    bronze.drop_partition(run.client, cible["table"], jour)
                    # Le chemin est relatif : chaque environnement le résout
                    # contre SON lake.
                    resultat = bronze.load_file(settings, cible,
                                                settings.lake_path / chemin, jour, run.run_id)
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
                        run.run_id, source, date.fromisoformat(jour), cible["table"],
                        chemin, 0, 0, 0, "FAILED", str(exc)[:1000], datetime.now()])
                    raise                          # run.etape enregistre l'incident
    return run.code_retour


# ─── Transformations ────────────────────────────────────────────────────────

def cmd_silver(settings, args, log) -> int:
    """Reconstruit la couche silver depuis bronze, en SQL."""
    with Execution(settings, "silver", "instruction") as run:
        silver.construire(run)
    return run.code_retour


def cmd_gold(settings, args, log) -> int:
    """Reconstruit les deux couches gold, cloisonnées par usage."""
    with Execution(settings, "gold", "instruction") as run:
        gold.construire(run)
    return run.code_retour


# ─── Restitution ────────────────────────────────────────────────────────────

def cmd_metabase(settings, args, log) -> int:
    """Provisionne la restitution : connexions, cloisonnement, tableaux de bord."""
    with Execution(settings, "metabase", "carte") as run:
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


# ─── La chaîne complète ─────────────────────────────────────────────────────

# L'ordre est celui du patron médaillon : chaque étape consomme la précédente.
# La restitution n'y figure pas — les cartes interrogent gold directement, elles
# n'ont donc rien à rafraîchir. `eds metabase` et `eds acces` provisionnent, une
# fois pour toutes.
CHAINE = ["lake", "bronze", "silver", "gold"]


def cmd_run(settings, args, log) -> int:
    """Enchaîne le pipeline, et s'arrête à la première étape en échec.

    Poursuivre après un échec produirait des indicateurs calculés sur des
    données incomplètes — pire qu'une absence d'indicateurs, parce que rien ne
    les distinguerait des bons.
    """
    try:
        with verrou.unique(ROOT / "logs" / "eds.lock", attendre=args.attendre):
            debut = time.perf_counter()
            for rang, etape in enumerate(CHAINE, 1):
                log.info("étape", extra={"rang": f"{rang}/{len(CHAINE)}", "nom": etape})
                code = COMMANDES[etape](settings, args, log)
                if code != 0:
                    log.error("chaîne interrompue", extra={"etape": etape, "code": code})
                    return code
            log.info("chaîne complète",
                     extra={"etapes": len(CHAINE), "duree_s": round(time.perf_counter() - debut, 2)})
            return 0
    except verrou.DejaEnCours as exc:
        log.warning("exécution ignorée", extra={"motif": str(exc)})
        return 0                                   # ce n'est pas une erreur


def cmd_scheduler(settings, args, log) -> int:
    """Déclenche le pipeline à intervalle régulier, sans intervention.

    Trois précautions, chacune pour un incident réel :
      max_instances=1  une exécution qui déborde ne se voit pas doublée ;
      coalesce=True    plusieurs déclenchements manqués — machine en veille,
                       conteneur arrêté — donnent UNE exécution de rattrapage,
                       pas une par créneau perdu ;
      grâce d'une heure un démarrage tardif rattrape le créneau du jour au lieu
                       de l'abandonner.
    Le verrou de fichier reste la garantie ultime : il vaut aussi entre le
    planificateur et une commande lancée à la main.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    planification = args.cron or settings.planification
    ordonnanceur = BlockingScheduler(timezone=FUSEAU)
    declenchement = CronTrigger.from_crontab(planification, timezone=FUSEAU)

    ordonnanceur.add_job(lambda: cmd_run(settings, args, log), declenchement,
                         id="pipeline", name="pipeline EDS",
                         max_instances=1, coalesce=True, misfire_grace_time=3600)

    for signal_arret in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_arret, lambda *_: ordonnanceur.shutdown(wait=False))

    log.info("planificateur démarré",
             extra={"cron": planification, "fuseau": FUSEAU,
                    "prochain": str(declenchement.get_next_fire_time(None, datetime.now(ZoneInfo(FUSEAU))))})
    if args.immediat:
        log.info("exécution immédiate avant la mise en attente")
        cmd_run(settings, args, log)

    try:
        ordonnanceur.start()
    except (KeyboardInterrupt, SystemExit):
        # On journalise l'arrêt, puis on laisse l'interruption suivre son cours :
        # l'avaler ferait croire à une fin normale alors que l'opérateur a
        # demandé l'arrêt.
        log.info("planificateur arrêté")
        raise
    return 0


# ─── Contrôles et état ──────────────────────────────────────────────────────

def cmd_acces(settings, args, log) -> int:
    """Crée les comptes cloisonnés puis éprouve le cloisonnement, aux deux niveaux."""
    client = sql.connect(settings)
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
    client = sql.connect(settings)

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

# Toutes les commandes reçoivent la même signature (settings, args, log) pour
# être appelables uniformément depuis cette table ; celles qui n'ont pas
# d'option n'utilisent pas `args`.
COMMANDES = {"init": cmd_init, "lake": cmd_lake, "bronze": cmd_bronze,
             "silver": cmd_silver, "gold": cmd_gold, "run": cmd_run,
             "acces": cmd_acces, "metabase": cmd_metabase,
             "scheduler": cmd_scheduler, "status": cmd_status}


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

    p_run = sub.add_parser("run", help="enchaîne lake → bronze → silver → gold")
    p_run.add_argument("--date", help="ne traiter que cette date de dépôt (AAAA-MM-JJ)")
    p_run.add_argument("--source", help="ne traiter que ce flux")
    p_run.add_argument("--force", action="store_true", help="réingérer même si déjà traité")
    p_run.add_argument("--attendre", action="store_true",
                       help="patienter si une autre exécution est en cours")

    p_sched = sub.add_parser("scheduler", help="déclenche le pipeline à intervalle régulier")
    p_sched.add_argument("--cron", help="planification au format cron (défaut : .env)")
    p_sched.add_argument("--immediat", action="store_true",
                         help="exécuter une fois au démarrage, avant la mise en attente")

    sub.add_parser("silver", help="reconstruit le modèle métier depuis bronze")
    sub.add_parser("gold", help="reconstruit les indicateurs, cloisonnés par usage")
    sub.add_parser("acces", help="crée les comptes cloisonnés et vérifie le cloisonnement")
    sub.add_parser("metabase", help="provisionne connexions et tableaux de bord")
    sub.add_parser("status", help="état des dépôts et des dernières exécutions")

    args = parser.parse_args(argv)
    # `run` délègue à des commandes qui lisent --date, --source et --force :
    # on garantit leur présence pour toutes.
    for option in ("date", "source", "force", "attendre", "cron", "immediat"):
        if not hasattr(args, option):
            setattr(args, option, None if option in ("date", "source", "cron") else False)

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
