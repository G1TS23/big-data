-- Présenté en TABLE et non en barres : deux de ces contrôles valent zéro sur le
-- jeu courant, et une barre de longueur nulle ne se voit pas. Le tableau montre
-- « 0 sur 6 046 », ce qui distingue un contrôle qui n'a rien trouvé d'un
-- contrôle qui n'existe plus.
SELECT concat(table_cible, ' · ', regle) AS "Contrôle",
       lignes_concernees                 AS "Concernées",
       lignes_entree                     AS "Examinées",
       round(100 * taux, 2)              AS "Part (%)"
FROM ops.data_quality
WHERE run_id = (SELECT run_id FROM ops.run_log FINAL
                WHERE command = 'silver' AND status = 'OK'
                ORDER BY started_at DESC LIMIT 1)
  AND traitement = 'SIGNALEMENT'
  -- Les readmission_exclue_* tracent la construction d'un indicateur, pas
  -- une anomalie des données : elles ont leur place dans ops, pas ici.
  AND regle NOT LIKE 'readmission_exclue_%'
ORDER BY lignes_concernees DESC
