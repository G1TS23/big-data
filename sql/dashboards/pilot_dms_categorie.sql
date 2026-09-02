-- Séjours clos uniquement, comme la DMS par service.
SELECT categorie AS "Catégorie", dms_jours AS "DMS (jours)"
FROM gold_pilotage.kpi_activite_categorie
ORDER BY dms_jours DESC
