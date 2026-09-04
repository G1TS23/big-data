# Le seul écart assumé de la démonstration, et il est ici.
#
# Le défaut du code reste « francecentral », parce que c'est ce qu'exige
# l'hébergement de données de santé françaises. La souscription « Azure for
# Students » l'interdit : sa politique « Allowed resource deployment regions »
# (sys.regionrestriction) ne laisse que germanywestcentral, spaincentral,
# polandcentral, uaenorth et swedencentral. Toute création en France est
# refusée par un 403 RequestDisallowedByAzure — constaté, pas supposé.
#
# Des cinq régions permises, une seule convient :
#   uaenorth            hors EEE, écartée sans discussion
#   spaincentral        tous les gabarits en NotAvailableForSubscription
#   germanywestcentral  aucun gabarit de la famille B
#   polandcentral       non retenue, plus éloignée à service égal
#   swedencentral       Standard_B2s_v2 disponible, 6 vCPU de quota
#
# Stockholm est dans l'EEE : aucun transfert hors Union, le RGPD est tenu.
# Ce qui est perdu, c'est la certification HDS, dont le périmètre se limite
# aux deux régions françaises. Sur des données fictives, l'écart est sans
# conséquence. Sur de vraies données, il serait rédhibitoire — et c'est
# précisément ce que refuse la seconde validation de var.region dès que
# environnement vaut « production ».
#
# Voir docs/CLOUD.md, « Où sont les données ».
region = "swedencentral"
