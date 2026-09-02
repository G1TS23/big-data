"""Contrôles de la couche silver.

Deux niveaux : les règles chargées depuis la configuration se testent seules ;
les chiffres produits exigent le moteur, et sont ignorés s'il n'est pas là.
"""
import pytest

from eds.sql import load_regles, to_parameters


class TestReglesMetier:
    def test_bornes_et_seuils_distincts(self):
        """Confondre les deux jetterait des alertes ou compterait des pannes
        de capteur comme des urgences."""
        r = load_regles()
        assert r["bornes"]["heart_rate"]["min"] < r["alertes"]["heart_rate"]["bas"]
        assert r["alertes"]["heart_rate"]["haut"] < r["bornes"]["heart_rate"]["max"]
        assert r["bornes"]["spo2"]["min"] < r["alertes"]["spo2"]["bas"]
        assert r["alertes"]["temp_c"]["haut"] < r["bornes"]["temp_c"]["max"]

    def test_seuils_dans_les_plages_vides_observees(self):
        """Les données ne contiennent aucune valeur entre les deux populations.
        Un seuil placé dans ce vide rend l'indicateur insensible à son réglage."""
        a = load_regles()["alertes"]
        assert 50 <= a["heart_rate"]["bas"] <= 59 or a["heart_rate"]["bas"] == 60
        assert 96 <= a["heart_rate"]["haut"] <= 100
        assert 92 <= a["spo2"]["bas"] <= 95
        assert 37.7 <= a["temp_c"]["haut"] <= 38.5

    def test_parametres_couvrent_les_marqueurs_du_sql(self):
        from pathlib import Path
        import re
        sql = (Path(__file__).resolve().parent.parent / "sql" / "20_silver.sql").read_text(encoding="utf-8")
        marqueurs = set(re.findall(r"\{(\w+):", sql)) - {"b"}   # b = run_id, injecté à part
        assert marqueurs <= set(to_parameters(load_regles())), \
            f"marqueurs sans paramètre : {marqueurs - set(to_parameters(load_regles()))}"


# ─── Chiffres produits — exigent ClickHouse ────────────────────────────────

@pytest.fixture(scope="module")
def ch():
    from eds import sql
    from eds.config import load_settings
    try:
        # Délai court : sans le socle, on veut savoir tout de suite qu'on
        # saute, pas attendre les reconnexions du pilote.
        client = sql.connect(load_settings(), connect_timeout=2, send_receive_timeout=5)
        client.command("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse indisponible : {exc}")
    if not client.query("SELECT count() FROM silver.fait_sejour").result_rows[0][0]:
        pytest.skip("silver vide — lancer `eds lake && eds bronze && eds silver`")
    return client


def scalar(client, sql, **params):
    return client.query(sql, parameters=params).result_rows[0][0]


@pytest.fixture(scope="module")
def dernier_run(ch):
    """ops.rejects est un journal d'audit : il s'accumule d'une exécution à
    l'autre. Tout décompte doit donc être borné au run que l'on vérifie."""
    return scalar(ch, "SELECT run_id FROM ops.run_log FINAL WHERE command = 'silver' "
                      "AND status = 'OK' ORDER BY started_at DESC LIMIT 1")


class TestQualite:
    def test_deduplication_des_patients(self, ch):
        """Le CHU livre des instantanés qui se recouvrent : dim_patient doit
        contenir exactement les identifiants distincts, pas les lignes reçues.
        Les volumes attendus sont dans tests/test_reconciliation.py."""
        distincts = scalar(ch, "SELECT uniqExact(patient_key) FROM bronze.patients "
                               "WHERE patient_key != ''")
        assert scalar(ch, "SELECT count() FROM silver.dim_patient") == distincts
        assert scalar(ch, "SELECT count() FROM bronze.patients") > distincts

    def test_sejours_incoherents_ecartes(self, ch, dernier_run):
        """Autant de rejets que de séjours dont la sortie précède l'admission —
        la comparaison porte sur les HORODATAGES, non sur les dates."""
        incoherents = scalar(ch, "SELECT countIf(discharge_ts < admission_ts) "
                                 "FROM bronze.sejours")
        assert scalar(ch, "SELECT count() FROM ops.rejects WHERE run_id = {r:String} "
                          "AND table_source='fait_sejour' AND regle='sortie_avant_admission'",
                      r=dernier_run) == incoherents
        assert scalar(ch, "SELECT countIf(discharge_ts < admission_ts) "
                          "FROM silver.fait_sejour") == 0

    def test_sejours_en_cours_conserves(self, ch):
        """Un patient encore hospitalisé n'est pas une anomalie : il est conservé
        et marqué, jamais rejeté."""
        en_cours = scalar(ch, "SELECT countIf(est_en_cours = 1) FROM silver.fait_sejour")
        assert en_cours > 0
        assert en_cours == scalar(ch, "SELECT countIf(discharge_ts IS NULL) "
                                      "FROM silver.fait_sejour")
        assert scalar(ch, "SELECT countIf(discharge_ts IS NULL AND est_en_cours = 0) "
                          "FROM silver.fait_sejour") == 0

    def test_releves_hors_bornes_ecartes(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_monitoring "
                          "WHERE heart_rate NOT BETWEEN 20 AND 250 "
                          "OR spo2 NOT BETWEEN 50 AND 100 OR temp_c NOT BETWEEN 30 AND 45") == 0

    def test_alertes_conservees_et_signalees(self, ch):
        """Hors seuil clinique : la valeur est vraie, le patient va mal."""
        alertes = scalar(ch, "SELECT countIf(est_alerte = 1) FROM silver.fait_monitoring")
        assert alertes > 0
        assert scalar(ch, "SELECT countIf(est_alerte = 1 AND motif_alerte = '') "
                          "FROM silver.fait_monitoring") == 0

    def test_aucune_ligne_ne_disparait_sans_etre_comptee(self, ch, dernier_run):
        """Réconciliation source ↔ silver, bornée à une exécution."""
        for table, source in (("fait_sejour", "SELECT count() FROM bronze.sejours"),
                              ("fait_monitoring", "SELECT count() FROM bronze.monitoring"),
                              ("fait_diagnostic",
                               "SELECT sum(length(diagnostics)) FROM bronze.diagnostics")):
            entrees = scalar(ch, source)
            gardees = scalar(ch, f"SELECT count() FROM silver.{table}")
            rejetees = scalar(ch, "SELECT count() FROM ops.rejects WHERE run_id = {r:String} "
                                  "AND table_source = {t:String}", r=dernier_run, t=table)
            assert gardees + rejetees == entrees, (
                f"{table} : {gardees} gardées + {rejetees} rejetées != {entrees} entrées")


class TestDiagnosticsAplatis:
    """Le JSON imbriqué est aplati par le moteur. Deux façons de se tromper
    silencieusement : lire le tuple par position, ou produire une table vide."""

    def test_les_diagnostics_ne_sont_pas_vides(self, ch):
        """Un contrôle d'intégrité passe À VIDE : « aucune ligne invalide » est
        vrai sur une table sans lignes. Il faut donc l'affirmer séparément."""
        assert scalar(ch, "SELECT count() FROM silver.fait_diagnostic") > 0

    def test_le_type_de_diagnostic_appartient_a_son_domaine(self, ch):
        """Le test qui attrape une inversion du tuple : si code et type étaient
        permutés, cette colonne contiendrait des codes CIM-10."""
        valeurs = {v for (v,) in ch.query(
            "SELECT DISTINCT type_diagnostic FROM silver.fait_diagnostic").result_rows}
        assert valeurs == {"principal", "associe"}, f"valeurs inattendues : {valeurs}"

    def test_le_code_a_bien_la_forme_d_un_code_cim10(self, ch):
        """Une lettre suivie de deux chiffres. L'inversion produirait
        « principal » ou « associe »."""
        assert scalar(ch, "SELECT countIf(NOT match(code_cim10, '^[A-Z][0-9]{2}$')) "
                          "FROM silver.fait_diagnostic") == 0

    def test_un_seul_diagnostic_principal_par_sejour(self, ch):
        assert scalar(ch, "SELECT countIf(n > 1) FROM (SELECT countIf(est_principal = 1) AS n "
                          "FROM silver.fait_diagnostic GROUP BY stay_id)") == 0

    def test_la_tranche_dage_correspond_a_l_age_du_sejour(self, ch):
        """La tranche est dérivée de l'âge porté par le séjour. Les bornes
        doivent se correspondre exactement, sinon une cohorte serait rangée
        dans la mauvaise classe."""
        assert scalar(ch, """
            SELECT countIf(d.tranche_age != multiIf(
                       s.age_a_admission < 18, '00-17', s.age_a_admission < 65, '18-64',
                       s.age_a_admission < 75, '65-74', s.age_a_admission < 85, '75-84',
                       '85+'))
            FROM silver.fait_diagnostic AS d
            INNER JOIN silver.fait_sejour AS s ON s.stay_id = d.stay_id""") == 0

    def test_la_tranche_n_existe_qu_a_un_seul_endroit(self, ch):
        """Portée par deux tables, elle finirait par diverger."""
        porteuses = {t for (t,) in ch.query(
            "SELECT DISTINCT `table` FROM system.columns "
            "WHERE database = 'silver' AND name = 'tranche_age'").result_rows}
        assert porteuses == {"fait_diagnostic"}

    def test_la_tranche_dage_appartient_a_son_domaine(self, ch):
        tranches = {t for (t,) in ch.query(
            "SELECT DISTINCT tranche_age FROM silver.fait_diagnostic").result_rows}
        assert tranches <= {"00-17", "18-64", "65-74", "75-84", "85+"}

    def test_est_principal_suit_le_type(self, ch):
        assert scalar(ch, "SELECT countIf((type_diagnostic = 'principal') != (est_principal = 1)) "
                          "FROM silver.fait_diagnostic") == 0


class TestIntegriteReferentielle:
    def test_les_tables_ne_sont_pas_vides(self, ch):
        """Précaution générale : les contrôles qui suivent comptent des
        violations, et zéro violation sur zéro ligne ne prouve rien."""
        for table in ("dim_patient", "fait_sejour", "fait_diagnostic", "fait_monitoring"):
            assert scalar(ch, f"SELECT count() FROM silver.{table}") > 0, table

    def test_tout_sejour_pointe_un_patient_connu(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_sejour "
                          "WHERE patient_key NOT IN (SELECT patient_key FROM silver.dim_patient)") == 0

    def test_tout_diagnostic_pointe_un_sejour_et_un_code_connus(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_diagnostic "
                          "WHERE stay_id NOT IN (SELECT stay_id FROM silver.fait_sejour) "
                          "OR code_cim10 NOT IN (SELECT code_cim10 FROM silver.dim_cim10)") == 0

    def test_tout_releve_pointe_un_sejour_connu(self, ch):
        assert scalar(ch, "SELECT count() FROM silver.fait_monitoring "
                          "WHERE stay_id NOT IN (SELECT stay_id FROM silver.fait_sejour)") == 0


class TestReadmission:
    def test_jamais_sur_un_ecart_negatif(self, ch):
        """Un écart négatif signale des séjours qui se chevauchent, pas un retour."""
        assert scalar(ch, "SELECT countIf(est_readmission_30j = 1 "
                          "AND jours_depuis_sortie_precedente < 0) FROM silver.fait_sejour") == 0

    def test_jamais_apres_un_deces_ni_un_transfert(self, ch):
        assert scalar(ch, "SELECT countIf(est_readmission_30j = 1 AND mode_sortie_precedent "
                          "IN ('deces','mutation','transfert')) FROM silver.fait_sejour") == 0

    def test_jamais_au_dela_de_la_fenetre(self, ch):
        fenetre = load_regles()["readmission_fenetre_jours"]
        assert scalar(ch, f"SELECT countIf(est_readmission_30j = 1 "
                          f"AND jours_depuis_sortie_precedente > {fenetre}) "
                          f"FROM silver.fait_sejour") == 0

    def test_taux_plausible(self, ch):
        """Dénominateur : les séjours clos dont le patient POUVAIT revenir.
        Ce test aurait attrapé la version qui annonçait 59 %."""
        taux = scalar(ch, "SELECT countIf(est_readmission_30j = 1) / countIf("
                          "est_en_cours = 0 AND discharge_mode "
                          "NOT IN ('deces','mutation','transfert')) FROM silver.fait_sejour")
        assert 0.01 < taux < 0.25, f"taux de réadmission invraisemblable : {taux:.1%}"


class TestReproductibilite:
    """Le sujet exige des indicateurs reproductibles. Une fenêtre dont l'ordre
    n'est pas total ne l'est pas."""

    def test_la_readmission_ne_change_pas_d_une_execution_a_l_autre(self, ch):
        """Deux séjours admis à la même seconde existent dans les données. Sans
        départage, « le précédent » varie et le taux avec lui."""
        avant = dict(ch.query("SELECT stay_id, est_readmission_30j "
                              "FROM silver.fait_sejour").result_rows)
        import subprocess, sys
        from eds.config import ROOT
        subprocess.run([str(ROOT / ".venv/bin/eds"), "silver"],
                       capture_output=True, check=True, cwd=ROOT)
        apres = dict(ch.query("SELECT stay_id, est_readmission_30j "
                              "FROM silver.fait_sejour").result_rows)
        differences = {k for k in avant if avant[k] != apres.get(k)}
        assert not differences, f"{len(differences)} séjours ont changé de verdict"

    def test_l_ordre_des_fenetres_est_total(self, ch):
        """Le départage doit figurer dans TOUTES les fenêtres du script, sinon
        deux colonnes dérivées du même classement pourraient diverger."""
        from eds.config import ROOT
        script = (ROOT / "sql" / "20_silver.sql").read_text(encoding="utf-8")
        fenetres = script.count("PARTITION BY v.patient_key ORDER BY v.admission_ts")
        departages = script.count("ORDER BY v.admission_ts ASC, v.stay_id ASC")
        assert fenetres == departages, "une fenêtre n'a pas d'ordre total"


class TestOccupationBornee:
    def test_une_admission_future_ne_dilate_pas_l_univers(self, ch):
        """Un séjour PROGRAMMÉ donnerait une durée négative, que toUInt32
        convertirait en 4 294 967 295 : range() tenterait d'allouer trente-deux
        gigaoctets. La garde le ramène à sa seule journée d'admission."""
        jours = scalar(ch, "SELECT length(range(greatest(toInt32("
                           "dateDiff('day', toDate(now() + INTERVAL 90 DAY), toDate(now())) + 1), 1)))")
        assert jours == 1

    def test_un_sejour_normal_couvre_toutes_ses_journees(self, ch):
        jours = scalar(ch, "SELECT length(range(greatest(toInt32("
                           "dateDiff('day', toDate('2026-08-26'), toDate('2026-08-30')) + 1), 1)))")
        assert jours == 5           # du 26 au 30 inclus


class TestAnomaliesSignalees:
    def test_chevauchements_signales_et_non_rejetes(self, ch, dernier_run):
        """L'anomalie porte sur la relation entre deux séjours : en écarter un
        au hasard fabriquerait une erreur au lieu d'en corriger une.

        Le contrôle doit exister et compter juste MÊME À ZÉRO — un jeu de
        données assaini ne doit pas faire disparaître la mesure, sans quoi on ne
        saurait plus distinguer « aucun chevauchement » de « plus personne ne
        regarde »."""
        assert scalar(ch, "SELECT count() FROM ops.rejects "
                          "WHERE regle LIKE '%chevauch%'") == 0
        assert scalar(ch, "SELECT lignes_concernees FROM ops.data_quality "
                          "WHERE run_id = {r:String} AND regle = 'sejours_chevauchants' "
                          "AND traitement = 'SIGNALEMENT'", r=dernier_run) == scalar(
            ch, "SELECT countIf(est_chevauchant = 1) FROM silver.fait_sejour")

    def test_admissions_apres_deces_signalees(self, ch, dernier_run):
        assert scalar(ch, "SELECT lignes_concernees FROM ops.data_quality "
                          "WHERE run_id = {r:String} AND regle = 'admission_apres_deces'",
                      r=dernier_run) == scalar(
            ch, "SELECT countIf(est_apres_deces = 1) FROM silver.fait_sejour")
        assert scalar(ch, "SELECT count() FROM ops.rejects "
                          "WHERE regle LIKE '%deces%'") == 0

    def test_mode_de_sortie_manquant_signale(self, ch, dernier_run):
        """Un séjour clos sans mode de sortie est un trou des données source, pas
        du traitement : il est compté, jamais rejeté."""
        assert scalar(ch, "SELECT lignes_concernees FROM ops.data_quality "
                          "WHERE run_id = {r:String} AND regle = 'mode_sortie_manquant'",
                      r=dernier_run) == scalar(
            ch, "SELECT countIf(est_en_cours = 0 AND discharge_mode = '') "
                "FROM silver.fait_sejour")

    def test_aucun_sejour_en_cours_ne_porte_de_mode_de_sortie(self, ch):
        """L'incohérence symétrique : sortir sans être sorti."""
        assert scalar(ch, "SELECT countIf(est_en_cours = 1 AND discharge_mode != '') "
                          "FROM silver.fait_sejour") == 0


class TestExclusionsDuTauxDeReadmission:
    """Le numérateur publié doit se reconstituer depuis ops.data_quality.

    Un retour après décès est physiquement impossible ; un retour après mutation
    ou transfert n'est pas un retour, le patient n'était jamais rentré chez lui.
    Ces deux exclusions ne déplacent presque pas le taux — elles changent ce
    qu'il affirme. Voir docs/VALIDATION.md.
    """

    def controle(self, ch, run, regle):
        return ch.query("SELECT lignes_entree, lignes_concernees FROM ops.data_quality "
                        "WHERE run_id = %(r)s AND regle = %(g)s",
                        parameters={"r": run, "g": regle}).result_rows[0]

    def test_le_numerateur_se_reconstitue(self, ch, dernier_run):
        fenetre, deces = self.controle(ch, dernier_run, "retour_apres_deces_ecarte")
        _, mutation = self.controle(ch, dernier_run, "retour_apres_mutation_ecarte")
        publie = scalar(ch, "SELECT countIf(est_readmission_30j = 1) FROM silver.fait_sejour")
        assert fenetre - deces - mutation == publie

    def test_aucune_readmission_apres_un_deces(self, ch):
        assert scalar(ch, "SELECT countIf(est_readmission_30j = 1 "
                          "AND mode_sortie_precedent = 'deces') FROM silver.fait_sejour") == 0

    def test_les_retours_post_mortem_existent_bel_et_bien(self, ch, dernier_run):
        """Sans cette assertion, le test précédent passerait sur zéro ligne."""
        _, deces = self.controle(ch, dernier_run, "retour_apres_deces_ecarte")
        assert deces == scalar(ch, "SELECT countIf(est_apres_deces = 1 "
                                   "AND jours_depuis_sortie_precedente BETWEEN 0 AND 30) "
                                   "FROM silver.fait_sejour")
        assert deces > 0
