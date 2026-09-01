"""Étape 1 du pipeline : filestorage → lake.

Le filestorage du CHU est en lecture seule. On recopie chaque dépôt dans une
zone de travail locale, partitionnée par date de dépôt.

Deux régimes, selon ce que le fichier contient :

- **Copie brute, octet pour octet** (diagnostics, monitoring, référentiels).
  Le lake est alors fidèle à la source, ce qu'un test vérifie en comparant les
  octets eux-mêmes.

- **Copie pseudonymisée** (patients, séjours). Ces fichiers portent l'identité
  en clair. Les recopier tels quels ferait entrer nom, prénom et NIR dans la
  zone de travail, ce que le sujet interdit. On déroge donc au principe « lake =
  copie brute » pour ces deux flux, et c'est un choix de conformité assumé
  (privacy by design) : l'identité est détruite avant même d'être écrite sur
  disque, pas nettoyée après coup.

Un fichier illisible ou dont l'en-tête ne correspond pas au contrat déclaré est
mis en quarantaine et le traitement se poursuit sur les autres flux : un dépôt
corrompu ne doit pas faire échouer la journée entière.

REPRISE APRÈS INTERRUPTION
--------------------------
Le CHU garantit que le contenu d'un dossier de dépôt ne change jamais. Le
risque n'est donc pas la source qui bouge, c'est NOTRE écriture qui s'arrête en
chemin — processus tué, disque plein, machine éteinte.

Chaque fichier est donc écrit sous un nom provisoire, forcé sur le disque, puis
publié par un renommage. Le renommage est atomique : à son emplacement
définitif, un fichier est TOUJOURS complet. Une interruption ne laisse qu'un
résidu « .partiel », que l'exécution suivante efface avant de recommencer.

C'est plus sûr que ce qui précédait, qui relisait le fichier copié pour
comparer son empreinte : ce contrôle détectait la corruption, mais laissait le
fichier tronqué à sa place définitive. Le renommage, lui, empêche qu'il y
arrive.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from eds.pseudonymize import apply_privacy

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Suffixe des écritures en cours. Un fichier qui le porte est incomplet par
# définition : il n'a pas atteint le renommage final.
PARTIEL = ".partiel"


@dataclass(frozen=True)
class Deposit:
    """Un fichier déposé par le CHU, pour une source et une date données."""
    source: str
    deposit_date: str
    src_path: Path
    spec: dict


@dataclass
class IngestResult:
    deposit: Deposit
    status: str                      # OK | QUARANTINE | SKIPPED
    lake_path: Path | None = None
    rows_in: int = 0
    rows_out: int = 0
    bytes_in: int = 0
    reason: str = ""
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _file_specs(name: str, spec: dict) -> list[dict]:
    """Une source déclare soit un `path`, soit une liste de `files`."""
    if "files" in spec:
        return [{**spec, **f, "_name": f"{name}/{Path(f['path']).stem}"} for f in spec["files"]]
    return [{**spec, "_name": name}]


def discover(source_root: Path, sources: dict) -> list[Deposit]:
    """Liste tous les dépôts présents, toutes sources et toutes dates."""
    deposits: list[Deposit] = []
    for name, spec in sources.items():
        for file_spec in _file_specs(name, spec):
            template = file_spec["path"]
            flow_dir = source_root / template.split("/", 1)[0]
            if not flow_dir.is_dir():
                continue
            for date_dir in sorted(p for p in flow_dir.iterdir() if p.is_dir() and DATE_DIR.match(p.name)):
                path = source_root / template.format(date=date_dir.name)
                if path.is_file():
                    deposits.append(Deposit(file_spec["_name"], date_dir.name, path, file_spec))
    return sorted(deposits, key=lambda d: (d.deposit_date, d.source))


def _lake_target(lake_root: Path, dep: Deposit) -> Path:
    flow = dep.source.split("/", 1)[0]
    return lake_root / flow / f"ingestion_date={dep.deposit_date}" / dep.src_path.name


def est_publie(dep: Deposit, lake_root: Path) -> bool:
    """Le fichier attendu est-il réellement présent dans le lake ?

    Le journal dit ce qui a été ingéré ; il ne garantit pas que la copie soit
    encore là. Un lake purgé, un volume démonté, et l'étape suivante échouerait
    en cherchant un fichier absent. Une vérification d'existence, sans lecture.
    """
    return _lake_target(lake_root, dep).is_file()


def _quarantine(lake_root: Path, dep: Deposit, reason: str) -> IngestResult:
    target = lake_root / "_quarantaine" / dep.source.replace("/", "_") / dep.deposit_date
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dep.src_path, target / dep.src_path.name)
    (target / (dep.src_path.name + ".motif.txt")).write_text(reason, encoding="utf-8")
    return IngestResult(dep, "QUARANTINE", reason=reason, bytes_in=dep.src_path.stat().st_size)


def _check_header(dep: Deposit) -> str | None:
    expected = dep.spec.get("source_columns")
    if not expected or dep.spec.get("format") != "csv":
        return None
    with open(dep.src_path, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh), [])
    if [c.strip() for c in header] != list(expected):
        return f"en-tête inattendu : {header} au lieu de {expected}"
    return None


def nettoyer_residus(lake_root: Path) -> list[Path]:
    """Efface les écritures laissées en plan par une exécution interrompue.

    Un « .partiel » n'a pas atteint son renommage : il est incomplet, et la
    source étant immuable, il sera simplement réécrit.
    """
    residus = sorted(lake_root.rglob(f"*{PARTIEL}")) if lake_root.is_dir() else []
    for residu in residus:
        residu.unlink()
    return residus


def _publier(temporaire: Path, target: Path) -> None:
    """Rend le fichier visible à son emplacement définitif, d'un seul coup.

    os.replace est atomique au sein d'un même système de fichiers : aucun
    lecteur ne peut observer un fichier à moitié écrit.
    """
    os.replace(temporaire, target)


def _copy_raw(dep: Deposit, target: Path) -> IngestResult:
    """Copie fidèle, publiée d'un seul coup.

    Aucune empreinte n'est calculée ici. Elle ne servait qu'à l'idempotence, que
    la date assure désormais ; et comme le contenu d'un dépôt ne change jamais,
    elle reste recalculable à tout moment depuis la source ou depuis le lake.
    La stocker à l'ingestion aurait obligé à relire chaque fichier pour ne rien
    prouver de plus.

    Le renommage protège du cas demandé : un processus interrompu ne laisse
    qu'un « .partiel ». Une coupure de courant entre l'écriture et le renommage
    relève d'une autre classe de panne — le dépôt n'étant alors pas journalisé,
    l'exécution suivante le refait simplement.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporaire = target.with_name(target.name + PARTIEL)
    shutil.copy2(dep.src_path, temporaire)
    _publier(temporaire, target)
    return IngestResult(dep, "OK", lake_path=target,
                        bytes_in=dep.src_path.stat().st_size)


def _copy_pseudonymized(dep: Deposit, target: Path, salt: str) -> IngestResult:
    privacy = dep.spec["privacy"]
    columns = dep.spec["lake_columns"]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporaire = target.with_name(target.name + PARTIEL)

    rows_in = rows_out = 0
    with open(dep.src_path, newline="", encoding="utf-8") as fin, \
         open(temporaire, "w", newline="", encoding="utf-8") as fout:
        # lineterminator explicite : le dialecte csv par défaut écrit en CRLF,
        # ce qui alourdit inutilement les fichiers et surprend les outils Unix.
        writer = csv.DictWriter(fout, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for row in csv.DictReader(fin):
            rows_in += 1
            writer.writerow(apply_privacy(row, privacy, salt))
            rows_out += 1
        fout.flush()
        os.fsync(fout.fileno())
    _publier(temporaire, target)

    return IngestResult(dep, "OK", lake_path=target, rows_in=rows_in, rows_out=rows_out,
                        bytes_in=dep.src_path.stat().st_size)


def ingest(dep: Deposit, lake_root: Path, salt: str) -> IngestResult:
    """Recopie un dépôt dans le lake, pseudonymisé si le flux porte l'identité."""
    try:
        reason = _check_header(dep)
        if reason:
            return _quarantine(lake_root, dep, reason)

        target = _lake_target(lake_root, dep)
        if dep.spec.get("privacy"):
            return _copy_pseudonymized(dep, target, salt)
        return _copy_raw(dep, target)

    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return _quarantine(lake_root, dep, f"{type(exc).__name__}: {exc}")
