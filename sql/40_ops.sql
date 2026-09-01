-- Couche d'exploitation : tout ce qui permet de savoir d'où vient une donnée,
-- quand elle a été traitée, et ce qui a été écarté.
--
-- Créée avant toute donnée métier : la traçabilité n'est pas un ajout tardif,
-- c'est la première table du projet.

CREATE DATABASE IF NOT EXISTS ops;

-- Un enregistrement par exécution de l'orchestrateur.
--
-- Les étapes ne traitent pas la même matière : `lake` et `bronze` comptent des
-- dépôts, `silver` et `gold` des instructions SQL, `metabase` des cartes. La
-- colonne `unite` dit ce que les compteurs dénombrent — sans elle, « 28 traités »
-- sur une exécution de gold laisserait croire à vingt-huit fichiers.
CREATE TABLE IF NOT EXISTS ops.run_log
(
    run_id        String,
    command       String,
    started_at    DateTime64(3, 'Europe/Paris'),
    finished_at   Nullable(DateTime64(3, 'Europe/Paris')),
    status        Enum8('RUNNING' = 1, 'OK' = 2, 'PARTIAL' = 3, 'FAILED' = 4),
    unite         Enum8('dépôt' = 1, 'instruction' = 2, 'carte' = 3),
    objets_vus         UInt32 DEFAULT 0,
    objets_traites     UInt32 DEFAULT 0,
    objets_ignores     UInt32 DEFAULT 0,
    objets_quarantaine UInt32 DEFAULT 0,
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

-- Bilan chiffré par règle et par exécution.
--
-- `traitement` distingue deux situations que le tableau de bord ne doit jamais
-- confondre :
--   REJET        la ligne est fausse, elle n'entre pas dans silver
--   SIGNALEMENT  la ligne est conservée mais porte une anomalie détectée.
--                C'est le cas des incohérences qui portent sur la RELATION entre
--                deux enregistrements (séjours qui se chevauchent, admission
--                postérieure à un décès) : on ne peut pas savoir lequel des deux
--                est fautif, et en écarter un au hasard fabriquerait une erreur
--                au lieu d'en corriger une.
CREATE TABLE IF NOT EXISTS ops.data_quality
(
    run_id            String,
    table_cible       LowCardinality(String),
    regle             LowCardinality(String),
    traitement        Enum8('REJET' = 1, 'SIGNALEMENT' = 2),
    lignes_entree     UInt64,
    lignes_concernees UInt64,
    taux              Float64 MATERIALIZED if(lignes_entree = 0, 0, lignes_concernees / lignes_entree),
    mesure_at         DateTime64(3, 'Europe/Paris') DEFAULT now64(3)
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

-- Photographie des règles appliquées à chaque exécution.
-- Un indicateur n'est reproductible que si l'on sait avec quels seuils il a
-- été calculé : cette table lie un run_id aux valeurs qui l'ont produit.
CREATE TABLE IF NOT EXISTS ops.parametres
(
    run_id      String,
    nom         LowCardinality(String),
    valeur      String,
    applique_at DateTime64(3, 'Europe/Paris') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (run_id, nom);
