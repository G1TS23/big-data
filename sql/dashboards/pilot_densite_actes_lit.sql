-- Les services sans capacité connue sont écartés : une densité par lit ne se
-- calcule pas sans lits, et afficher zéro les ferait passer pour inactifs.
SELECT service_label AS "Service", actes_par_lit AS "Actes par lit"
FROM gold_pilotage.kpi_actes_service
WHERE actes_par_lit IS NOT NULL
ORDER BY actes_par_lit DESC
