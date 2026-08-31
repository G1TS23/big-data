SELECT service_label AS "Service",
       sum(dont_urgences)  AS "Urgence",
       sum(dont_programme) AS "Programmé",
       sum(dont_mutation)  AS "Mutation"
FROM gold_pilotage.kpi_activite_jour
GROUP BY service_label ORDER BY service_label
