"""Contrôles de bout en bout des commandes.

Le reste de la suite éprouve des fonctions pures. Ces contrôles-ci exécutent
les commandes elles-mêmes : c'est le seul endroit où le CÂBLAGE est vérifié.

Ils comblent une lacune réelle — un remaniement a introduit une variable locale
qui masquait un module, et les cent trois tests d'alors sont tous restés verts
parce qu'aucun n'appelait la commande.
"""
import argparse
import contextlib

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
    # Mêmes valeurs par défaut que la ligne de commande : `run` délègue à des
    # commandes qui lisent ces options.
    args = argparse.Namespace(command=commande, date=None, source=None,
                              force=False, attendre=False, cron=None, immediat=False)
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


class TestChaine:
    def test_la_chaine_complete_aboutit(self, contexte):
        assert executer(contexte, "run") == 0

    def test_les_quatre_etapes_sont_journalisees(self, contexte):
        from eds.cli import CHAINE
        settings, _ = contexte
        client = sql.connect(settings)
        executer(contexte, "run")
        recentes = {ligne[0] for ligne in client.query(
            "SELECT command FROM ops.run_log FINAL ORDER BY started_at DESC LIMIT 4"
        ).result_rows}
        assert recentes == set(CHAINE)

    def test_une_etape_en_echec_arrete_la_chaine(self, contexte, monkeypatch):
        """Poursuivre produirait des indicateurs calculés sur des données
        incomplètes — pire qu'une absence d'indicateurs, parce que rien ne les
        distinguerait des bons."""
        from eds import cli
        appelees = []

        def espion(nom, code):
            def commande(settings, args, log):
                appelees.append(nom)
                return code
            return commande

        monkeypatch.setitem(cli.COMMANDES, "lake", espion("lake", 0))
        monkeypatch.setitem(cli.COMMANDES, "bronze", espion("bronze", 1))
        monkeypatch.setitem(cli.COMMANDES, "silver", espion("silver", 0))
        monkeypatch.setitem(cli.COMMANDES, "gold", espion("gold", 0))

        settings, log = contexte
        import argparse
        args = argparse.Namespace(date=None, source=None, force=False, attendre=False)
        assert cli.cmd_run(settings, args, log) == 1
        assert appelees == ["lake", "bronze"], "la chaîne a continué après un échec"

    def test_une_execution_concurrente_est_ignoree_sans_erreur(self, contexte, monkeypatch):
        """Un planificateur qui trouve le verrou pris doit passer son tour, pas
        alerter : ce n'est pas une panne."""
        from eds import cli, verrou

        @contextlib.contextmanager
        def occupe(*a, **kw):
            raise verrou.DejaEnCours("une autre exécution est en cours (PID 123)")
            yield

        monkeypatch.setattr(verrou, "unique", occupe)
        settings, log = contexte
        import argparse
        args = argparse.Namespace(date=None, source=None, force=False, attendre=False)
        assert cli.cmd_run(settings, args, log) == 0


class TestJournalPortable:
    def test_les_chemins_sont_relatifs(self, contexte):
        """Un chemin absolu enfermerait le journal dans la machine qui l'a
        écrit : le planificateur en conteneur, dont le lake est ailleurs, ne
        retrouverait rien."""
        settings, _ = contexte
        client = sql.connect(settings)
        executer(contexte, "lake", force=True)
        for src, lake in client.query(
                "SELECT src_path, lake_path FROM ops.ingestion_log "
                "WHERE status = 'OK' LIMIT 5").result_rows:
            assert not src.startswith("/"), f"chemin source absolu : {src}"
            assert not lake.startswith("/"), f"chemin lake absolu : {lake}"


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
