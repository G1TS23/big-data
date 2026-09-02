-- Le service vient du SÉJOUR, dénormalisé sur fait_acte : aucune jointure entre
-- deux tables de faits n'est nécessaire ici.
SELECT service_label AS "Service", actes AS "Actes"
FROM gold_pilotage.kpi_actes_service
ORDER BY actes DESC
