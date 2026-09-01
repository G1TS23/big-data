-- Composition par sexe de chaque cohorte. Deux catégories sans ordre : deux
-- teintes distinctes, et non une rampe. Bleu et orange plutôt que rose et
-- bleu, qui reconduiraient un code de couleur sans fondement.
SELECT libelle AS "Pathologie",
       sumIf(patients, sex = 'F') AS "Femmes",
       sumIf(patients, sex = 'M') AS "Hommes"
FROM gold_recherche.coh_pathologie_sexe
GROUP BY libelle
ORDER BY libelle
