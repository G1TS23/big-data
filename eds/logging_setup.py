"""Journalisation structurée.

Deux destinations, deux usages : le fichier JSON pour l'analyse d'incident,
la console pour le suivi d'exécution. Les tables ops.* prennent le relais pour
le pilotage et les tableaux de bord d'exploitation.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        detail = "  " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
        return f"{record.levelname:<7} {record.getMessage()}{detail}"


def setup(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("eds")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_dir / f"eds-{date.today():%Y-%m-%d}.log", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    return logger
