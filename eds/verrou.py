"""Un seul pipeline à la fois.

Deux exécutions simultanées se disputeraient les mêmes fichiers temporaires et
les mêmes partitions. Le verrou repose sur un verrou de FICHIER, tenu par le
noyau : il est libéré à la mort du processus, même tué par un signal — un
incident ne peut donc pas laisser un verrou coincé, ce qu'un simple fichier
témoin ne garantirait pas.

Deux implémentations, une par système :

  POSIX     flock sur le descripteur. Verrou consultatif : le fichier reste
            lisible par les autres, qui peuvent y lire le PID du détenteur.

  Windows   msvcrt.locking, qui n'existe qu'en verrou IMPÉRATIF — la zone
            verrouillée devient illisible pour les autres processus. On
            verrouille donc un octet situé APRÈS le contenu, à un décalage que
            personne ne lit : le PID, écrit au début, reste accessible.
            Verrouiller au-delà de la fin du fichier est permis par Windows.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

WINDOWS = sys.platform == "win32"

# Décalage de l'octet verrouillé sous Windows, hors de portée du contenu.
_OCTET_VERROU = 1024
# Intervalle de reprise quand on accepte d'attendre (Windows n'a pas de mode
# bloquant : LK_LOCK abandonne au bout de dix secondes).
_ATTENTE = 0.25
# Le PID est écrit sur une largeur FIXE, complété d'espaces : sans troncature,
# aucun reliquat d'un PID plus long ne subsiste, et l'on évite un ftruncate
# sous une zone verrouillée — dont Windows ne garantit rien.
_LARGEUR_PID = 32
# Sous Windows, un descripteur ouvert sans O_BINARY traduit les fins de ligne.
_BINAIRE = getattr(os, "O_BINARY", 0)

if WINDOWS:
    import msvcrt

    def _prendre(descripteur: int, attendre: bool) -> None:
        os.lseek(descripteur, _OCTET_VERROU, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(descripteur, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if not attendre:
                    raise
                time.sleep(_ATTENTE)
                os.lseek(descripteur, _OCTET_VERROU, os.SEEK_SET)

    def _rendre(descripteur: int) -> None:
        # Fermer suffirait, mais un déverrouillage explicite laisse le fichier
        # dans un état propre si le descripteur devait resservir.
        try:
            os.lseek(descripteur, _OCTET_VERROU, os.SEEK_SET)
            msvcrt.locking(descripteur, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _prendre(descripteur: int, attendre: bool) -> None:
        mode = fcntl.LOCK_EX if attendre else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(descripteur, mode)

    def _rendre(descripteur: int) -> None:
        pass                            # la fermeture libère le verrou


class DejaEnCours(RuntimeError):
    """Une autre exécution détient le verrou."""


def _detenteur(chemin: Path) -> str:
    """Le PID inscrit dans le fichier, ou « inconnu » s'il est illisible."""
    try:
        return chemin.read_text(encoding="utf-8", errors="replace").strip() or "inconnu"
    except OSError:
        return "inconnu"


@contextmanager
def unique(chemin: Path, attendre: bool = False):
    """Garantit qu'une seule exécution traverse ce bloc à la fois.

    `attendre` fait patienter au lieu d'échouer : utile pour une exécution
    planifiée, qui préfère décaler son tour plutôt que sauter un cycle.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur = os.open(chemin, os.O_RDWR | os.O_CREAT | _BINAIRE, 0o644)
    try:
        try:
            _prendre(descripteur, attendre)
        except OSError as exc:
            raise DejaEnCours(
                f"une autre exécution est en cours (PID {_detenteur(chemin)})") from exc

        os.lseek(descripteur, 0, os.SEEK_SET)
        os.write(descripteur, str(os.getpid()).ljust(_LARGEUR_PID).encode())
        os.fsync(descripteur)
        yield
    finally:
        _rendre(descripteur)
        os.close(descripteur)      # ferme et libère le verrou
