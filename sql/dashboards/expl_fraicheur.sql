SELECT dateDiff('hour', max(ingested_at), now()) AS "Heures depuis le dernier dépôt"
FROM ops.ingestion_log WHERE status = 'OK'
