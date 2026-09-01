-- gold_recherche — cohortes pour la recherche clinique.
--
-- Des VUES et non des tables, et ce choix porte toute la conformité.
--
-- Ces vues sont déclarées SQL SECURITY DEFINER : elles s'exécutent avec les
-- droits de leur définisseur, pas ceux de l'appelant. Le chercheur peut donc
-- les interroger, les filtrer, les joindre entre elles — mais il n'a AUCUN
-- droit sur silver. Le seuil de diffusion est à l'intérieur de la vue : il n'y
-- a pas de requête qui permette de le contourner.
--
-- Un tableau de bord qui masquerait les petits effectifs à l'affichage ne
-- protégerait rien : il suffirait de se connecter en SQL. Ici, la protection
-- est dans le moteur.
--
-- Seuil appliqué : k patients distincts (RGPD, petits effectifs), scellé dans
-- la définition de la vue par l'orchestrateur au moment de la création.
--
-- Il ne peut PAS être un paramètre de vue : ClickHouse permettrait alors
-- d'appeler coh_prevalence(k = 1) et de lever la protection depuis la requête.
-- Un garde-fou que l'appelant peut régler n'en est pas un.
-- uniqExact et non uniq : une approximation pourrait laisser passer une
-- cohorte de 4 patients ou en masquer une de 6.
--
-- Toute colonne projetée porte un alias EXPLICITE. Sans lui, ClickHouse nomme
-- « c.code_cim10 » la colonne issue d'une référence qualifiée, et le chercheur
-- doit écrire des accents graves autour du nom pour la filtrer. Une couche de
-- restitution ne doit pas exposer les alias de ses jointures.

CREATE DATABASE IF NOT EXISTS gold_recherche;

-- ─── Prévalence : taille des cohortes par pathologie ───────────────────────
CREATE OR REPLACE VIEW gold_recherche.coh_prevalence
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT c.code_cim10 AS code_cim10, c.libelle AS libelle,
       uniqExact(f.patient_key) AS patients,
       count() AS diagnostics,
       countIf(f.est_principal = 1) AS dont_principal
FROM silver.fait_diagnostic AS f
INNER JOIN silver.dim_cim10 AS c ON c.code_cim10 = f.code_cim10
GROUP BY c.code_cim10, c.libelle
HAVING uniqExact(f.patient_key) >= $$K_ANONYMITE$$;

-- ─── Description de cohorte : distribution par âge et sexe ─────────────────
CREATE OR REPLACE VIEW gold_recherche.coh_age_sexe
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT s.tranche_age, p.sex,
       uniqExact(s.patient_key) AS patients,
       count() AS sejours
FROM silver.fait_sejour AS s
INNER JOIN silver.dim_patient AS p ON p.patient_key = s.patient_key
GROUP BY s.tranche_age, p.sex
HAVING uniqExact(s.patient_key) >= $$K_ANONYMITE$$;

-- ─── Le croisement qui fait vraiment mordre le seuil ───────────────────────
-- Pathologie × tranche d'âge × sexe : c'est là que les cohortes deviennent
-- petites, donc là que la suppression protège réellement quelqu'un.
CREATE OR REPLACE VIEW gold_recherche.coh_pathologie_age_sexe
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT c.code_cim10 AS code_cim10, c.libelle AS libelle, f.tranche_age, p.sex,
       uniqExact(f.patient_key) AS patients
FROM silver.fait_diagnostic AS f
INNER JOIN silver.dim_patient AS p ON p.patient_key = f.patient_key
INNER JOIN silver.dim_cim10   AS c ON c.code_cim10 = f.code_cim10
GROUP BY c.code_cim10, c.libelle, f.tranche_age, p.sex
HAVING uniqExact(f.patient_key) >= $$K_ANONYMITE$$;

-- ─── Prévalence par tranche d'âge ──────────────────────────────────────────
-- Le croisement demandé le plus souvent : à quel âge rencontre-t-on telle
-- pathologie. Les cohortes y sont plus grandes qu'en ajoutant le sexe, donc
-- moins souvent masquées par le seuil de diffusion.
CREATE OR REPLACE VIEW gold_recherche.coh_pathologie_age
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT c.code_cim10 AS code_cim10, c.libelle AS libelle, f.tranche_age,
       uniqExact(f.patient_key) AS patients,
       countIf(f.est_principal = 1) AS dont_principal
FROM silver.fait_diagnostic AS f
INNER JOIN silver.dim_cim10 AS c ON c.code_cim10 = f.code_cim10
GROUP BY c.code_cim10, c.libelle, f.tranche_age
HAVING uniqExact(f.patient_key) >= $$K_ANONYMITE$$;

-- ─── Durée de séjour par pathologie ────────────────────────────────────────
CREATE OR REPLACE VIEW gold_recherche.coh_duree_pathologie
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT c.code_cim10 AS code_cim10, c.libelle AS libelle,
       uniqExact(s.patient_key) AS patients,
       count() AS sejours_clos,
       round(avg(s.duree_jours), 2) AS duree_moyenne,
       round(quantileExact(0.5)(s.duree_jours), 1) AS duree_mediane
FROM silver.fait_diagnostic AS f
INNER JOIN silver.fait_sejour AS s ON s.stay_id = f.stay_id
INNER JOIN silver.dim_cim10   AS c ON c.code_cim10 = f.code_cim10
WHERE f.est_principal = 1 AND s.est_en_cours = 0
GROUP BY c.code_cim10, c.libelle
HAVING uniqExact(s.patient_key) >= $$K_ANONYMITE$$;

-- ─── Comorbidités : pathologies qui apparaissent ensemble ──────────────────
-- Chaque paire n'est comptée qu'une fois, dans l'ordre alphabétique des codes.
CREATE OR REPLACE VIEW gold_recherche.coh_comorbidites
DEFINER = $$DEFINER$$ SQL SECURITY DEFINER AS
SELECT a.code_cim10 AS code_1, ca.libelle AS libelle_1,
       b.code_cim10 AS code_2, cb.libelle AS libelle_2,
       uniqExact(a.patient_key) AS patients
FROM silver.fait_diagnostic AS a
INNER JOIN silver.fait_diagnostic AS b
        ON b.stay_id = a.stay_id AND b.code_cim10 > a.code_cim10
INNER JOIN silver.dim_cim10 AS ca ON ca.code_cim10 = a.code_cim10
INNER JOIN silver.dim_cim10 AS cb ON cb.code_cim10 = b.code_cim10
GROUP BY code_1, libelle_1, code_2, libelle_2
HAVING uniqExact(a.patient_key) >= $$K_ANONYMITE$$;
