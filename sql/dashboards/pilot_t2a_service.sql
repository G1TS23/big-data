-- Somme des tarifs CCAM des actes réalisés, rattachés au service du séjour.
SELECT service_label AS "Service", montant_t2a AS "Montant facturé (€)"
FROM gold_pilotage.kpi_actes_service
ORDER BY montant_t2a DESC
