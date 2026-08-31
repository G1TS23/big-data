-- Séjours clos uniquement : un séjour en cours a une durée tronquée.
SELECT service_label AS "Service", dms_jours AS "DMS (jours)"
FROM gold_pilotage.kpi_dms_service
ORDER BY dms_jours DESC
