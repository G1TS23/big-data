"""Contrôles de l'évolution : description des services et actes médicaux.

Le sujet signale deux pièges. Ces tests les gardent, parce qu'aucun des deux ne
lève d'erreur quand on tombe dedans — ils produisent seulement des chiffres faux.
"""
import pytest

from eds.config import load_settings


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def ch(settings):
    from eds import sql
    try:
        client = sql.connect(settings, connect_timeout=2, send_receive_timeout=5)
        client.command("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse indisponible : {exc}")
    if not client.query("SELECT count() FROM system.tables "
                        "WHERE database = 'silver' AND name = 'fait_acte'").result_rows[0][0]:
        pytest.skip("fait_acte absent — lancer `eds run`")
    return client


def scalar(client, sql, **params):
    return client.query(sql, parameters=params).result_rows[0][0]


class TestReferentielIncomplet:
    """Piège 1 — le référentiel de description ne couvre pas tous les services.

    Un INNER JOIN ferait disparaître le service non décrit de tous les
    indicateurs par catégorie, sans un mot : 1 208 séjours et 1 471 actes
    s'évaporeraient et le total resterait « cohérent » avec lui-même.
    """

    def test_tous_les_services_sont_dans_la_dimension(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.dim_service") \
            == scalar(ch, "SELECT uniqExact(service_code) FROM bronze.services")

    def test_un_service_non_decrit_reste_visible(self, ch):
        """Il doit rester dans la dimension, marqué, et non disparaître."""
        non_decrits = scalar(ch, "SELECT countIf(est_decrit = 0) FROM silver.dim_service")
        assert non_decrits > 0, "sans service non décrit, ce garde-fou passe à vide"
        assert scalar(ch, "SELECT countIf(est_decrit = 0 AND categorie != 'non décrit') "
                          "FROM silver.dim_service") == 0

    def test_la_capacite_inconnue_est_nulle_et_non_zero(self, ch):
        """Zéro lit se diviserait ; un nombre inconnu se laisse en blanc."""
        assert scalar(ch, "SELECT countIf(est_decrit = 0 AND capacite_lits IS NOT NULL) "
                          "FROM silver.dim_service") == 0
        assert scalar(ch, "SELECT countIf(capacite_lits = 0) FROM silver.dim_service") == 0

    def test_aucune_densite_infinie(self, ch):
        assert scalar(ch, "SELECT countIf(isInfinite(actes_par_lit)) "
                          "FROM gold_pilotage.kpi_actes_service") == 0

    def test_les_sejours_du_service_non_decrit_restent_comptes(self, ch):
        """La somme par catégorie doit couvrir TOUS les séjours."""
        assert scalar(ch, "SELECT sum(sejours) FROM gold_pilotage.kpi_activite_categorie") \
            == scalar(ch, "SELECT count() FROM silver.fait_sejour")

    def test_le_service_non_decrit_est_signale(self, ch):
        run = scalar(ch, "SELECT argMax(run_id, mesure_at) FROM ops.data_quality")
        assert scalar(ch, "SELECT lignes_concernees FROM ops.data_quality "
                          "WHERE run_id = {r:String} AND regle = 'service_sans_description'",
                      r=run) == scalar(ch, "SELECT countIf(est_decrit = 0) "
                                           "FROM silver.dim_service")


class TestServicePorteParLeSejour:
    """Piège 2 — le service vient du séjour, pas de l'acte.

    Joindre fait_acte à fait_sejour multiplierait chaque séjour par son nombre
    d'actes. Le service est donc dénormalisé sur fait_acte à la construction.
    """

    def test_le_service_de_l_acte_est_celui_du_sejour(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_acte AS a "
                          "INNER JOIN silver.sejour_recevable AS s ON s.stay_id = a.stay_id "
                          "WHERE a.service_code != s.service_code") == 0

    def test_le_compte_des_sejours_n_est_pas_gonfle_par_les_actes(self, ch):
        """Le test qui attrape la jointure entre deux faits : avec elle, la somme
        des séjours vaudrait le nombre d'actes, non le nombre de séjours."""
        assert scalar(ch, "SELECT sum(sejours) FROM gold_pilotage.kpi_actes_service") \
            == scalar(ch, "SELECT count() FROM silver.fait_sejour")

    def test_tout_service_de_la_dimension_a_sa_ligne(self, ch):
        assert scalar(ch, "SELECT count() FROM gold_pilotage.kpi_actes_service") \
            == scalar(ch, "SELECT count() FROM silver.dim_service")


class TestActes:
    def test_tous_les_actes_sont_retenus(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_acte") \
            == scalar(ch, "SELECT count() FROM bronze.actes")

    def test_tout_acte_pointe_un_code_connu(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_acte "
                          "WHERE code_ccam NOT IN (SELECT code_ccam FROM silver.dim_ccam)") == 0

    def test_les_actes_orphelins_sont_comptes(self, ch):
        run = scalar(ch, "SELECT argMax(run_id, mesure_at) FROM ops.data_quality")
        orphelins = scalar(ch, "SELECT countIf(stay_id NOT IN "
                               "(SELECT stay_id FROM silver.fait_sejour)) FROM silver.fait_acte")
        assert orphelins > 0, "sans orphelin, le contrôle passerait à vide"
        assert scalar(ch, "SELECT lignes_concernees FROM ops.data_quality "
                          "WHERE run_id = {r:String} AND regle = 'acte_sans_sejour_retenu'",
                      r=run) == orphelins

    def test_le_montant_t2a_concorde_entre_les_deux_grains(self, ch):
        """Service et type d'acte découpent les mêmes actes : les deux totaux
        doivent tomber sur le même euro."""
        assert scalar(ch, "SELECT sum(montant_t2a) FROM gold_pilotage.kpi_actes_service") \
            == scalar(ch, "SELECT sum(montant_t2a) FROM gold_pilotage.kpi_actes_type")

    def test_les_actes_se_comptent_pareil_aux_deux_grains(self, ch):
        assert scalar(ch, "SELECT sum(actes) FROM gold_pilotage.kpi_actes_service") \
            == scalar(ch, "SELECT sum(actes) FROM gold_pilotage.kpi_actes_type")


class TestNonRegression:
    """Le sujet l'exige : les indicateurs existants ne doivent pas bouger."""

    def test_les_kpi_historiques_sont_inchanges(self, ch):
        assert ch.query("SELECT sejours, patients, sejours_en_cours, dms_jours, "
                        "taux_readmission_30j, releves_en_alerte "
                        "FROM gold_pilotage.kpi_synthese").result_rows[0] \
            == (6729, 5949, 683, 5.15, 0.1289, 3314)
