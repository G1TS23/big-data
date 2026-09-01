-- Composition par âge de chaque cohorte. Les tranches sont ORDONNÉES : elles
-- appellent une rampe d'une seule teinte, du clair au foncé, et non six
-- couleurs distinctes qui suggéreraient des catégories sans ordre.
SELECT libelle AS "Pathologie",
       sumIf(patients, tranche_age = '00-17') AS "0-17 ans",
       sumIf(patients, tranche_age = '18-44') AS "18-44 ans",
       sumIf(patients, tranche_age = '45-64') AS "45-64 ans",
       sumIf(patients, tranche_age = '65-74') AS "65-74 ans",
       sumIf(patients, tranche_age = '75-84') AS "75-84 ans",
       sumIf(patients, tranche_age = '85+')   AS "85 ans et plus"
FROM gold_recherche.coh_pathologie_age
GROUP BY libelle
ORDER BY libelle
