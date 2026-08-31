-- Pyramide des âges. L'âge est celui atteint dans l'année de l'admission :
-- la date de naissance est généralisée à l'année, d'où un écart possible d'un an.
SELECT tranche_age AS "Tranche d'âge",
       sumIf(patients, sex = 'F') AS "Femmes",
       sumIf(patients, sex = 'M') AS "Hommes"
FROM gold_recherche.coh_age_sexe
GROUP BY tranche_age ORDER BY tranche_age
