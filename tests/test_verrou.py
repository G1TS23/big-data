"""Contrôles du verrou d'exclusion.

Deux exécutions simultanées se disputeraient les mêmes fichiers temporaires et
les mêmes partitions. Le point délicat n'est pas d'empêcher la seconde : c'est
de garantir qu'un incident ne laisse pas un verrou coincé.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from eds.verrou import DejaEnCours, unique

RACINE = Path(__file__).resolve().parent.parent


def _detenteur(chemin: Path, duree: float) -> subprocess.Popen:
    """Lance un processus qui garde le verrou pendant `duree` secondes."""
    return subprocess.Popen([sys.executable, "-c", (
        f'import sys, time; sys.path.insert(0, {str(RACINE)!r})\n'
        f'from pathlib import Path\n'
        f'from eds.verrou import unique\n'
        f'with unique(Path({str(chemin)!r})):\n'
        f'    time.sleep({duree})\n')])


class TestExclusion:
    def test_une_seule_execution_a_la_fois(self, tmp_path):
        chemin = tmp_path / "eds.lock"
        detenteur = _detenteur(chemin, 3)
        time.sleep(0.7)
        try:
            with pytest.raises(DejaEnCours):
                with unique(chemin):
                    pass
        finally:
            detenteur.kill()
            detenteur.wait()

    def test_le_verrou_est_rendu_a_la_fin(self, tmp_path):
        chemin = tmp_path / "eds.lock"
        with unique(chemin):
            pass
        with unique(chemin):
            pass                            # doit s'obtenir sans attendre

    def test_un_processus_tue_ne_coince_pas_le_verrou(self, tmp_path):
        """Le cas qui compte. Un simple fichier témoin resterait en place après
        un kill -9 et bloquerait toutes les exécutions suivantes ; flock est
        libéré par le noyau."""
        chemin = tmp_path / "eds.lock"
        detenteur = _detenteur(chemin, 30)
        time.sleep(0.7)
        detenteur.kill()
        detenteur.wait()
        with unique(chemin):
            pass

    def test_le_verrou_nomme_le_detenteur(self, tmp_path):
        chemin = tmp_path / "eds.lock"
        with unique(chemin):
            assert chemin.read_text().strip() == str(os.getpid())

    def test_le_dossier_est_cree_au_besoin(self, tmp_path):
        chemin = tmp_path / "jamais" / "cree" / "eds.lock"
        with unique(chemin):
            assert chemin.is_file()
