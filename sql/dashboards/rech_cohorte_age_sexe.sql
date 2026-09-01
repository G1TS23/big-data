-- Description d'UNE cohorte : la pathologie est choisie par le filtre du
-- tableau de bord. Une distribution âge/sexe tous diagnostics confondus ne
-- décrirait aucune cohorte, et le sujet demande bien « description de cohorte ».
SELECT tranche_age AS "Tranche d'âge",
       sumIf(patients, sex = 'F') AS "Femmes",
       sumIf(patients, sex = 'M') AS "Hommes"
FROM gold_recherche.coh_pathologie_age_sexe
WHERE code_cim10 = {{pathologie}}
GROUP BY tranche_age
ORDER BY tranche_age
