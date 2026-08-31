-- Rattachée au service qui a laissé SORTIR le patient.
SELECT service_label AS "Service", round(100 * taux, 1) AS "Taux de réadmission (%)"
FROM gold_pilotage.kpi_readmission_service
ORDER BY taux DESC
