-- Cloisonnement des usages.
--
-- Le pilotage et la recherche ne voient pas les mêmes données. Ce cloisonnement
-- est porté par le MOTEUR, pas par les tableaux de bord : un analyste qui se
-- connecte en SQL avec son compte se heurte aux mêmes murs que dans Metabase.
--
-- Aucun des deux rôles n'a le moindre droit sur lake, bronze, silver ni ops.
-- Ils ne voient que la couche gold correspondant à leur usage.

CREATE DATABASE IF NOT EXISTS gold_pilotage;
CREATE DATABASE IF NOT EXISTS gold_recherche;

CREATE ROLE IF NOT EXISTS role_pilotage;
CREATE ROLE IF NOT EXISTS role_recherche;

-- Révocation d'abord : rejouer ce script ne doit jamais élargir des droits.
REVOKE ALL ON *.* FROM role_pilotage;
REVOKE ALL ON *.* FROM role_recherche;

GRANT SELECT ON gold_pilotage.* TO role_pilotage;
GRANT SELECT ON gold_recherche.* TO role_recherche;
