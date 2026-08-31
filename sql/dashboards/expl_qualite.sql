SELECT concat(table_cible, ' · ', regle) AS "Contrôle", lignes_concernees AS "Lignes"
FROM ops.data_quality
WHERE run_id = (SELECT run_id FROM ops.run_log FINAL
                WHERE command = 'silver' AND status = 'OK'
                ORDER BY started_at DESC LIMIT 1)
  AND traitement = 'REJET'
ORDER BY lignes_concernees DESC
