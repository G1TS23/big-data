-- Couche silver : les tables typées deviennent un modèle métier fiable.
--
-- Trois choses s'y passent, et rien d'autre :
--   NETTOYER   écarter ce qui est faux, en traçant chaque ligne écartée
--   DÉDUPLIQUER  absorber les redépôts quotidiens
--   ENRICHIR   dériver ce qui est une propriété de l'événement (durée, âge,
--              réadmission, alerte) — jamais un agrégat, qui appartient à gold
--
-- Silver est intégralement reconstruite depuis bronze à chaque exécution. Le
-- rejeu est donc trivialement idempotent, et une correction de règle se
-- propage à tout l'historique au run suivant. À plus grande échelle on
-- reconstruirait par partition ; à 15 000 séjours, la simplicité l'emporte.
--
-- Les valeurs écartées partent dans ops.rejects avec leur règle et le run_id
-- qui les a produites : écarter sans tracer reviendrait à perdre la donnée.

CREATE DATABASE IF NOT EXISTS silver;

-- ─── Dimensions ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS silver.dim_patient
(
    patient_key String,
    birth_year  UInt16,
    sex         LowCardinality(String),
    region_code LowCardinality(String),
    _batch_id   LowCardinality(String) CODEC(ZSTD(1)),
    _built_at   DateTime DEFAULT now()  CODEC(ZSTD(1))
)
ENGINE = MergeTree ORDER BY patient_key;

CREATE TABLE IF NOT EXISTS silver.dim_service
(
    service_code  LowCardinality(String),
    service_label String,
    _batch_id     LowCardinality(String) CODEC(ZSTD(1))
)
ENGINE = MergeTree ORDER BY service_code;

CREATE TABLE IF NOT EXISTS silver.dim_cim10
(
    code_cim10 LowCardinality(String),
    libelle    String,
    _batch_id  LowCardinality(String) CODEC(ZSTD(1))
)
ENGINE = MergeTree ORDER BY code_cim10;

-- ─── Faits ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS silver.fait_sejour
(
    stay_id         String,
    patient_key     String,
    service_code    LowCardinality(String),
    admission_ts    DateTime,
    discharge_ts    Nullable(DateTime),
    admission_mode  LowCardinality(String),
    discharge_mode  LowCardinality(String),

    duree_jours     Nullable(Int32),
    est_en_cours    UInt8,
    age_a_admission Int16,
    tranche_age     LowCardinality(String),
    jours_depuis_sortie_precedente Nullable(Int32),
    mode_sortie_precedent LowCardinality(String),
    -- Service du séjour précédent : le taux de réadmission se mesure sur le
    -- service qui a laissé SORTIR le patient, pas sur celui qui le reçoit.
    service_precedent LowCardinality(String),
    est_readmission_30j UInt8,
    -- Anomalies détectées en explorant, conservées et signalées (cf. ops.data_quality) :
    est_chevauchant  UInt8,   -- le séjour précédent du patient n'était pas terminé
    est_apres_deces  UInt8,   -- le séjour précédent s'est terminé par un décès

    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _built_at       DateTime DEFAULT now()  CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(admission_ts)
ORDER BY (service_code, admission_ts, stay_id);

CREATE TABLE IF NOT EXISTS silver.fait_diagnostic
(
    stay_id         String,
    patient_key     String,
    code_cim10      LowCardinality(String),
    type_diagnostic LowCardinality(String),
    est_principal   UInt8,
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _built_at       DateTime DEFAULT now()  CODEC(ZSTD(1))
)
ENGINE = MergeTree ORDER BY (code_cim10, stay_id);

CREATE TABLE IF NOT EXISTS silver.fait_monitoring
(
    stay_id      String,
    service_code LowCardinality(String),
    ts           DateTime          CODEC(DoubleDelta, ZSTD(1)),
    heart_rate   Int32             CODEC(T64, ZSTD(1)),
    spo2         Int32             CODEC(T64, ZSTD(1)),
    temp_c       Float32           CODEC(Gorilla, ZSTD(1)),
    est_alerte   UInt8             CODEC(ZSTD(1)),
    motif_alerte LowCardinality(String) CODEC(ZSTD(1)),
    _batch_id    LowCardinality(String) CODEC(ZSTD(1)),
    _built_at    DateTime DEFAULT now()  CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (stay_id, ts);

-- ─── Reconstruction ─────────────────────────────────────────────────────────

TRUNCATE TABLE silver.dim_patient;
TRUNCATE TABLE silver.dim_service;
TRUNCATE TABLE silver.dim_cim10;
TRUNCATE TABLE silver.fait_sejour;
TRUNCATE TABLE silver.fait_diagnostic;
TRUNCATE TABLE silver.fait_monitoring;

-- Référentiels : on retient le dépôt le plus récent.
INSERT INTO silver.dim_service (service_code, service_label, _batch_id)
SELECT service_code, argMax(service_label, _ingestion_date), {b:String}
FROM bronze.services GROUP BY service_code;

INSERT INTO silver.dim_cim10 (code_cim10, libelle, _batch_id)
SELECT code_cim10, argMax(libelle, _ingestion_date), {b:String}
FROM bronze.cim10 GROUP BY code_cim10;

-- ─── dim_patient ────────────────────────────────────────────────────────────
-- Le même patient revient chaque jour : on garde la version du dépôt le plus
-- récent. La déduplication n'est pas un rejet, elle n'alimente pas ops.rejects.

INSERT INTO ops.rejects (run_id, table_source, cle, regle, valeur)
SELECT {b:String}, 'dim_patient', patient_key,
       multiIf(patient_key = '',                                       'cle_absente',
               sex NOT IN ('M', 'F'),                                  'sexe_non_normalise',
               birth_year IS NULL,                                     'annee_naissance_absente',
                                                                       'annee_naissance_implausible'),
       concat('sex=', toString(sex), ' birth_year=', toString(birth_year))
FROM bronze.patients
WHERE patient_key = ''
   OR sex NOT IN ('M', 'F')
   OR birth_year IS NULL
   OR birth_year NOT BETWEEN {annee_min:UInt16} AND toYear(now());

INSERT INTO silver.dim_patient (patient_key, birth_year, sex, region_code, _batch_id)
SELECT patient_key,
       argMax(birth_year, _ingestion_date),
       argMax(sex, _ingestion_date),
       argMax(region_code, _ingestion_date),
       {b:String}
FROM bronze.patients
WHERE patient_key != ''
  AND sex IN ('M', 'F')
  AND birth_year IS NOT NULL
  AND birth_year BETWEEN {annee_min:UInt16} AND toYear(now())
GROUP BY patient_key;

-- ─── fait_sejour ────────────────────────────────────────────────────────────
-- Une sortie vide n'est pas une anomalie : c'est un patient encore hospitalisé.
-- Elle est donc absente des règles de rejet, et marquée est_en_cours = 1.

INSERT INTO ops.rejects (run_id, table_source, cle, regle, valeur)
SELECT {b:String}, 'fait_sejour', s.stay_id,
       multiIf(s.admission_ts IS NULL,                          'admission_absente',
               s.discharge_ts < s.admission_ts,                 'sortie_avant_admission',
               d.service_code = '',                             'service_inconnu',
                                                                'patient_inconnu'),
       concat('admission=', toString(s.admission_ts),
              ' sortie=', toString(s.discharge_ts),
              ' service=', toString(s.service_code))
FROM bronze.sejours AS s
LEFT JOIN silver.dim_service AS d USING (service_code)
LEFT JOIN silver.dim_patient AS p USING (patient_key)
WHERE s.admission_ts IS NULL
   OR s.discharge_ts < s.admission_ts
   OR d.service_code = ''
   OR p.patient_key = '';

INSERT INTO silver.fait_sejour
    (stay_id, patient_key, service_code, admission_ts, discharge_ts,
     admission_mode, discharge_mode, duree_jours, est_en_cours,
     age_a_admission, tranche_age, jours_depuis_sortie_precedente,
     mode_sortie_precedent, service_precedent, est_readmission_30j,
     est_chevauchant, est_apres_deces, _batch_id)
SELECT
    stay_id, patient_key, service_code, admission_ts, discharge_ts,
    admission_mode, discharge_mode,
    duree_jours,
    est_en_cours,
    age_a_admission,
    -- Tranches cliniques. Volontairement dans le modèle et non en configuration :
    -- c'est la granularité de diffusion de la recherche, pas un réglage.
    multiIf(age_a_admission < 18, '00-17',
            age_a_admission < 45, '18-44',
            age_a_admission < 65, '45-64',
            age_a_admission < 75, '65-74',
            age_a_admission < 85, '75-84',
                                  '85+') AS tranche_age,
    jours_depuis_sortie_precedente,
    mode_sortie_precedent,
    service_precedent,
    -- Réadmission : le patient est ressorti, puis revenu dans la fenêtre.
    --   écart négatif  → les séjours se chevauchent, ce n'est pas un retour
    --   mutation / transfert → le patient a changé de service ou d'établissement,
    --                          il n'est jamais rentré chez lui
    --   décès → un retour est impossible ; c'est une incohérence, pas un retour
    toUInt8(jours_depuis_sortie_precedente BETWEEN 0 AND {fenetre:UInt16}
            AND mode_sortie_precedent NOT IN ('deces', 'mutation', 'transfert')
            ) AS est_readmission_30j,
    toUInt8(jours_depuis_sortie_precedente < 0) AS est_chevauchant,
    toUInt8(mode_sortie_precedent = 'deces')    AS est_apres_deces,
    {b:String}
FROM (
    SELECT
        v.stay_id, v.patient_key, v.service_code, v.admission_ts, v.discharge_ts,
        v.admission_mode, v.discharge_mode,
        if(v.discharge_ts IS NULL, NULL,
           toInt32(dateDiff('day', v.admission_ts, v.discharge_ts))) AS duree_jours,
        toUInt8(v.discharge_ts IS NULL) AS est_en_cours,
        -- Âge ATTEINT dans l'année de l'admission : la date de naissance a été
        -- généralisée à l'année dès l'entrée du lake. Biais de +0,5 an en moyenne.
        toInt16(toYear(v.admission_ts)) - toInt16(p.birth_year) AS age_a_admission,
        -- Sortie du séjour précédent du même patient, dans l'ordre chronologique.
        toInt32OrNull(toString(dateDiff('day',
            lagInFrame(v.discharge_ts) OVER (
                PARTITION BY v.patient_key ORDER BY v.admission_ts ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
            v.admission_ts))) AS jours_depuis_sortie_precedente,
        lagInFrame(v.discharge_mode) OVER (
            PARTITION BY v.patient_key ORDER BY v.admission_ts ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS mode_sortie_precedent,
        lagInFrame(v.service_code) OVER (
            PARTITION BY v.patient_key ORDER BY v.admission_ts ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS service_precedent
    FROM (
        SELECT stay_id,
               argMax(patient_key, _ingestion_date)    AS patient_key,
               argMax(service_code, _ingestion_date)   AS service_code,
               argMax(admission_ts, _ingestion_date)   AS admission_ts,
               argMax(discharge_ts, _ingestion_date)   AS discharge_ts,
               argMax(admission_mode, _ingestion_date) AS admission_mode,
               argMax(discharge_mode, _ingestion_date) AS discharge_mode
        FROM bronze.sejours AS b
        WHERE b.admission_ts IS NOT NULL
          AND (b.discharge_ts IS NULL OR b.discharge_ts >= b.admission_ts)
          AND b.service_code IN (SELECT service_code FROM silver.dim_service)
          AND b.patient_key  IN (SELECT patient_key  FROM silver.dim_patient)
        GROUP BY stay_id
    ) AS v
    INNER JOIN silver.dim_patient AS p ON p.patient_key = v.patient_key
);

-- ─── fait_diagnostic ────────────────────────────────────────────────────────
-- Le JSON imbriqué est aplati ici, par le moteur : une ligne par code posé.

INSERT INTO ops.rejects (run_id, table_source, cle, regle, valeur)
SELECT {b:String}, 'fait_diagnostic', concat(stay_id, '/', code),
       if(stay_id NOT IN (SELECT stay_id FROM silver.fait_sejour),
          'sejour_inconnu', 'code_cim10_inconnu'),
       code
FROM (
    -- Accès par NOM et non par position : d.1 et d.2 désigneraient des rangs,
    -- et une réorganisation du tuple dans 10_bronze.sql inverserait le code et
    -- le type sans lever la moindre erreur.
    SELECT stay_id, d.code_cim10 AS code
    FROM bronze.diagnostics ARRAY JOIN diagnostics AS d
)
WHERE stay_id NOT IN (SELECT stay_id FROM silver.fait_sejour)
   OR code   NOT IN (SELECT code_cim10 FROM silver.dim_cim10);

INSERT INTO silver.fait_diagnostic
    (stay_id, patient_key, code_cim10, type_diagnostic, est_principal, _batch_id)
SELECT a.stay_id, s.patient_key, a.code, a.type_diagnostic,
       toUInt8(a.type_diagnostic = 'principal'), {b:String}
FROM (
    -- Accès par nom, pour la même raison. `type_diagnostic` plutôt que `type` :
    -- TYPE est un mot-clé du langage, et l'alias porte ainsi le nom de la
    -- colonne qu'il alimente.
    SELECT stay_id, d.code_cim10 AS code, d.type AS type_diagnostic
    FROM bronze.diagnostics ARRAY JOIN diagnostics AS d
) AS a
INNER JOIN silver.fait_sejour AS s ON s.stay_id = a.stay_id
WHERE a.code IN (SELECT code_cim10 FROM silver.dim_cim10);

-- ─── fait_monitoring ────────────────────────────────────────────────────────
-- Deux contrôles à ne pas confondre :
--   hors BORNES physiologiques  → la valeur est fausse, la ligne est écartée
--   hors SEUILS cliniques       → le patient va mal, la ligne est conservée
--                                 et marquée est_alerte = 1

INSERT INTO ops.rejects (run_id, table_source, cle, regle, valeur)
SELECT {b:String}, 'fait_monitoring', concat(stay_id, '@', toString(ts)),
       multiIf(heart_rate IS NULL OR spo2 IS NULL OR temp_c IS NULL, 'mesure_absente',
               heart_rate NOT BETWEEN {fc_min:Int32} AND {fc_max:Int32},   'fc_hors_bornes',
               spo2 NOT BETWEEN {spo2_min:Int32} AND {spo2_max:Int32},     'spo2_hors_bornes',
               temp_c NOT BETWEEN {temp_min:Float32} AND {temp_max:Float32}, 'temp_hors_bornes',
                                                                           'sejour_inconnu'),
       concat('fc=', toString(heart_rate), ' spo2=', toString(spo2), ' temp=', toString(temp_c))
FROM bronze.monitoring
WHERE heart_rate IS NULL OR spo2 IS NULL OR temp_c IS NULL
   OR heart_rate NOT BETWEEN {fc_min:Int32} AND {fc_max:Int32}
   OR spo2       NOT BETWEEN {spo2_min:Int32} AND {spo2_max:Int32}
   OR temp_c     NOT BETWEEN {temp_min:Float32} AND {temp_max:Float32}
   OR stay_id NOT IN (SELECT stay_id FROM silver.fait_sejour);

INSERT INTO silver.fait_monitoring
    (stay_id, service_code, ts, heart_rate, spo2, temp_c, est_alerte, motif_alerte, _batch_id)
SELECT m.stay_id, s.service_code, m.ts, m.heart_rate, m.spo2, m.temp_c,
       toUInt8(m.heart_rate < {a_fc_bas:Int32} OR m.heart_rate > {a_fc_haut:Int32}
               OR m.spo2 < {a_spo2_bas:Int32}
               OR m.temp_c > {a_temp_haut:Float32}) AS est_alerte,
       multiIf(m.heart_rate < {a_fc_bas:Int32},   'bradycardie',
               m.heart_rate > {a_fc_haut:Int32},  'tachycardie',
               m.spo2 < {a_spo2_bas:Int32},       'hypoxemie',
               m.temp_c > {a_temp_haut:Float32},  'fievre',
                                                  '') AS motif_alerte,
       {b:String}
FROM bronze.monitoring AS m
INNER JOIN silver.fait_sejour AS s ON s.stay_id = m.stay_id
WHERE m.heart_rate IS NOT NULL AND m.spo2 IS NOT NULL AND m.temp_c IS NOT NULL
  AND m.heart_rate BETWEEN {fc_min:Int32} AND {fc_max:Int32}
  AND m.spo2       BETWEEN {spo2_min:Int32} AND {spo2_max:Int32}
  AND m.temp_c     BETWEEN {temp_min:Float32} AND {temp_max:Float32};

-- ─── Bilan qualité ──────────────────────────────────────────────────────────
-- Une ligne par règle : combien de lignes examinées, combien écartées.

INSERT INTO ops.data_quality
    (run_id, table_cible, regle, traitement, lignes_entree, lignes_concernees)
SELECT {b:String}, table_source, regle, 'REJET',
       multiIf(table_source = 'dim_patient',     (SELECT count() FROM bronze.patients),
               table_source = 'fait_sejour',     (SELECT count() FROM bronze.sejours),
               table_source = 'fait_monitoring', (SELECT count() FROM bronze.monitoring),
               (SELECT sum(length(diagnostics)) FROM bronze.diagnostics)),
       count()
FROM ops.rejects
WHERE run_id = {b:String}
GROUP BY table_source, regle;

-- Anomalies conservées et signalées. Elles portent sur la relation entre deux
-- séjours : aucune ligne prise isolément n'est fausse, donc aucune ne peut être
-- écartée sans en fabriquer une autre.
INSERT INTO ops.data_quality
    (run_id, table_cible, regle, traitement, lignes_entree, lignes_concernees)
SELECT {b:String}, 'fait_sejour', 'sejours_chevauchants', 'SIGNALEMENT',
       count(), countIf(est_chevauchant = 1) FROM silver.fait_sejour;

INSERT INTO ops.data_quality
    (run_id, table_cible, regle, traitement, lignes_entree, lignes_concernees)
SELECT {b:String}, 'fait_sejour', 'admission_apres_deces', 'SIGNALEMENT',
       count(), countIf(est_apres_deces = 1) FROM silver.fait_sejour;

INSERT INTO ops.data_quality
    (run_id, table_cible, regle, traitement, lignes_entree, lignes_concernees)
SELECT {b:String}, 'fait_monitoring', 'releve_en_alerte', 'SIGNALEMENT',
       count(), countIf(est_alerte = 1) FROM silver.fait_monitoring;

INSERT INTO ops.data_quality
    (run_id, table_cible, regle, traitement, lignes_entree, lignes_concernees)
SELECT {b:String}, 'fait_sejour', 'sejour_en_cours', 'SIGNALEMENT',
       count(), countIf(est_en_cours = 1) FROM silver.fait_sejour;
