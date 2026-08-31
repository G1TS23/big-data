SELECT libelle_1 AS "Pathologie", libelle_2 AS "Associée à", patients AS "Patients"
FROM gold_recherche.coh_comorbidites
ORDER BY patients DESC LIMIT 15
