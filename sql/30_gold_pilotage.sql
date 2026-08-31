-- gold_pilotage — indicateurs de direction, pré-agrégés.
--
-- Des TABLES et non des vues : ce sont des indicateurs fixes, affichés en
-- boucle sur un tableau de bord. Payer l'agrégation une fois par exécution
-- plutôt qu'à chaque rafraîchissement est le bon arbitrage ici.
--
-- Aucune ligne patient n'y figure : tout est agrégé.

CREATE DATABASE IF NOT EXISTS gold_pilotage;

-- ─── Synthèse : la ligne de cartes en haut du tableau de bord ───────────────
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_synthese
(
    sejours              UInt64,
    patients             UInt64,
    sejours_en_cours     UInt64,
    dms_jours            Float64,
    taux_readmission_30j Float64,
    releves_en_alerte    UInt64,
    part_releves_alerte  Float64,
    _batch_id            LowCardinality(String)
) ENGINE = MergeTree ORDER BY tuple();

-- ─── Durée moyenne de séjour, par service et par mois ──────────────────────
-- Séjours CLOS uniquement : inclure un séjour en cours tronquerait sa durée et
-- tirerait la DMS vers le bas.
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_dms_service
(
    mois            Date,
    service_code    LowCardinality(String),
    service_label   String,
    sejours_clos    UInt64,
    dms_jours       Float64,
    duree_mediane   Float64,
    duree_p90       Float64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (service_code, mois);

-- ─── Activité quotidienne, par service ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_activite_jour
(
    jour            Date,
    service_code    LowCardinality(String),
    service_label   String,
    admissions      UInt64,
    dont_urgences   UInt64,
    dont_programme  UInt64,
    dont_mutation   UInt64,
    sorties         UInt64,
    dont_deces      UInt64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, service_code);

-- ─── Passages aux urgences par jour ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_urgences_jour
(
    jour            Date,
    passages        UInt64,
    dont_service_urgences UInt64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY jour;

-- ─── Occupation : patients présents chaque jour ────────────────────────────
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_occupation_jour
(
    jour            Date,
    service_code    LowCardinality(String),
    service_label   String,
    patients_presents UInt64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, service_code);

-- ─── Réadmission à 30 jours, par service de SORTIE ─────────────────────────
-- La qualité des soins se juge sur le service qui a laissé sortir le patient,
-- pas sur celui qui le récupère.
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_readmission_service
(
    service_code    LowCardinality(String),
    service_label   String,
    sejours_eligibles UInt64,
    readmissions    UInt64,
    taux            Float64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY service_code;

-- ─── Relevés en alerte, par jour et par service ────────────────────────────
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_alertes_jour
(
    jour            Date,
    service_code    LowCardinality(String),
    service_label   String,
    motif_alerte    LowCardinality(String),
    releves_alerte  UInt64,
    releves_total   UInt64,
    part_alerte     Float64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, service_code, motif_alerte);

-- ─── Reconstruction ────────────────────────────────────────────────────────

TRUNCATE TABLE gold_pilotage.kpi_synthese;
TRUNCATE TABLE gold_pilotage.kpi_dms_service;
TRUNCATE TABLE gold_pilotage.kpi_activite_jour;
TRUNCATE TABLE gold_pilotage.kpi_urgences_jour;
TRUNCATE TABLE gold_pilotage.kpi_occupation_jour;
TRUNCATE TABLE gold_pilotage.kpi_readmission_service;
TRUNCATE TABLE gold_pilotage.kpi_alertes_jour;

INSERT INTO gold_pilotage.kpi_synthese
SELECT
    (SELECT count() FROM silver.fait_sejour),
    (SELECT uniqExact(patient_key) FROM silver.fait_sejour),
    (SELECT countIf(est_en_cours = 1) FROM silver.fait_sejour),
    (SELECT round(avgIf(duree_jours, est_en_cours = 0), 2) FROM silver.fait_sejour),
    (SELECT round(countIf(est_readmission_30j = 1)
                / nullIf(countIf(est_en_cours = 0
                        AND discharge_mode NOT IN ('deces', 'mutation', 'transfert')), 0), 4)
     FROM silver.fait_sejour),
    (SELECT countIf(est_alerte = 1) FROM silver.fait_monitoring),
    (SELECT round(countIf(est_alerte = 1) / count(), 4) FROM silver.fait_monitoring),
    {b:String};

INSERT INTO gold_pilotage.kpi_dms_service
SELECT toStartOfMonth(s.admission_ts) AS mois, s.service_code, d.service_label,
       count() AS sejours_clos,
       round(avg(s.duree_jours), 2) AS dms_jours,
       round(quantileExact(0.5)(s.duree_jours), 1) AS duree_mediane,
       round(quantileExact(0.9)(s.duree_jours), 1) AS duree_p90,
       {b:String}
FROM silver.fait_sejour AS s
INNER JOIN silver.dim_service AS d ON d.service_code = s.service_code
WHERE s.est_en_cours = 0
GROUP BY mois, s.service_code, d.service_label;

INSERT INTO gold_pilotage.kpi_activite_jour
SELECT toDate(s.admission_ts) AS jour, s.service_code, d.service_label,
       count() AS admissions,
       countIf(s.admission_mode = 'urgence')   AS dont_urgences,
       countIf(s.admission_mode = 'programme') AS dont_programme,
       countIf(s.admission_mode = 'mutation')  AS dont_mutation,
       countIf(s.est_en_cours = 0)             AS sorties,
       countIf(s.discharge_mode = 'deces')     AS dont_deces,
       {b:String}
FROM silver.fait_sejour AS s
INNER JOIN silver.dim_service AS d ON d.service_code = s.service_code
GROUP BY jour, s.service_code, d.service_label;

INSERT INTO gold_pilotage.kpi_urgences_jour
SELECT toDate(admission_ts) AS jour,
       countIf(admission_mode = 'urgence') AS passages,
       countIf(service_code = 'URGENCES')  AS dont_service_urgences,
       {b:String}
FROM silver.fait_sejour
GROUP BY jour;

-- Un patient est « présent » un jour donné s'il est admis avant ou ce jour-là,
-- et pas encore sorti.
--
-- Plutôt que de croiser un calendrier avec les séjours — ClickHouse n'accepte
-- pas de jointure sans clé d'égalité — chaque séjour est dilaté en la liste des
-- journées qu'il couvre. Une seule passe, aucun produit cartésien.
INSERT INTO gold_pilotage.kpi_occupation_jour
SELECT jour, service_code, service_label, count() AS patients_presents, {b:String}
FROM (
    SELECT s.service_code, d.service_label,
           toDate(s.admission_ts) + arrayJoin(range(toUInt32(
               dateDiff('day', toDate(s.admission_ts),
                        toDate(ifNull(s.discharge_ts, now()))) + 1))) AS jour
    FROM silver.fait_sejour AS s
    INNER JOIN silver.dim_service AS d ON d.service_code = s.service_code
)
GROUP BY jour, service_code, service_label;

-- Numérateur et dénominateur ne se comptent pas sur les mêmes lignes :
--   dénominateur — les séjours clos d'un service, dont le patient pouvait revenir
--   numérateur   — les séjours qui SONT un retour, rattachés au service de sortie
INSERT INTO gold_pilotage.kpi_readmission_service
SELECT e.service_code, d.service_label, e.eligibles, ifNull(r.readmissions, 0) AS readmissions,
       round(ifNull(r.readmissions, 0) / nullIf(e.eligibles, 0), 4) AS taux, {b:String}
FROM (
    SELECT service_code, count() AS eligibles
    FROM silver.fait_sejour
    WHERE est_en_cours = 0 AND discharge_mode NOT IN ('deces', 'mutation', 'transfert')
    GROUP BY service_code
) AS e
LEFT JOIN (
    SELECT service_precedent AS service_code, count() AS readmissions
    FROM silver.fait_sejour WHERE est_readmission_30j = 1
    GROUP BY service_precedent
) AS r ON r.service_code = e.service_code
INNER JOIN silver.dim_service AS d ON d.service_code = e.service_code;

INSERT INTO gold_pilotage.kpi_alertes_jour
SELECT toDate(m.ts) AS jour, m.service_code, d.service_label,
       if(m.motif_alerte = '', 'aucune', m.motif_alerte) AS motif_alerte,
       countIf(m.est_alerte = 1) AS releves_alerte,
       count() AS releves_total,
       round(countIf(m.est_alerte = 1) / count(), 4) AS part_alerte,
       {b:String}
FROM silver.fait_monitoring AS m
INNER JOIN silver.dim_service AS d ON d.service_code = m.service_code
GROUP BY jour, m.service_code, d.service_label, motif_alerte;
