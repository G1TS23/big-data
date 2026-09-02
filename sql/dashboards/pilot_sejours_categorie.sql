-- La catégorie regroupe plusieurs services. « non décrit » n'est pas un défaut
-- d'affichage : c'est un service que le référentiel de description ne couvre
-- pas, et que l'on préfère montrer plutôt que d'escamoter.
SELECT categorie AS "Catégorie", sejours AS "Séjours"
FROM gold_pilotage.kpi_activite_categorie
ORDER BY sejours DESC
