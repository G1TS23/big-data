"""Réconciliation source ↔ entrepôt : aucune ligne ne disparaît sans être comptée.

Le compte de la source est refait ici à la main — csv, json, pyarrow — sans
importer une seule ligne du pipeline. Deux mesures indépendantes qui tombent
juste valent mieux qu'une mesure répétée deux fois.

Pour chaque table, l'équation vérifiée est :

    lignes source = lignes silver + rejets + doublons écartés

    python docs/outils/reconcilier.py
"""

import csv
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eds.config import load_settings  # noqa: E402  (le chemin doit précéder l'import)
from eds.sql import connect  # noqa: E402


def compter_source(racine: Path) -> dict[str, dict[str, int]]:
    """Relit les fichiers déposés par le CHU, sans le pipeline.

    Chaque flux a SON calendrier : le CHU dépose les séjours, diagnostics et
    relevés tous les jours, les référentiels une fois, et les patients par
    instantanés en fin de période. Parcourir les dates d'un flux pour en lire un
    autre ne lirait qu'une partie des dépôts, en silence.
    """

    def depots(flux: str) -> list[Path]:
        return sorted(p for p in (racine / flux).iterdir() if p.is_dir())

    def lignes_csv(chemin: Path) -> int:
        with chemin.open(encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))

    lignes_patients = 0
    identifiants: set[str] = set()
    for depot in depots("patients"):
        with (depot / "patients.csv").open(encoding="utf-8") as f:
            for ligne in csv.DictReader(f):
                lignes_patients += 1
                identifiants.add(ligne["patient_id"])

    lignes_sejours = sum(lignes_csv(d / "sejours.csv") for d in depots("sejours"))

    codes = 0
    for depot in depots("diagnostics"):
        objets = json.loads((depot / "diagnostics.json").read_text("utf-8"))
        codes += sum(len(o["diagnostics"]) for o in objets)

    releves = sum(pq.read_table(d / "monitoring.parquet").num_rows
                  for d in depots("monitoring"))

    referentiels = depots("referentiels")[-1]
    return {
        # « doublons » : ce que la déduplication doit légitimement retirer.
        "patients": {"source": lignes_patients,
                     "doublons": lignes_patients - len(identifiants)},
        "sejours": {"source": lignes_sejours, "doublons": 0},
        "diagnostics": {"source": codes, "doublons": 0},
        "monitoring": {"source": releves, "doublons": 0},
        "services": {"source": lignes_csv(referentiels / "services.csv"), "doublons": 0},
        "cim10": {"source": lignes_csv(referentiels / "cim10.csv"), "doublons": 0},
    }


# table source → (table bronze, expression de comptage, table silver, nom du fait)
CORRESPONDANCES = {
    "patients": ("bronze.patients", "count()", "silver.dim_patient", "dim_patient"),
    "sejours": ("bronze.sejours", "count()", "silver.fait_sejour", "fait_sejour"),
    # bronze conserve le JSON imbriqué : une ligne par séjour, N codes dedans.
    "diagnostics": ("bronze.diagnostics", "sum(length(diagnostics))",
                    "silver.fait_diagnostic", "fait_diagnostic"),
    "monitoring": ("bronze.monitoring", "count()", "silver.fait_monitoring", "fait_monitoring"),
    "services": ("bronze.services", "count()", "silver.dim_service", "dim_service"),
    "cim10": ("bronze.cim10", "count()", "silver.dim_cim10", "dim_cim10"),
}


def compter_entrepot(client) -> dict[str, dict[str, int]]:
    dernier = client.query("SELECT argMax(run_id, rejected_at) FROM ops.rejects").result_rows
    run = dernier[0][0] if dernier and dernier[0][0] else ""

    mesures = {}
    for nom, (bronze, expression, silver, fait) in CORRESPONDANCES.items():
        mesures[nom] = {
            "bronze": client.query(f"SELECT {expression} FROM {bronze}").result_rows[0][0],
            "silver": client.query(f"SELECT count() FROM {silver}").result_rows[0][0],
            "rejets": client.query(
                "SELECT count() FROM ops.rejects WHERE run_id = %(run)s AND table_source = %(t)s",
                parameters={"run": run, "t": fait}).result_rows[0][0],
        }
    return mesures


def main() -> int:
    reglages = load_settings()
    source = compter_source(Path(reglages.source_path))
    with connect(reglages) as client:
        entrepot = compter_entrepot(client)

    entete = f"{'table':<14}{'source':>9}{'bronze':>9}{'silver':>9}{'rejets':>9}{'doublons':>10}   "
    print(entete + "équation")
    print("─" * (len(entete) + 8))

    ecarts = 0
    for nom in CORRESPONDANCES:
        s, e = source[nom], entrepot[nom]
        somme = e["silver"] + e["rejets"] + s["doublons"]
        juste = somme == s["source"] and e["bronze"] == s["source"]
        ecarts += not juste
        verdict = "✓" if juste else f"✗ écart de {s['source'] - somme}"
        print(f"{nom:<14}{s['source']:>9}{e['bronze']:>9}{e['silver']:>9}"
              f"{e['rejets']:>9}{s['doublons']:>10}   {verdict}")

    print()
    if ecarts:
        print(f"{ecarts} table(s) ne se réconcilient pas.")
        return 1
    print("Toutes les tables se réconcilient : source = silver + rejets + doublons,")
    print("et bronze reproduit la source ligne pour ligne.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
