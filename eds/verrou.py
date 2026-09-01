"""Un seul pipeline à la fois.

Deux exécutions simultanées se disputeraient les mêmes fichiers temporaires et
les mêmes partitions. Le verrou repose sur flock : le noyau le libère à la mort
du processus, même tué par un signal — un incident ne peut donc pas laisser un
verrou coincé, ce qu'un simple fichier témoin ne garantirait pas.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class DejaEnCours(RuntimeError):
    """Une autre exécution détient le verrou."""


@contextmanager
def unique(chemin: Path, attendre: bool = False):
    """Garantit qu'une seule exécution traverse ce bloc à la fois.

    `attendre` fait patienter au lieu d'échouer : utile pour une exécution
    planifiée, qui préfère décaler son tour plutôt que sauter un cycle.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur = os.open(chemin, os.O_RDWR | os.O_CREAT, 0o644)
    mode = fcntl.LOCK_EX if attendre else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        try:
            fcntl.flock(descripteur, mode)
        except BlockingIOError as exc:
            detenteur = os.read(descripteur, 64).decode("utf-8", "replace").strip()
            raise DejaEnCours(
                f"une autre exécution est en cours (PID {detenteur or 'inconnu'})") from exc

        os.ftruncate(descripteur, 0)
        os.write(descripteur, str(os.getpid()).encode())
        os.fsync(descripteur)
        yield
    finally:
        os.close(descripteur)      # ferme et libère le verrou
