SELECT substring(run_id, 1, 8) AS "Run", command AS "Étape",
       started_at AS "Début", toString(status) AS "Statut",
       dateDiff('second', started_at, finished_at) AS "Durée (s)",
       deposits_ingested AS "Traités", deposits_quarantined AS "Quarantaine"
FROM ops.run_log FINAL
ORDER BY started_at DESC LIMIT 12
