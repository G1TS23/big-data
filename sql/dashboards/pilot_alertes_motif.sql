SELECT jour AS "Jour",
       sumIf(releves_alerte, motif_alerte = 'bradycardie') AS "Bradycardie",
       sumIf(releves_alerte, motif_alerte = 'tachycardie') AS "Tachycardie",
       sumIf(releves_alerte, motif_alerte = 'hypoxemie')   AS "Hypoxémie",
       sumIf(releves_alerte, motif_alerte = 'fievre')      AS "Fièvre"
FROM gold_pilotage.kpi_alertes_jour
GROUP BY jour ORDER BY jour
