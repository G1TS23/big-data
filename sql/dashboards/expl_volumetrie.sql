SELECT source AS "Flux", toString(deposit_date) AS "Dépôt", sum(rows_loaded) AS "Lignes"
FROM ops.load_log WHERE status = 'OK'
GROUP BY source, deposit_date ORDER BY deposit_date, source
