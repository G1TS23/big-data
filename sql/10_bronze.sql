-- Couche bronze : les fichiers du lake deviennent des tables typées.
--
-- Deux principes gouvernent ces définitions :
--
-- 1. FIDÉLITÉ. Bronze ne corrige rien. Les types sont volontairement permissifs
--    (Nullable, Int32 là où UInt8 suffirait) pour qu'une valeur aberrante soit
--    CHARGÉE puis écartée par la couche silver, avec traçage. Un type trop
--    strict ferait échouer l'insertion et masquerait l'anomalie.
--
-- 2. REJEU. Toutes les tables sont partitionnées par _ingestion_date, c'est-à-dire
--    par dépôt. Réingérer une journée revient à supprimer sa partition puis à la
--    réinsérer : l'opération est idempotente et ne touche pas les autres jours.
--    Le partitionnement métier (par mois d'admission, par jour de relevé) est
--    l'affaire de silver, dont le rôle est d'être interrogé.

CREATE DATABASE IF NOT EXISTS bronze;

-- Colonnes techniques présentes partout — c'est le fil de traçabilité :
--   _batch_id → ops.run_log → ops.ingestion_log → fichier source et son empreinte
--
-- Elles sont en LowCardinality : un dépôt ne contient qu'un seul chemin source
-- et un seul identifiant de lot, répétés sur toutes ses lignes. Stockés en
-- String bruts, ils pesaient plus lourd que les mesures elles-mêmes. Le
-- dictionnaire par partition ramène chaque colonne à une poignée d'octets.

CREATE TABLE IF NOT EXISTS bronze.patients
(
    -- Pseudonyme de 32 caractères hexadécimaux. String et non FixedString(32) :
    -- une valeur vide serait complétée par des octets nuls et deviendrait une
    -- fausse clé indétectable. Ici elle reste vide, donc visible pour silver.
    patient_key     String,
    birth_year      Nullable(UInt16),
    sex             LowCardinality(String),
    region_code     LowCardinality(String),

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY patient_key;

CREATE TABLE IF NOT EXISTS bronze.sejours
(
    stay_id         String,
    patient_key     String,
    service_code    LowCardinality(String),
    admission_ts    Nullable(DateTime),
    -- Nullable et non « erreur » : une sortie vide est un séjour en cours.
    discharge_ts    Nullable(DateTime),
    admission_mode  LowCardinality(String),
    discharge_mode  LowCardinality(String),

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY stay_id;

-- Le JSON est chargé tel qu'il arrive, imbriqué. L'aplatissement des codes
-- se fera en SQL (arrayJoin) au passage en silver : la transformation reste
-- dans le moteur, et bronze demeure une image fidèle du fichier.
CREATE TABLE IF NOT EXISTS bronze.diagnostics
(
    stay_id         String,
    diagnostics     Array(Tuple(code_cim10 String, type String)),

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY stay_id;

-- Le flux volumineux. ORDER BY (stay_id, ts) regroupe les relevés d'un même
-- séjour dans l'ordre chronologique : les valeurs voisines se ressemblent, ce
-- dont les codecs delta tirent parti.
--   DoubleDelta : horodatages à pas régulier → l'écart des écarts est nul
--   T64         : entiers de faible amplitude → transposition sur 64 bits
--   Gorilla     : flottants qui varient peu d'une mesure à l'autre
CREATE TABLE IF NOT EXISTS bronze.monitoring
(
    stay_id         String,
    ts              DateTime          CODEC(DoubleDelta, ZSTD(1)),
    -- Int32 alors que 0–500 tiendrait dans UInt16 : une valeur négative doit
    -- pouvoir entrer pour que silver la rejette explicitement.
    heart_rate      Nullable(Int32)   CODEC(T64, ZSTD(1)),
    spo2            Nullable(Int32)   CODEC(T64, ZSTD(1)),
    temp_c          Nullable(Float32) CODEC(Gorilla, ZSTD(1)),

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY (stay_id, ts);

-- Nomenclatures. Déposées le premier jour, rechargées intégralement si le CHU
-- les redépose : silver retiendra la version au _ingestion_date le plus récent.
CREATE TABLE IF NOT EXISTS bronze.services
(
    service_code    LowCardinality(String),
    service_label   String,

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY service_code;

CREATE TABLE IF NOT EXISTS bronze.cim10
(
    code_cim10      LowCardinality(String),
    libelle         String,

    _source_file    LowCardinality(String) CODEC(ZSTD(1)),
    _ingestion_date Date                  CODEC(ZSTD(1)),
    _batch_id       LowCardinality(String) CODEC(ZSTD(1)),
    _loaded_at      DateTime DEFAULT now() CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY _ingestion_date
ORDER BY code_cim10;
