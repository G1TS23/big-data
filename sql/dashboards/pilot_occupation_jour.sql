SELECT jour AS "Jour", sum(patients_presents) AS "Patients présents"
FROM gold_pilotage.kpi_occupation_jour
GROUP BY jour ORDER BY jour
