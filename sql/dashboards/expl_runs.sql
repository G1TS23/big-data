-- `unite` dit ce que compte la colonne « Traités » : une exécution de gold
-- traite des instructions SQL, pas des dépôts de fichiers.
SELECT substring(run_id, 1, 8) AS "Run", command AS "Étape",
       started_at AS "Début", toString(status) AS "Statut",
       dateDiff('second', started_at, finished_at) AS "Durée (s)",
       concat(toString(objets_traites), ' ', toString(unite),
              if(objets_traites > 1, 's', '')) AS "Traités",
       objets_quarantaine AS "Quarantaine"
FROM ops.run_log FINAL
ORDER BY started_at DESC LIMIT 12
