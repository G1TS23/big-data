"""Contrôles de bout en bout des commandes.

Le reste de la suite éprouve des fonctions pures. Ces contrôles-ci exécutent
les commandes elles-mêmes : c'est le seul endroit où le CÂBLAGE est vérifié.

Ils comblent une lacune réelle — un remaniement a introduit une variable locale
qui masquait un module, et les cent trois tests d'alors sont tous restés verts
parce qu'aucun n'appelait la commande.
"""
import argparse

import pytest

from eds import sql
from eds.cli import COMMANDES
from eds.config import load_settings
from eds.execution import journaliser
from eds.config import ROOT


@pytest.fixture(scope="module")
def contexte():
    settings = load_settings()
    try:
        sql.connect(settings, connect_timeout=2, send_receive_timeout=5).command("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse indisponible : {exc}")
    return settings, journaliser(ROOT / "logs")


def executer(contexte, commande, **options):
    settings, log = contexte
    args = argparse.Namespace(command=commande, date=None, source=None, force=False)
    for nom, valeur in options.items():
        setattr(args, nom, valeur)
    return COMMANDES[commande](settings, args, log)


class TestCommandes:
    """Chaque commande doit au moins s'exécuter sans erreur de câblage."""

    @pytest.mark.parametrize("commande", ["init", "lake", "bronze", "silver", "gold", "status"])
    def test_la_commande_aboutit(self, contexte, commande):
        assert executer(contexte, commande) == 0

    def test_le_rejeu_du_lake_n_ingere_rien_de_neuf(self, contexte):
        """Deuxième passage : tout est déjà là, donc tout est ignoré."""
        settings, _ = contexte
        client = sql.connect(settings)
        executer(contexte, "lake")
        executer(contexte, "lake")
        traites = client.query(
            "SELECT objets_traites FROM ops.run_log FINAL WHERE command = 'lake' "
            "ORDER BY started_at DESC LIMIT 1").result_rows[0][0]
        assert traites == 0

    def test_une_journee_ciblee_ne_touche_pas_les_autres(self, contexte):
        settings, _ = contexte
        client = sql.connect(settings)
        avant = client.query("SELECT count() FROM bronze.sejours").result_rows[0][0]
        assert executer(contexte, "bronze", date="2026-08-27") == 0
        assert client.query("SELECT count() FROM bronze.sejours").result_rows[0][0] == avant


class TestJournalDesExecutions:
    def test_chaque_commande_laisse_une_trace_close(self, contexte):
        settings, _ = contexte
        client = sql.connect(settings)
        executer(contexte, "silver")
        run = client.query(
            "SELECT toString(status), toString(unite), finished_at IS NOT NULL "
            "FROM ops.run_log FINAL WHERE command = 'silver' "
            "ORDER BY started_at DESC LIMIT 1").result_rows[0]
        assert run[0] == "OK"
        assert run[1] == "instruction"
        assert run[2], "exécution restée ouverte"

    def test_aucune_execution_ne_reste_ouverte(self, contexte):
        settings, _ = contexte
        client = sql.connect(settings)
        assert client.query(
            "SELECT count() FROM ops.run_log FINAL WHERE status = 'RUNNING'"
        ).result_rows[0][0] == 0
