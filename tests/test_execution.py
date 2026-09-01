"""Contrôles du cycle de vie d'une exécution.

Le point critique n'est pas le cas nominal : c'est qu'un incident ne laisse
jamais une exécution éternellement « RUNNING » dans le journal.
"""
import pytest

from eds import sql
from eds.config import load_settings
from eds.execution import Execution


@pytest.fixture(scope="module")
def settings():
    reglages = load_settings()
    try:
        sql.connect(reglages, connect_timeout=2, send_receive_timeout=5).command("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse indisponible : {exc}")
    return reglages


def dernier_run(client, run_id):
    return client.query(
        "SELECT toString(status), finished_at IS NOT NULL, message "
        "FROM ops.run_log FINAL WHERE run_id = {r:String}",
        parameters={"r": run_id}).result_rows[0]


class TestCloture:
    def test_une_execution_reussie_est_close_en_ok(self, settings):
        with Execution(settings, "test", "dépôt") as run:
            run.traites = 3
            identifiant = run.run_id
        statut, close, _ = dernier_run(run.client, identifiant)
        assert statut == "OK" and close

    def test_une_exception_ne_laisse_pas_l_execution_ouverte(self, settings):
        """Sans cela, un incident laisserait une ligne RUNNING pour toujours,
        et `eds status` la signalerait indéfiniment."""
        with pytest.raises(ValueError):
            with Execution(settings, "test", "dépôt") as run:
                identifiant = run.run_id
                raise ValueError("incident simulé")
        statut, close, message = dernier_run(run.client, identifiant)
        assert statut == "FAILED" and close
        assert "incident simulé" in message

    def test_l_exception_poursuit_sa_route(self, settings):
        """Le gestionnaire journalise, il n'avale pas."""
        with pytest.raises(RuntimeError, match="doit remonter"):
            with Execution(settings, "test", "dépôt"):
                raise RuntimeError("doit remonter")


class TestStatut:
    def test_un_incident_isole_donne_partiel(self, settings):
        with Execution(settings, "test", "dépôt") as run:
            run.traites = 2
            with run.etape("flux en échec"):
                raise ValueError("un dépôt sur trois")
            assert run.statut == "PARTIAL"

    def test_une_etape_en_echec_n_arrete_pas_les_suivantes(self, settings):
        """Un dépôt corrompu ne doit pas faire échouer la journée entière."""
        traites = []
        with Execution(settings, "test", "dépôt") as run:
            for numero in range(3):
                with run.etape(f"dépôt {numero}"):
                    if numero == 1:
                        raise ValueError("corrompu")
                    traites.append(numero)
        assert traites == [0, 2]

    def test_aucun_element_traite_donne_echec(self, settings):
        with Execution(settings, "test", "dépôt") as run:
            with run.etape("le seul dépôt"):
                raise ValueError("corrompu")
            assert run.statut == "FAILED"
            assert run.code_retour == 1

    def test_une_mise_en_quarantaine_donne_partiel(self, settings):
        with Execution(settings, "test", "dépôt") as run:
            run.traites, run.quarantaine = 5, 1
            assert run.statut == "PARTIAL"
            assert run.code_retour == 0      # partiel n'est pas un échec
