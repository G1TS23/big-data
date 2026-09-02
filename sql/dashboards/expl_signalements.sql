-- Les contrôles à zéro sont écartés de l'AFFICHAGE, pas de la mesure : ils
-- tournent à chaque exécution et restent dans ops.data_quality, où le tableau
-- de bord ne les montre simplement plus tant qu'ils ne trouvent rien.
SELECT concat(table_cible, ' · ', regle) AS "Anomalie signalée", lignes_concernees AS "Lignes"
FROM ops.data_quality
WHERE run_id = (SELECT run_id FROM ops.run_log FINAL
                WHERE command = 'silver' AND status = 'OK'
                ORDER BY started_at DESC LIMIT 1)
  AND traitement = 'SIGNALEMENT'
  -- Les readmission_exclue_* tracent la construction d'un indicateur, pas
  -- une anomalie des données : elles ont leur place dans ops, pas ici.
  AND regle NOT LIKE 'readmission_exclue_%'
  AND lignes_concernees > 0
ORDER BY lignes_concernees DESC
