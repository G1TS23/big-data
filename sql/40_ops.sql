-- Couche d'exploitation : tout ce qui permet de savoir d'où vient une donnée,
-- quand elle a été traitée, et ce qui a été écarté.
--
-- Créée avant toute donnée métier : la traçabilité n'est pas un ajout tardif,
-- c'est la première table du projet.

CREATE DATABASE IF NOT EXISTS ops;

-- Un enregistrement par exécution de l'orchestrateur.
CREATE TABLE IF NOT EXISTS ops.run_log
(
    run_id        String,
    command       String,
    started_at    DateTime64(3, 'Europe/Paris'),
    finished_at   Nullable(DateTime64(3, 'Europe/Paris')),
    status        Enum8('RUNNING' = 1, 'OK' = 2, 'PARTIAL' = 3, 'FAILED' = 4),
    deposits_seen      UInt32 DEFAULT 0,
    deposits_ingested  UInt32 DEFAULT 0,
    deposits_skipped   UInt32 DEFAULT 0,
    deposits_quarantined UInt32 DEFAULT 0,
    message       String DEFAULT '',
    -- Version de la ligne : un run est inséré à l'ouverture puis réinséré à la
    -- clôture. ReplacingMergeTree ne conserve que la version la plus récente.
    updated_at    DateTime64(3, 'Europe/Paris') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY run_id;

-- Un enregistrement par fichier recopié dans le lake.
-- src_sha256 porte l'idempotence : un fichier déjà ingéré à l'identique est ignoré.
CREATE TABLE IF NOT EXISTS ops.ingestion_log
(
    run_id        String,
    source        LowCardinality(String),
    deposit_date  Date,
    src_path      String,
    src_sha256    FixedString(64),
    lake_path     String,
    lake_sha256   String,
    rows_in       UInt64 DEFAULT 0,
    rows_out      UInt64 DEFAULT 0,
    bytes_in      UInt64 DEFAULT 0,
    status        Enum8('OK' = 1, 'QUARANTINE' = 2, 'SKIPPED' = 3),
    reason        String DEFAULT '',
    ingested_at   DateTime64(3, 'Europe/Paris')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(deposit_date)
ORDER BY (source, deposit_date, src_sha256);

-- Une ligne par enregistrement écarté par un contrôle qualité (couche silver).
CREATE TABLE IF NOT EXISTS ops.rejects
(
    run_id        String,
    table_source  LowCardinality(String),
    cle           String,
    regle         LowCardinality(String),
    valeur        String DEFAULT '',
    rejected_at   DateTime64(3, 'Europe/Paris') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(rejected_at)
ORDER BY (table_source, regle, run_id);

-- Bilan chiffré par règle et par exécution : alimente le tableau de bord
-- d'exploitation et le chapitre « qualité » du dossier.
CREATE TABLE IF NOT EXISTS ops.data_quality
(
    run_id          String,
    table_cible     LowCardinality(String),
    regle           LowCardinality(String),
    lignes_entree   UInt64,
    lignes_rejetees UInt64,
    taux_rejet      Float64 MATERIALIZED if(lignes_entree = 0, 0, lignes_rejetees / lignes_entree),
    mesure_at       DateTime64(3, 'Europe/Paris') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (table_cible, regle, run_id);

-- Un enregistrement par fichier chargé du lake vers bronze.
CREATE TABLE IF NOT EXISTS ops.load_log
(
    run_id        String,
    source        LowCardinality(String),
    deposit_date  Date,
    target_table  LowCardinality(String),
    lake_path     String,
    rows_loaded   UInt64,
    bytes_read    UInt64,
    duration_ms   UInt32,
    status        Enum8('OK' = 1, 'FAILED' = 2),
    message       String DEFAULT '',
    loaded_at     DateTime64(3, 'Europe/Paris')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(deposit_date)
ORDER BY (target_table, deposit_date, run_id);
