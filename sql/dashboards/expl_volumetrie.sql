-- argMax, et non sum : bronze efface la partition du jour avant de la
-- recharger, si bien qu'un dépôt rechargé vingt fois n'a qu'un seul contenu.
-- Sommer les chargements successifs multiplierait la volumétrie par le nombre
-- d'exécutions — 40 848 lignes affichées pour 1 776 réelles, sans qu'aucune
-- erreur ne se lève.
SELECT source AS "Flux", toString(deposit_date) AS "Dépôt",
       argMax(rows_loaded, loaded_at) AS "Lignes"
FROM ops.load_log
WHERE status = 'OK'
GROUP BY source, deposit_date
ORDER BY deposit_date, source
