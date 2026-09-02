"""Contrôles de la couche gold et du cloisonnement.

Ce module éprouve ce que le sujet réclame de démontrer : deux usages qui ne
voient pas les mêmes données, et un seuil de diffusion qu'aucune requête ne
peut lever.
"""
import pytest

from eds.access import COMPTES, INTERDITS, _peut_lire
from eds.config import load_settings
from eds.sql import load_regles


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
                        "WHERE database = 'gold_pilotage'").result_rows[0][0]:
        pytest.skip("gold absent — lancer `eds gold && eds acces`")
    return client


def scalar(client, sql, **params):
    return client.query(sql, parameters=params).result_rows[0][0]


class TestIndicateursDePilotage:
    def test_dms_calculee_sur_les_sejours_clos(self, ch):
        """Inclure un séjour en cours tronquerait sa durée et tirerait la DMS
        vers le bas."""
        clos = scalar(ch, "SELECT countIf(est_en_cours = 0) FROM silver.fait_sejour")
        assert scalar(ch, "SELECT sum(sejours_clos) FROM gold_pilotage.kpi_dms_service") == clos

    def test_dms_coherente_avec_silver(self, ch):
        attendu = scalar(ch, "SELECT round(avgIf(duree_jours, est_en_cours = 0), 2) "
                             "FROM silver.fait_sejour")
        assert abs(scalar(ch, "SELECT dms_jours FROM gold_pilotage.kpi_synthese") - attendu) < 0.01

    def test_readmission_rattachee_au_service_de_sortie(self, ch):
        """La qualité des soins se juge sur le service qui laisse sortir le
        patient, pas sur celui qui le récupère."""
        total = scalar(ch, "SELECT sum(readmissions) FROM gold_pilotage.kpi_readmission_service")
        assert total == scalar(ch, "SELECT countIf(est_readmission_30j = 1) FROM silver.fait_sejour")

    def test_denominateur_exclut_ce_qui_interdit_un_retour(self, ch):
        """Un patient décédé, muté ou transféré ne peut pas « revenir »."""
        assert scalar(ch, "SELECT sum(sejours_eligibles) FROM gold_pilotage.kpi_readmission_service") \
            == scalar(ch, "SELECT countIf(est_en_cours = 0 AND discharge_mode "
                          "NOT IN ('deces','mutation','transfert')) FROM silver.fait_sejour")

    def test_taux_de_readmission_plausible(self, ch):
        taux = scalar(ch, "SELECT taux_readmission_30j FROM gold_pilotage.kpi_synthese")
        assert 0.01 < taux < 0.25, f"taux invraisemblable : {taux:.1%}"

    def test_occupation_couvre_toute_la_periode(self, ch):
        """Un séjour compte chaque jour où le patient est présent, admission
        comprise, jusqu'à sa sortie ou l'horizon d'observation.

        La borne haute est le dernier dépôt, pas la date du jour : au-delà, les
        admissions ne sont plus connues. Voir TestHorizonDObservation."""
        jours = scalar(ch, "SELECT uniqExact(jour) FROM gold_pilotage.kpi_occupation_jour")
        attendu = scalar(ch, "SELECT dateDiff('day', min(toDate(s.admission_ts)), "
                             "(SELECT max(_ingestion_date) FROM bronze.sejours)) + 1 "
                             "FROM silver.fait_sejour AS s")
        assert jours == attendu

    def test_alertes_coherentes_avec_silver(self, ch):
        assert scalar(ch, "SELECT sum(releves_alerte) FROM gold_pilotage.kpi_alertes_jour") \
            == scalar(ch, "SELECT countIf(est_alerte = 1) FROM silver.fait_monitoring")

    def test_aucune_ligne_patient_dans_le_pilotage(self, ch):
        """Le pilotage ne voit que des agrégats : aucune colonne ne porte une
        clé patient ou un identifiant de séjour."""
        colonnes = ch.query(
            "SELECT name FROM system.columns WHERE database = 'gold_pilotage'").result_rows
        interdites = {"patient_key", "stay_id"}
        assert not (interdites & {c[0] for c in colonnes})


class TestSeuilDeDiffusion:
    def test_seuil_scelle_dans_la_definition_des_vues(self, ch):
        """Le seuil ne doit pas être un paramètre : ClickHouse permettrait
        alors d'appeler la vue avec k = 1."""
        k = load_regles()["k_anonymite"]
        vues = ch.query("SELECT name, create_table_query FROM system.tables "
                        "WHERE database = 'gold_recherche'").result_rows
        assert vues, "aucune vue de recherche"
        for name, ddl in vues:
            assert f">= {k}" in ddl, f"{name} n'applique pas le seuil"
            assert "SQL SECURITY DEFINER" in ddl, f"{name} s'exécute avec les droits de l'appelant"
            assert "{k:" not in ddl, f"{name} expose le seuil en paramètre"

    def test_la_description_de_cohorte_porte_sur_un_diagnostic(self, ch):
        """Le sujet demande « description de COHORTE : distribution par âge et
        sexe », après « taille des cohortes par diagnostic ». Une distribution
        tous diagnostics confondus ne décrirait aucune cohorte : la vue doit
        donc porter le code."""
        colonnes = {c for (c,) in ch.query(
            "SELECT name FROM system.columns WHERE database = 'gold_recherche' "
            "AND `table` = 'coh_pathologie_age_sexe'").result_rows}
        assert {"code_cim10", "tranche_age", "sex"} <= colonnes

    def test_aucune_vue_ne_distribue_l_age_hors_cohorte(self, ch):
        vues = {v for (v,) in ch.query(
            "SELECT name FROM system.tables WHERE database = 'gold_recherche'").result_rows}
        assert "coh_age_sexe" not in vues, "vue globale sans besoin correspondant"

    def test_aucun_alias_de_jointure_ne_fuit_dans_les_colonnes(self, ch):
        """Une colonne nommée « c.code_cim10 » obligerait le chercheur à
        l'entourer d'accents graves pour la filtrer. Une couche de restitution
        n'expose pas les alias de ses jointures."""
        fuites = ch.query("SELECT database, name FROM system.columns "
                          "WHERE database LIKE 'gold%' AND name LIKE '%.%'").result_rows
        assert not fuites, f"colonnes qualifiées exposées : {fuites}"

    def test_aucune_cohorte_sous_le_seuil_n_est_diffusee(self, ch):
        k = load_regles()["k_anonymite"]
        for vue in ("coh_prevalence", "coh_pathologie_age", "coh_pathologie_age_sexe",
                    "coh_duree_pathologie", "coh_comorbidites"):
            assert scalar(ch, f"SELECT countIf(patients < {k}) FROM gold_recherche.{vue}") == 0

    def test_la_clause_de_seuil_est_bien_active(self, ch):
        """Sur ce jeu de données aucune cohorte ne passe sous 5 : on éprouve
        donc la clause en la rejouant avec un seuil qui, lui, mord."""
        base = ("SELECT count() FROM (SELECT uniqExact(f.patient_key) AS n "
                "FROM silver.fait_diagnostic f GROUP BY f.code_cim10 HAVING n >= {s})")
        assert scalar(ch, base.format(s=5)) > scalar(ch, base.format(s=2800))


class TestCloisonnement:
    def test_chaque_compte_lit_sa_base(self, settings, ch):
        for compte, (_role, base, attribut) in COMPTES.items():
            objet = ch.query("SELECT name FROM system.tables WHERE database = {d:String} "
                             "ORDER BY name LIMIT 1", parameters={"d": base}).result_rows[0][0]
            autorise, motif = _peut_lire(settings, compte, getattr(settings, attribut),
                                         f"{base}.{objet}")
            assert autorise, f"{compte} ne peut pas lire {base}.{objet} : {motif}"

    def test_aucun_compte_ne_lit_la_base_de_l_autre(self, settings, ch):
        for compte, (_role, base, attribut) in COMPTES.items():
            autre = next(b for c, (_r, b, _a) in COMPTES.items() if c != compte)
            objet = ch.query("SELECT name FROM system.tables WHERE database = {d:String} "
                             "ORDER BY name LIMIT 1", parameters={"d": autre}).result_rows[0][0]
            autorise, _ = _peut_lire(settings, compte, getattr(settings, attribut),
                                     f"{autre}.{objet}")
            assert not autorise, f"{compte} accède à {autre}, le cloisonnement ne tient pas"

    def test_aucun_compte_n_atteint_les_couches_internes(self, settings, ch):
        """Ni le détail patient, ni les données brutes, ni les journaux d'un
        autre usage. L'exploitation a bien sûr accès aux siens : c'est sa base."""
        for compte, (_role, base, attribut) in COMPTES.items():
            for objet in INTERDITS:
                if objet.startswith(base + "."):
                    continue
                autorise, _ = _peut_lire(settings, compte, getattr(settings, attribut), objet)
                assert not autorise, f"{compte} accède à {objet}"

    def test_le_seuil_ne_peut_pas_etre_passe_en_parametre(self, settings, ch):
        """coh_prevalence(k = 1) doit échouer : la vue n'est pas paramétrée."""
        autorise, motif = _peut_lire(settings, "bi_recherche",
                                     settings.ch_recherche_password,
                                     "gold_recherche.coh_prevalence(k = 1)")
        assert not autorise
        assert "Unknown table function" in motif or "UNKNOWN" in motif.upper()


class TestHorizonDObservation:
    """L'occupation s'arrête au dernier dépôt, jamais à la date du jour.

    Le défaut corrigé : les séjours en cours étaient comptés présents jusqu'à
    « now() », si bien que le graphique montrait une falaise le jour même — une
    falaise qui se déplaçait à chaque exécution.
    """

    def test_la_serie_s_arrete_au_dernier_depot(self, ch):
        horizon = scalar(ch, "SELECT max(_ingestion_date) FROM bronze.sejours")
        assert scalar(ch, "SELECT max(jour) FROM gold_pilotage.kpi_occupation_jour") == horizon

    def test_la_serie_ne_depend_pas_de_la_date_du_jour(self, ch):
        """Si la borne était now(), la série irait jusqu'à aujourd'hui — ce qui
        est nécessairement postérieur au dernier dépôt d'un jeu figé."""
        assert scalar(ch, "SELECT countIf(jour > (SELECT max(_ingestion_date) "
                          "FROM bronze.sejours)) FROM gold_pilotage.kpi_occupation_jour") == 0

    def test_aucun_jour_sans_patient(self, ch):
        """Une occupation nulle un jour du milieu signalerait un trou dans le
        dépliage des séjours."""
        assert scalar(ch, "SELECT countIf(patients_presents = 0) "
                          "FROM gold_pilotage.kpi_occupation_jour") == 0
