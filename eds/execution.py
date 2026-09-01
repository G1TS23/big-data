"""Ce qu'est une exécution de l'orchestrateur : sa journalisation et sa trace.

Les cinq commandes du pipeline partageaient le même squelette — ouvrir une
ligne de journal, tenir quatre compteurs, envelopper le travail dans un
try/except/finally, clôturer quoi qu'il arrive. Cent vingt-cinq lignes de
redite, qui noyaient la logique métier de chaque commande.

Tout cela vit ici. Une commande se réduit désormais à ce qu'elle fait vraiment :

    with Execution(settings, "lake", "dépôt", log) as run:
        for depot in depots:
            with run.etape(f"{depot.source}/{depot.date}"):
                ...
                run.traites += 1
    return run.code_retour
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from eds import sql
from eds.config import Settings

_RESERVE = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

_COLONNES = ["run_id", "command", "started_at", "finished_at", "status",
             "unite", "objets_vus", "objets_traites", "objets_ignores",
             "objets_quarantaine", "message", "updated_at"]


# ─── Journalisation ─────────────────────────────────────────────────────────

class FormatJson(logging.Formatter):
    """Le fichier de journal, lisible par une machine."""

    def format(self, record: logging.LogRecord) -> str:
        charge = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                  "level": record.levelname, "logger": record.name,
                  "message": record.getMessage()}
        charge.update({k: v for k, v in record.__dict__.items() if k not in _RESERVE})
        if record.exc_info:
            charge["exception"] = self.formatException(record.exc_info)
        return json.dumps(charge, ensure_ascii=False, default=str)


class FormatConsole(logging.Formatter):
    """La console, lisible par un humain."""

    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVE}
        detail = "  " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
        return f"{record.levelname:<7} {record.getMessage()}{detail}"


def journaliser(dossier: Path, niveau: int = logging.INFO) -> logging.Logger:
    dossier.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("eds")
    logger.setLevel(niveau)
    logger.handlers.clear()
    logger.propagate = False

    fichier = logging.FileHandler(dossier / f"eds-{date.today():%Y-%m-%d}.log", encoding="utf-8")
    fichier.setFormatter(FormatJson())
    logger.addHandler(fichier)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(FormatConsole())
    logger.addHandler(console)
    return logger


# ─── L'exécution ────────────────────────────────────────────────────────────

class Execution:
    """Une exécution journalisée, du premier au dernier instant.

    La ligne de journal est ouverte à l'entrée et clôturée à la sortie, y
    compris en cas d'incident : une exécution ne doit jamais rester
    éternellement « RUNNING ».
    """

    def __init__(self, settings: Settings, commande: str, unite: str, log: logging.Logger):
        self.settings, self.commande, self.unite, self.log = settings, commande, unite, log
        self.run_id = uuid.uuid4().hex
        self.vus = self.traites = self.ignores = self.quarantaine = 0
        self.incidents: list[str] = []
        self._a_journaliser: dict[str, tuple[list[str], list[list]]] = {}
        self._fatal = False

    # ── cycle de vie ────────────────────────────────────────────────────────
    def __enter__(self) -> Execution:
        self.client = sql.connect(self.settings)
        self.debut = datetime.now()
        self._ecrire_journal("RUNNING", "")
        return self

    def __exit__(self, type_exc, exc, trace) -> bool:
        if exc is not None:
            self._fatal = True
            self.incidents.append(f"erreur fatale : {exc}")
        self._vider_journaux()
        try:
            self._ecrire_journal(self.statut, " | ".join(self.incidents)[:2000])
        except Exception:                          # noqa: BLE001
            self.log.error("exécution non clôturée", exc_info=True,
                           extra={"run_id": self.run_id})
        self.log.info("run terminé", extra={"run_id": self.run_id, "statut": self.statut,
                                            "vus": self.vus, "traites": self.traites,
                                            "ignores": self.ignores,
                                            "quarantaine": self.quarantaine})
        return False                               # l'exception poursuit sa route

    # ── état ────────────────────────────────────────────────────────────────
    @property
    def statut(self) -> str:
        if self._fatal or (self.incidents and not self.traites):
            return "FAILED"
        if self.incidents or self.quarantaine:
            return "PARTIAL"
        return "OK"

    @property
    def code_retour(self) -> int:
        return 1 if self.statut == "FAILED" else 0

    # ── travail ─────────────────────────────────────────────────────────────
    @contextmanager
    def etape(self, quoi: str):
        """Isole le traitement d'un élément : son échec n'arrête pas les autres.

        Un dépôt corrompu ne doit pas faire échouer la journée entière.
        """
        try:
            yield
        except Exception as exc:                   # noqa: BLE001
            self.incidents.append(f"{quoi} : {exc}")
            self.log.error("échec", exc_info=True, extra={"element": quoi})

    def journaliser(self, table: str, colonnes: list[str], ligne: list) -> None:
        """Accumule une ligne de journal métier, écrite à la clôture."""
        self._a_journaliser.setdefault(table, (colonnes, []))[1].append(ligne)

    # ── écritures ───────────────────────────────────────────────────────────
    def _ecrire_journal(self, statut: str, message: str) -> None:
        self.client.insert(
            "ops.run_log",
            [[self.run_id, self.commande, self.debut,
              None if statut == "RUNNING" else datetime.now(), statut, self.unite,
              self.vus, self.traites, self.ignores, self.quarantaine,
              message, datetime.now()]],
            column_names=_COLONNES)

    def _vider_journaux(self) -> None:
        for table, (colonnes, lignes) in self._a_journaliser.items():
            if not lignes:
                continue
            try:
                self.client.insert(table, lignes, column_names=colonnes)
            except Exception:                      # noqa: BLE001
                self.log.error("journal métier non écrit", exc_info=True,
                               extra={"table": table, "run_id": self.run_id})
                self.incidents.append(f"{table} non écrit")
