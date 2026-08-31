SELECT libelle AS "Pathologie", duree_moyenne AS "Durée moyenne (jours)"
FROM gold_recherche.coh_duree_pathologie
ORDER BY duree_moyenne DESC
