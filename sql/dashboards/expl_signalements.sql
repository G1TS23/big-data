SELECT concat(table_cible, ' · ', regle) AS "Anomalie signalée", lignes_concernees AS "Lignes"
FROM ops.data_quality
WHERE run_id = (SELECT run_id FROM ops.run_log FINAL
                WHERE command = 'silver' AND status = 'OK'
                ORDER BY started_at DESC LIMIT 1)
  AND traitement = 'SIGNALEMENT'
  -- Les readmission_exclue_* tracent la construction d'un indicateur, pas
  -- une anomalie des données : elles ont leur place dans ops, pas ici.
  AND regle NOT LIKE 'readmission_exclue_%'
ORDER BY lignes_concernees DESC
