-- Composition par âge des cohortes — Femmes.
--
-- Deux cartes en regard plutôt qu'une seule à douze séries : six tranches
-- d'âge se lisent, douze ne se distinguent plus. Les deux graphiques partagent
-- le même axe et la même rampe, si bien que la comparaison entre sexes se fait
-- d'un regard.
SELECT libelle AS "Pathologie",
       sumIf(patients, tranche_age = '00-17') AS "0-17 ans",
       sumIf(patients, tranche_age = '18-44') AS "18-44 ans",
       sumIf(patients, tranche_age = '45-64') AS "45-64 ans",
       sumIf(patients, tranche_age = '65-74') AS "65-74 ans",
       sumIf(patients, tranche_age = '75-84') AS "75-84 ans",
       sumIf(patients, tranche_age = '85+')   AS "85 ans et plus"
FROM gold_recherche.coh_pathologie_age_sexe
WHERE sex = 'F'
GROUP BY libelle
ORDER BY libelle
