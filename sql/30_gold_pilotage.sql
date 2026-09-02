-- gold_pilotage — indicateurs de direction, pré-agrégés.
--
-- Des TABLES et non des vues : ce sont des indicateurs fixes, affichés en
-- boucle sur un tableau de bord. Payer l'agrégation une fois par exécution
-- plutôt qu'à chaque rafraîchissement est le bon arbitrage ici.
--
-- Aucune ligne patient n'y figure : tout est agrégé.

CREATE DATABASE IF NOT EXISTS gold_pilotage;

-- ─── Synthèse : la ligne de cartes en haut du tableau de bord ───────────────
CREATE OR REPLACE TABLE gold_pilotage.kpi_synthese
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
CREATE OR REPLACE TABLE gold_pilotage.kpi_dms_service
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
CREATE OR REPLACE TABLE gold_pilotage.kpi_activite_jour
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
-- « Passages aux urgences » compte les séjours du SERVICE Urgences, et non les
-- admissions en mode urgence : un patient admis en urgence en cardiologie n'est
-- pas passé aux urgences. Les deux mesures sont conservées, nommées pour ce
-- qu'elles sont.
CREATE OR REPLACE TABLE gold_pilotage.kpi_urgences_jour
(
    jour                    Date,
    passages                UInt64,   -- séjours du service URGENCES admis ce jour
    encore_presents         UInt64,   -- dont le patient est toujours hospitalisé
    duree_moy_heures        Float64,  -- durée moyenne des passages clos, en heures
    admissions_mode_urgence UInt64,   -- admissions en mode « urgence », tous services
    _batch_id               LowCardinality(String)
) ENGINE = MergeTree ORDER BY jour;

-- ─── Occupation : patients présents chaque jour ────────────────────────────
CREATE OR REPLACE TABLE gold_pilotage.kpi_occupation_jour
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
CREATE OR REPLACE TABLE gold_pilotage.kpi_readmission_service
(
    service_code    LowCardinality(String),
    service_label   String,
    sejours_eligibles UInt64,
    readmissions    UInt64,
    taux            Float64,
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY service_code;

-- ─── Relevés en alerte, par jour et par service ────────────────────────────
CREATE OR REPLACE TABLE gold_pilotage.kpi_alertes_jour
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
CREATE OR REPLACE TABLE gold_pilotage.kpi_alertes_jour_general
(
    jour            Date,
    motif_alerte    LowCardinality(String),
    releves_alerte  UInt64,   -- relevés portant CE motif, ce jour-là
    releves_total   UInt64,   -- tous les relevés du jour, alertes comprises
    part_alerte     Float64,  -- releves_alerte / releves_total
    _batch_id       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (jour, motif_alerte);

-- ─── Évolution : activité par catégorie, et actes médicaux ──────────────────
-- Trois des cinq indicateurs demandés — actes par service, densité par lit,
-- montant facturé — partagent le MÊME GRAIN : un service. Ils tiennent donc
-- dans une seule table plutôt que dans trois presque identiques. Le grain d'une
-- table de restitution est ce qui la définit ; le multiplier sans raison
-- multiplierait aussi les occasions de les voir diverger.
CREATE OR REPLACE TABLE gold_pilotage.kpi_actes_service
(
    service_code     LowCardinality(String),
    service_label    String,
    categorie        LowCardinality(String),
    pole             LowCardinality(String),
    actes            UInt64,
    sejours          UInt64,
    actes_par_sejour Float64,
    capacite_lits    Nullable(UInt16),
    -- Nullable : un service sans description n'a pas une densité nulle, il a
    -- une densité qu'on ne sait pas calculer. La carte affichera une case vide.
    actes_par_lit    Nullable(Float64),
    montant_t2a      UInt64,
    _batch_id        LowCardinality(String)
) ENGINE = MergeTree ORDER BY service_code;

CREATE OR REPLACE TABLE gold_pilotage.kpi_activite_categorie
(
    categorie        LowCardinality(String),
    services         UInt64,
    sejours          UInt64,
    sejours_clos     UInt64,
    dms_jours        Float64,
    capacite_lits    Nullable(UInt32),
    _batch_id        LowCardinality(String)
) ENGINE = MergeTree ORDER BY categorie;

CREATE OR REPLACE TABLE gold_pilotage.kpi_actes_type
(
    code_ccam        LowCardinality(String),
    libelle          String,
    actes            UInt64,
    tarif_euros      Nullable(UInt32),
    montant_t2a      UInt64,
    part_des_actes   Float64,
    _batch_id        LowCardinality(String)
) ENGINE = MergeTree ORDER BY code_ccam;

-- ─── Reconstruction ────────────────────────────────────────────────────────
--
-- Chaque table est remplacée puis réécrite depuis silver : gold ne détient
-- aucun état propre, tout s'y recalcule.
--
-- CREATE OR REPLACE TABLE, et non CREATE IF NOT EXISTS suivi de TRUNCATE.
--
-- La raison est une panne vécue deux fois : « IF NOT EXISTS » ne modifie pas une
-- table déjà là. Ajouter une colonne passait donc inaperçu sur une installation
-- neuve et bloquait sur une base existante, avec un « No such column » qui ne
-- désigne pas la cause — il fallait supprimer la table à la main.
--
-- Le remplacement est légitime ici parce que ces tables ne détiennent AUCUN état
-- qui leur soit propre : tout est recalculé depuis les couches précédentes. Ce
-- n'est pas le cas de bronze, qui accumule les dépôts, ni de ops, qui garde la
-- trace des exécutions : ces deux-là conservent « IF NOT EXISTS », et une
-- migration de leur schéma reste une opération à conduire (voir
-- docs/EXPLOITATION.md).


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
       countIf(service_code = 'URGENCES')                        AS passages,
       countIf(service_code = 'URGENCES' AND est_en_cours = 1)   AS encore_presents,
       -- La durée en HEURES, non en jours : un passage aux urgences se compte
       -- en heures, et dateDiff('day') les écraserait toutes sur 0 ou 1.
       -- ifNotFinite : un jour où aucun passage ne serait clos donnerait NaN.
       ifNotFinite(round(avgIf(dateDiff('hour', admission_ts, discharge_ts),
                               service_code = 'URGENCES' AND est_en_cours = 0), 1), 0)
                                                                 AS duree_moy_heures,
       countIf(admission_mode = 'urgence')                       AS admissions_mode_urgence,
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

-- ─── Actes par service, densité par lit, montant facturé ────────────────────
-- Le service de l'acte vient du SÉJOUR, et il a déjà été dénormalisé sur
-- fait_acte en silver. On ne joint donc JAMAIS fait_acte à fait_sejour : une
-- telle jointure multiplierait chaque séjour par son nombre d'actes, et le
-- compte des séjours serait faux sans qu'aucune erreur ne se lève.
--
-- Les deux comptes sont agrégés SÉPARÉMENT, puis rapprochés sur service_code —
-- une clé de dimension, pas une ligne de fait.
INSERT INTO gold_pilotage.kpi_actes_service
SELECT d.service_code, d.service_label, d.categorie, d.pole,
       a.actes, s.sejours,
       round(a.actes / s.sejours, 2)                    AS actes_par_sejour,
       d.capacite_lits,
       round(a.actes / d.capacite_lits, 1)              AS actes_par_lit,
       a.montant_t2a,
       {b:String}
FROM silver.dim_service AS d
LEFT JOIN (
    SELECT f.service_code AS service_code,
           count()        AS actes,
           sum(ifNull(c.tarif_euros, 0)) AS montant_t2a
    FROM silver.fait_acte AS f
    INNER JOIN silver.dim_ccam AS c ON c.code_ccam = f.code_ccam
    GROUP BY f.service_code
) AS a ON a.service_code = d.service_code
LEFT JOIN (
    SELECT service_code, count() AS sejours
    FROM silver.fait_sejour GROUP BY service_code
) AS s ON s.service_code = d.service_code;

-- ─── Activité et DMS par catégorie de service ───────────────────────────────
-- La catégorie vient de la dimension enrichie. Les services non décrits y
-- figurent sous « non décrit » : ils pèsent dans l'activité de l'hôpital, les
-- masquer fausserait le total.
INSERT INTO gold_pilotage.kpi_activite_categorie
SELECT a.categorie, a.services, a.sejours, a.sejours_clos, a.dms_jours,
       cap.capacite_lits, {b:String}
FROM (
    SELECT d.categorie AS categorie,
           uniqExact(d.service_code)   AS services,
           count()                     AS sejours,
           countIf(s.est_en_cours = 0) AS sejours_clos,
           round(avgIf(s.duree_jours, s.est_en_cours = 0), 2) AS dms_jours
    FROM silver.fait_sejour AS s
    INNER JOIN silver.dim_service AS d ON d.service_code = s.service_code
    GROUP BY d.categorie
) AS a
LEFT JOIN (
    -- La capacité s'agrège depuis la DIMENSION, jamais depuis les séjours : la
    -- sommer ligne à ligne compterait les lits une fois par séjour.
    --
    -- Et elle n'est renseignée que si TOUS les services de la catégorie le
    -- sont. Une somme partielle serait sous-estimée sans le dire, ce qui est
    -- pire qu'une case vide : elle se laisserait comparer aux autres.
    SELECT categorie,
           if(countIf(capacite_lits IS NULL) = 0,
              toNullable(toUInt32(sum(capacite_lits))),
              CAST(NULL AS Nullable(UInt32))) AS capacite_lits
    FROM silver.dim_service GROUP BY categorie
) AS cap ON cap.categorie = a.categorie;

-- ─── Actes par type d'acte ──────────────────────────────────────────────────
INSERT INTO gold_pilotage.kpi_actes_type
SELECT c.code_ccam, c.libelle, count() AS actes, c.tarif_euros,
       count() * ifNull(c.tarif_euros, 0) AS montant_t2a,
       round(count() / sum(count()) OVER (), 4) AS part_des_actes,
       {b:String}
FROM silver.fait_acte AS f
INNER JOIN silver.dim_ccam AS c ON c.code_ccam = f.code_ccam
GROUP BY c.code_ccam, c.libelle, c.tarif_euros;
