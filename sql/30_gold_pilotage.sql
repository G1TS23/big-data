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
    releves_alerte  UInt64,   -- relevés portant CE motif, ce jour-là, dans ce service
    releves_total   UInt64,   -- tous les relevés du jour pour ce service
    part_alerte     Float64,  -- releves_alerte / releves_total
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, service_code, motif_alerte);

-- ─── Relevés en alerte, tous services confondus ─────────────────────────────
-- La même mesure que kpi_alertes_jour, sans la dimension service : c'est la
-- courbe que regarde une direction, quand l'autre sert à comparer les services.
CREATE TABLE IF NOT EXISTS gold_pilotage.kpi_alertes_jour_general
(
    jour            Date,
    motif_alerte    LowCardinality(String),
    releves_alerte  UInt64,   -- relevés portant CE motif, ce jour-là
    releves_total   UInt64,   -- tous les relevés du jour, alertes comprises
    part_alerte     Float64,  -- releves_alerte / releves_total
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, motif_alerte);

-- ─── Reconstruction ────────────────────────────────────────────────────────

TRUNCATE TABLE gold_pilotage.kpi_synthese;
TRUNCATE TABLE gold_pilotage.kpi_dms_service;
TRUNCATE TABLE gold_pilotage.kpi_activite_jour;
TRUNCATE TABLE gold_pilotage.kpi_urgences_jour;
TRUNCATE TABLE gold_pilotage.kpi_occupation_jour;
TRUNCATE TABLE gold_pilotage.kpi_readmission_service;
TRUNCATE TABLE gold_pilotage.kpi_alertes_jour;
TRUNCATE TABLE gold_pilotage.kpi_alertes_jour_general;

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
-- L'occupation s'arrête à l'HORIZON D'OBSERVATION : la date du dernier dépôt.
-- Deux raisons, et la seconde est un piège.
--   Au-delà de l'horizon, on ne connaît plus les admissions — la courbe
--   décroîtrait sans que l'hôpital se vide, ce qui n'est pas de l'occupation
--   mais l'extinction d'une cohorte fermée.
--   Et « now() » pour un séjour en cours faisait sortir d'un coup, à la date du
--   jour, les patients encore hospitalisés : le graphique montrait une falaise,
--   qui se déplaçait à chaque exécution. Un tableau de bord dont la forme dépend
--   de l'heure où on le regarde n'est pas un tableau de bord.
INSERT INTO gold_pilotage.kpi_occupation_jour
SELECT jour, service_code, service_label, count() AS patients_presents, {b:String}
FROM (
    -- Alias explicites : avec le CROSS JOIN, « s.service_code » ne serait pas
    -- exposé sous le nom « service_code » à la portée englobante.
    SELECT s.service_code AS service_code, d.service_label AS service_label,
           -- greatest(…, 1) est vital : un séjour PROGRAMMÉ, admis dans le
           -- futur, donnerait une durée négative, que toUInt32 convertirait en
           -- 4 294 967 295. range() tenterait alors d'allouer trente-deux
           -- gigaoctets et l'étape mourrait. Un tel séjour compte pour sa seule
           -- journée d'admission.
           toDate(s.admission_ts) + arrayJoin(range(greatest(toInt32(
               dateDiff('day', toDate(s.admission_ts),
                        least(toDate(ifNull(s.discharge_ts, h.horizon)),
                              h.horizon)) + 1), 1))) AS jour
    FROM silver.fait_sejour AS s
    INNER JOIN silver.dim_service AS d ON d.service_code = s.service_code
    -- assumeNotNull : un sous-select scalaire est toujours Nullable, et
    -- least() sur une Date Nullable propagerait le NULL à toute la colonne.
    CROSS JOIN (SELECT assumeNotNull(max(_ingestion_date)) AS horizon
                FROM bronze.sejours) AS h
    WHERE toDate(s.admission_ts) <= h.horizon
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
SELECT jour, service_code, service_label, motif_alerte,
       releves_alerte, releves_total,
       round(releves_alerte / releves_total, 4) AS part_alerte,
       {b:String}
FROM (
    SELECT toDate(m.ts) AS jour,
           m.service_code AS service_code,
           d.service_label AS service_label,
           if(m.motif_alerte = '', 'aucune', m.motif_alerte) AS motif_alerte,
           -- countIf, et non count() : sur la ligne « aucune », le groupe est
           -- fait de relevés SANS alerte, qui en compte donc zéro. La colonne
           -- garde ainsi le même sens sur toutes les lignes.
           countIf(m.est_alerte = 1) AS releves_alerte,
           -- Le dénominateur est le total du jour POUR CE SERVICE, pris par une
           -- fenêtre sur les groupes. Le calculer dans le groupe donnerait
           -- count() / count() = 1 sur chaque ligne.
           sum(count()) OVER (PARTITION BY toDate(m.ts), m.service_code) AS releves_total
    FROM silver.fait_monitoring AS m
    INNER JOIN silver.dim_service AS d ON d.service_code = m.service_code
    GROUP BY jour, service_code, service_label, motif_alerte
);
-- La ligne « aucune » est conservée, contrairement à la table générale : ici
-- elle garantit qu'un service SANS alerte un jour donné garde une ligne. Sans
-- elle, comparer les services ferait disparaître les bonnes journées au lieu de
-- les montrer à zéro. Les parts d'un même couple jour × service somment à 1.

INSERT INTO gold_pilotage.kpi_alertes_jour_general
SELECT jour, motif_alerte, releves_alerte, releves_total,
       round(releves_alerte / releves_total, 4) AS part_alerte,
       {b:String}
FROM (
    -- Pas d'alias sur motif_alerte : « m.motif_alerte AS motif_alerte » est un
    -- auto-alias, que l'analyseur peut ne pas reconnaître dans le GROUP BY.
    SELECT toDate(m.ts) AS jour,
           m.motif_alerte,
           countIf(m.est_alerte = 1) AS releves_alerte,
           -- Le dénominateur est le TOTAL DU JOUR, pris par une fenêtre sur les
           -- groupes. Le calculer dans le groupe donnerait count() / count() = 1
           -- sur chaque ligne : la part vaudrait toujours 1 pour une alerte et 0
           -- pour le reste, ce qui n'apprend rien.
           sum(count()) OVER (PARTITION BY toDate(m.ts)) AS releves_total
    FROM silver.fait_monitoring AS m
    GROUP BY jour, m.motif_alerte
)
-- Les relevés sans alerte ont servi de dénominateur ; ils n'ont pas leur place
-- dans une table qui décrit les alertes.
WHERE motif_alerte != '';
