# Validation des chiffres

Ce document répond à une seule question : **peut-on justifier chaque chiffre
affiché sur les tableaux de bord ?**

Il procède par mesure indépendante. Tous les nombres qui suivent ont été
recalculés à partir des fichiers déposés par le CHU, avec du code écrit pour
l'occasion — `csv`, `json`, `pyarrow` — qui n'importe pas une ligne du pipeline.
Deux mesures indépendantes qui concordent valent mieux qu'une mesure répétée
deux fois.

- [1. Ce que le CHU dépose](#1-ce-que-le-chu-dépose)
- [2. Réconciliation source ↔ entrepôt](#2-réconciliation-source--entrepôt)
- [3. Ce que chaque écart recouvre](#3-ce-que-chaque-écart-recouvre)
- [4. Anomalies conservées et signalées](#4-anomalies-conservées-et-signalées)
- [5. Le seuil de diffusion, éprouvé](#5-le-seuil-de-diffusion-éprouvé)
- [6. Recalcul des sept indicateurs de synthèse](#7-recalcul-des-sept-indicateurs-de-synthèse)
- [7. Confrontation au corrigé du commanditaire](#8-confrontation-au-corrigé-du-commanditaire)
- [8. Recalcul détaillé sur trois séjours](#9-recalcul-détaillé-sur-trois-séjours)
- [9. Ce que le recalcul a corrigé](#10-ce-que-le-recalcul-a-corrigé)
- [10. Test de rejeu](#11-test-de-rejeu)
- [11. Ce que cette validation ne couvre pas](#12-ce-que-cette-validation-ne-couvre-pas)

---

## 1. Ce que le CHU dépose

Chaque flux a **son propre calendrier**, ce qui compte pour qui veut recompter :
parcourir les dates d'un flux pour en lire un autre n'en lirait qu'une partie,
sans erreur visible.

| flux | dépôts | période | volume |
|---|---:|---|---:|
| `sejours` | 28 | 2026-08-01 → 08-28 | 6 797 lignes |
| `diagnostics` | 28 | 2026-08-01 → 08-28 | 6 797 objets, 12 720 codes |
| `monitoring` | 28 | 2026-08-01 → 08-28 | 41 778 relevés |
| `patients` | 3 | 2026-08-26 → 08-28 | 3 × 6 000 = 18 000 lignes |
| `referentiels` | 1 | 2026-08-01 | 8 services, 13 codes CIM-10 |
| `actes` | 1 | 2026-08-29 | 8 112 actes médicaux |
| `referentiels` (évolution) | 1 | 2026-08-29 | 7 services décrits, 8 actes CCAM |

92 fichiers, 3,3 Mo, versionnés dans `source-filestorage/`. Les séjours,
diagnostics et relevés sont **incrémentaux** : 6 797 identifiants de séjour pour
6 797 lignes, aucun ne revient d'un jour sur l'autre. Les patients sont livrés
en **instantanés complets**, les mêmes 6 000 patients trois fois.

**Les trois derniers dépôts sont partiels**, et cela se voit sur tout indicateur
exprimé par jour :

| dépôt | 08-01 … 08-25 | 08-26 | 08-27 | 08-28 |
|---|---:|---:|---:|---:|
| séjours reçus | ~250 à 300 par jour | 40 | 51 | 63 |

Les courbes d'activité, d'urgences et d'occupation décrochent donc sur les trois
derniers jours. Ce décrochage appartient à la livraison, pas à l'hôpital : il ne
doit pas se lire comme une baisse d'activité.

---

## 2. Réconciliation source ↔ entrepôt

L'exigence : **aucune ligne ne disparaît sans être comptée.** Pour chaque table,

```
lignes source = lignes silver + rejets + doublons écartés
```

À rejouer à tout moment — le script sort en erreur si une équation ne se ferme
pas :

```bash
python docs/outils/reconcilier.py
```

| table | source | bronze | silver | rejets | doublons | équation |
|---|---:|---:|---:|---:|---:|:--:|
| patients | 18 000 | 18 000 | 6 000 | 0 | 12 000 | ✓ |
| sejours | 6 797 | 6 797 | 6 729 | 68 | 0 | ✓ |
| diagnostics | 12 720 | 12 720 | 12 720 | 0 | 0 | ✓ |
| monitoring | 41 778 | 41 778 | 40 920 | 858 | 0 | ✓ |
| actes | 8 112 | 8 112 | 8 112 | 0 | 0 | ✓ |
| services | 8 | 8 | 8 | 0 | 0 | ✓ |
| cim10 | 13 | 13 | 13 | 0 | 0 | ✓ |
| ccam | 8 | 8 | 8 | 0 | 0 | ✓ |
| description_service | 7 | 7 | 7 | 0 | 0 | ✓ |

`description_service` n'a pas de table à elle : elle enrichit `dim_service`. Son
« silver » est donc le nombre de services effectivement décrits — 7 sur 8.

**Bronze reproduit la source ligne pour ligne**, sur les six tables. C'est
l'engagement de la couche : elle recopie sans juger. La ligne `diagnostics`
compte les codes dépliés (`sum(length(diagnostics))`) et non les lignes — bronze
conserve le JSON imbriqué tel qu'il arrive, soit 6 797 objets portant 12 720
codes, dépliés par le moteur en silver.

---

## 3. Ce que chaque écart recouvre

### Patients : 18 000 → 6 000 (12 000 doublons)

Les trois dépôts de patients sont des **instantanés complets**, non des deltas :
6 000 lignes chacun, et l'union des trois ne porte que **6 000 identifiants
distincts**, exactement le contenu de `dim_patient`. Les 12 000 lignes d'écart
sont de la répétition, pas de la perte.

`dim_patient` conserve la version la plus récente de chaque patient
(`argMax(..., _ingestion_date)`) : si une correction arrive dans un dépôt
ultérieur, c'est elle qui l'emporte.

### Séjours : 68 rejets, tous de la même règle

Les 68 rejets portent le motif `sortie_avant_admission`. Aucun autre motif ne se
déclenche : pas d'admission absente, pas de service inconnu, pas de patient
inconnu. Le référentiel des services couvre l'intégralité des séjours, et tous
les `patient_id` cités trouvent leur patient.

### Diagnostics : aucun rejet, et un rattachement à bronze

Les 12 720 codes passent intégralement en silver. C'est un choix explicite, et
il mérite d'être défendu.

Un diagnostic ne se rattache **pas** à `silver.fait_sejour` mais à
`silver.sejour_recevable`, une vue sur bronze qui ne demande que deux choses :
un séjour identifiable et un patient connu. Elle ne juge pas les dates.

La raison : **une faute de saisie sur une date de sortie n'invalide pas le
diagnostic.** Le patient a bien été hospitalisé, le code a bien été posé par un
médecin. Les 68 séjours écartés pour `sortie_avant_admission` portent 127 codes
cliniques ; les joindre au fait épuré les aurait fait disparaître pour une
erreur qui ne les concerne pas. Ces 68 séjours sont d'ailleurs sains par
ailleurs — patient connu, service connu, modes d'entrée et de sortie
renseignés — et leur sortie n'est antérieure que de 0 jour (53 cas, même
journée) ou 1 jour (15 cas).

**La contrepartie est réelle et mesurée.** Ces 127 codes portent un `stay_id`
absent de `fait_sejour` : toute requête joignant les deux faits les perdra. Le
contrôle `diagnostic_sans_sejour_retenu` les compte à chaque exécution, et un
test vérifie que le compte est exact — la perte est lue, pas subie.

Concrètement, sur les six vues de recherche, une seule joint `fait_sejour` :
`coh_duree_pathologie`, qui a besoin d'une durée. Elle écarte donc ces 127
codes, ce qui est correct : sans date de sortie exploitable, il n'y a pas de
durée à moyenner. Les cinq autres vues les intègrent.

### Monitoring : 858 rejets, un seul motif

Seuls les relevés **hors bornes physiologiques** sont écartés : 858 fréquences
cardiaques hors de 20–250 bpm, où la valeur est fausse (capteur, saisie). Aucun
relevé n'est écarté pour une SpO2 ou une température hors bornes.

Comme les diagnostics, les relevés se rattachent à `silver.sejour_recevable` et
non à `fait_sejour` : **une constante mesurée au chevet du patient reste vraie
quand la date de sortie du séjour est fautive.** 520 relevés appartiennent aux
68 séjours écartés ; les joindre au fait épuré les aurait supprimés. Le contrôle
`releve_sans_sejour_retenu` les compte, comme son équivalent pour les
diagnostics.

> La distinction est structurante. Une valeur **hors bornes physiologiques** est
> fausse : elle est écartée. Une valeur **hors seuils cliniques** est vraie mais
> mauvaise pour le patient : elle est conservée et marquée `est_alerte = 1`.
> Confondre les deux reviendrait à supprimer les patients qui vont mal.

---

## 4. Anomalies conservées et signalées

Rejeter une ligne, c'est décider qu'elle est fausse. Certaines anomalies ne le
sont pas : elles sont vraies, gênantes, et les écarter fabriquerait une erreur au
lieu d'en corriger une. Elles sont donc **conservées et signalées** dans
`ops.data_quality`, que `eds status` et le tableau de bord Exploitation donnent à
voir.

| contrôle | concernées | base | part |
|---|---:|---:|---:|
| `sejour_en_cours` | 683 | 6 729 | 10,2 % |
| `admission_apres_deces` | 133 | 6 729 | 2,0 % |
| `releve_en_alerte` | 3 314 | 40 920 | 8,1 % |
| `sejours_chevauchants` | **0** | 6 729 | 0 % |
| `diagnostic_sans_sejour_retenu` | 127 | 12 720 | 1,0 % |
| `releve_sans_sejour_retenu` | 520 | 40 920 | 1,3 % |
| `acte_sans_sejour_retenu` | 82 | 8 112 | 1,0 % |
| `service_sans_description` | 1 | 8 | 12,5 % |
| `mode_sortie_manquant` | **0** | 6 046 clos | 0 % |

Les deux derniers valent zéro sur ce jeu, et **les contrôles restent en place**.
Une livraison antérieure présentait 53,7 % de séjours chevauchants et 14,4 % de
modes de sortie manquants ; la mesure est ce qui a permis de le dire. La
supprimer parce qu'elle affiche zéro rendrait indistinguables « aucune anomalie »
et « plus personne ne regarde ».

### Les retours après un décès

133 séjours suivent un séjour terminé par un décès, et **tous tombent dans la
fenêtre des 30 jours** : ils seraient comptés comme des réadmissions. La clause
`mode_sortie_precedent NOT IN ('deces', ...)` les écarte — `est_readmission_30j`
vaut 0 pour l'intégralité d'entre eux.

Un retour après un décès est impossible ; un retour après mutation ou transfert
n'est pas un retour, le patient n'étant jamais rentré chez lui. Les deux
exclusions sont consignées, si bien que **le numérateur publié se reconstitue
sans lire une ligne de SQL** :

```
retours dans la fenêtre de 30 jours                780
  − retours après un décès           (écartés)   − 133
  − retours après mutation/transfert (écartés)   − 255
                                                 ─────
  = réadmissions publiées                          392
```

Un test le vérifie à chaque exécution
(`tests/test_reconciliation.py::TestAnomaliesAttendues`).

---

## 5. Le seuil de diffusion, éprouvé

Les vues de recherche n'exposent une cohorte que si elle compte au moins
**k = 5 patients** (`EDS_K_ANONYMITE`). En deçà, un effectif suffit à
ré-identifier : dans un CHU, « une femme de 22 ans atteinte de mucoviscidose »
ne désigne qu'une personne.

Le référentiel livré compte 13 codes CIM-10, dont **trois maladies rares** que le
jeu précédent ne contenait pas :

| code | libellé | patients | diffusé ? |
|---|---|---:|:--|
| `G12` | Amyotrophie spinale | 8 | **oui** — 8 ≥ 5 |
| `E84` | Mucoviscidose | 4 | **non** — supprimé |
| `Q90` | Trisomie 21 | 3 | **non** — supprimé |

`gold_recherche.coh_prevalence` renvoie donc **11 lignes pour 13 codes**. Ce
n'est pas une perte de données : c'est la protection qui s'applique, et elle est
visible sans instrumentation particulière — il suffit de compter les lignes.

Sur l'ensemble des croisements pathologie × tranche d'âge, **41 cohortes sur 47
sont diffusables et 6 sont supprimées**. Le seuil n'est pas décoratif : il retire
effectivement de la donnée, et les vues sont déclarées `SQL SECURITY DEFINER`
pour qu'un compte de recherche ne puisse pas le contourner en interrogeant
directement les tables silver — `eds acces` le vérifie.

> Le jeu de données a manifestement été construit pour éprouver ce point. Trois
> pathologies à 3, 4 et 8 patients encadrent le seuil de part et d'autre : c'est
> le test que l'on écrirait soi-même.

---

## 6. L'évolution : catégories de service et actes

Le CHU a livré, le 2026-08-29, une description plus fine des services et un
nouveau flux d'actes médicaux. Le socle n'a pas bougé : **seuls 3 fichiers sur
92 ont été copiés dans le lake**, les 89 autres reconnus sur leur date. La
découverte teste l'existence de chaque fichier et ignore les dates où il manque,
si bien qu'un référentiel qui arrive en cours de route ne demande aucune
configuration particulière.

Le sujet signale deux pièges. Aucun des deux ne lève d'erreur quand on tombe
dedans — ils produisent seulement des chiffres faux, ce qui est pire.

### Piège 1 — le référentiel de description est incomplet

Il décrit **7 services sur 8** : `NEURO` n'y figure pas. Ce n'est pas un détail,
c'est le deuxième service en volume — 1 208 séjours, 1 471 actes, 393 850 € de
facturation.

Un `INNER JOIN` l'aurait fait disparaître de tous les indicateurs par catégorie
et par pôle, **sans un mot** : les totaux seraient restés cohérents entre eux, et
faux. Le choix retenu :

- **`LEFT JOIN`**, le service reste dans la dimension ;
- sa catégorie et son pôle valent explicitement `non décrit`, visibles sur les
  graphiques plutôt qu'escamotés ;
- un témoin `est_decrit` évite d'éparpiller des tests sur chaîne vide ;
- **`capacite_lits` reste `NULL`, jamais 0.** Un service non décrit n'a pas zéro
  lit, il a un nombre de lits inconnu. Écrire 0 produirait une division par zéro
  dans la densité d'actes par lit — un infini, ou pire, un chiffre plausible.

La densité par lit est donc **incalculable pour NEURO**, et la carte l'écarte
plutôt que de l'afficher à zéro, ce qui le ferait passer pour inactif. La
capacité d'une catégorie n'est de même renseignée que si **tous** ses services le
sont : une somme partielle se laisserait comparer aux autres sans dire qu'elle
est sous-estimée.

Le contrôle `service_sans_description` compte ces services à chaque exécution.

### Piège 2 — le service est porté par le séjour, pas par l'acte

`actes.parquet` ne contient que `stay_id`, `code_ccam` et `acte_ts`. Pour compter
les actes par service, il faut donc remonter au séjour — sans relier deux tables
de faits.

Joindre `fait_acte` à `fait_sejour` multiplierait chaque séjour par son nombre
d'actes : le compte des séjours vaudrait 8 112 au lieu de 6 729, et **aucune
erreur ne se lèverait**. La solution est celle que le projet applique déjà à
`fait_monitoring` : `service_code` est **dénormalisé sur `fait_acte`** à la
construction, depuis `sejour_recevable`. Les indicateurs ne joignent ensuite que
`fait_acte` et `dim_service` — un fait et une dimension.

Quand deux comptes doivent se rencontrer — actes par séjour — ils sont agrégés
**séparément** puis rapprochés sur `service_code`, une clé de dimension et non
une ligne de fait. Un test le garde : la somme des séjours de
`kpi_actes_service` doit valoir exactement `count()` sur `fait_sejour`.

### Les cinq indicateurs

Trois d'entre eux — actes par service, densité par lit, montant facturé —
partagent le **même grain**. Ils tiennent donc dans une seule table,
`kpi_actes_service`, plutôt que dans trois presque identiques : le grain définit
la table, et le multiplier sans raison multiplierait les occasions de les voir
diverger.

| service | actes | lits | actes/lit | actes/séjour | T2A |
|---|---:|---:|---:|---:|---:|
| CARDIO | 1 935 | 30 | 64,5 | 1,21 | 521 655 € |
| URGENCES | 1 731 | 20 | 86,6 | 1,22 | 478 585 € |
| **NEURO** | 1 471 | *—* | *—* | 1,22 | 393 850 € |
| PNEUMO | 1 009 | 28 | 36,0 | 1,20 | 268 045 € |
| PEDIA | 598 | 22 | 27,2 | 1,19 | 171 165 € |
| CHIR | 564 | 40 | 14,1 | 1,18 | 147 145 € |
| REA | 563 | 16 | 35,2 | 1,21 | 154 740 € |
| ONCO | 241 | 35 | 6,9 | 1,14 | 64 265 € |

Les totaux se recoupent à trois endroits : 8 112 actes et 2 199 450 € se
retrouvent aussi bien par service que par type d'acte, et les 6 729 séjours
aussi bien par service que par catégorie.

### Non-régression

Le sujet l'exige, et un test le vérifie : les sept indicateurs de synthèse sont
**inchangés** après l'évolution — 6 729 séjours, 5 949 patients, DMS 5,15 j,
réadmission 12,89 %, 3 314 relevés en alerte.

---

## 7. Recalcul des sept indicateurs de synthèse

Chaque valeur de `gold_pilotage.kpi_synthese` a été recalculée depuis les
fichiers bruts, en réimplémentant la définition métier à la main.

| indicateur | entrepôt | recalcul manuel | |
|---|---:|---:|:--:|
| séjours | 6 729 | 6 729 | ✓ |
| patients distincts | 5 949 | 5 949 | ✓ |
| séjours en cours | 683 | 683 | ✓ |
| DMS (jours) | 5,15 | 31 140 / 6 046 = 5,1505 | ✓ |
| taux de réadmission 30 j | 0,1289 | 392 / 3 042 = 0,1289 | ✓ |
| relevés en alerte | 3 314 | 3 314 | ✓ |
| part de relevés en alerte | 0,0810 | 3 314 / 40 920 = 0,0810 | ✓ |

Les définitions retenues, telles que le recalcul les a reproduites :

**DMS** — moyenne de `duree_jours` sur les seuls séjours **clos** : 31 140 jours
pour 6 046 séjours. Inclure les 683 séjours en cours tronquerait leur durée à la
date d'observation et tirerait la moyenne vers le bas. Les durées observées vont
de 1 à 19 jours, médiane 4.

**Patients distincts : 5 949, et non 6 000.** `dim_patient` contient 6 000
patients, mais 51 n'ont aucun séjour retenu. L'indicateur compte les patients
**hospitalisés**, pas les patients connus. Ces 51 patients ne sont pas perdus
pour autant — voir
[Les 51 patients sans séjour](#les-51-patients-sans-séjour).

**Taux de réadmission** — numérateur : les séjours dont le précédent séjour du
même patient s'est terminé entre 0 et 30 jours plus tôt, hors sorties par décès,
mutation ou transfert. Dénominateur : les 3 042 séjours clos dont la sortie n'est
pas l'une de ces trois — soit exactement les sorties à domicile.

**Alertes** — bradycardie < 50, tachycardie > 100, hypoxémie SpO2 < 92, fièvre
> 38,5 °C, appliquées aux 40 920 relevés retenus. Les seuils sont dans
`config/regles.yml` et consignés dans `ops.parametres` à chaque exécution : on
sait toujours avec quels seuils un chiffre a été produit.

---

## 8. Confrontation au corrigé du commanditaire

Le commanditaire a fourni une feuille de réponses attendues, calculée sur le même
jeu de données. C'est une troisième mesure, indépendante de nos deux premières.

| point de contrôle | résultat |
|---|---|
| `dim_patient` : 18 000 → 6 000 | ✓ |
| `fait_sejour` : 6 797 → 6 729, 68 écartés | ✓ |
| `fait_monitoring` : 41 778 → 40 920, 858 écartés | ✓ |
| **KPI 1** — DMS par 8 services | ✓ effectifs exacts, DMS à ±0,02 (tolérance ±0,1) |
| **KPI 2** — réadmission à 30 jours | **écart de définition, assumé** — voir plus bas |
| **KPI 3** — activité urgences par jour | ✓ 28 jours × 3 colonnes |
| **KPI 4** — surveillance des constantes par jour | ✓ 30 jours × 2 colonnes |
| **KPI 5** — prévalence par pathologie | ✓ 11 codes diffusés, 2 masqués sous k = 5 |
| **KPI 6** — cohortes âge × sexe | ✓ **102 lignes sur 102** |

Trois règles ont été alignées sur le corrigé, qui fait foi :

**Les seuils d'alerte** passent à FC < 50 (au lieu de 60) et T° > 38,5 (au lieu
de 38,0) ; SpO2 < 92 était déjà identique. Chacun tombe dans une plage vide des
données — la FC saute de 49 à 60 puis de 95 à 101, la SpO2 de 91 à 96, la
température de 37,6 à 38,6 — donc le décompte ne dépendait pas de leur réglage
fin, et l'écart portait sur la définition, non sur les chiffres.

**Les relevés se rattachent à bronze**, comme les diagnostics. Nous en rejetions
520 parce que leur séjour avait été écarté : c'était une incohérence de notre
part, puisque nous venions d'adopter le principe inverse pour les diagnostics.
Une constante mesurée au chevet du patient reste vraie quand la date de sortie
du séjour est fautive.

**`kpi_urgences_jour` compte le service URGENCES**, et non les admissions en mode
« urgence » — un patient admis en urgence en cardiologie n'est pas passé aux
urgences. Les deux mesures coexistent désormais, nommées pour ce qu'elles sont,
et les colonnes `encore_presents` et `duree_moy_heures` ont été ajoutées.

### L'écart maintenu : le taux de réadmission

| | numérateur | dénominateur | taux |
|---|---:|---:|---:|
| corrigé | 780 | 6 729 | 11,59 % |
| ce projet | 392 | 3 042 | 12,89 % |

Notre chiffre brut de **780 retours dans la fenêtre de 30 jours est exactement
celui du corrigé** : la mesure concorde, la définition diffère. Nous en
retranchons 133 retours après un décès et 255 après mutation ou transfert, et
nous rapportons le reste aux 3 042 séjours dont le patient pouvait effectivement
revenir — c'est-à-dire les sorties à domicile.

Adopter la définition du corrigé publierait **133 patients réadmis après leur
propre décès**. L'écart est donc conservé, délibérément, et documenté en
[section 4](#4-anomalies-conservées-et-signalées) : `ops.data_quality` porte les
deux exclusions, si bien que le chiffre du corrigé se reconstitue depuis le
nôtre sans lire une ligne de SQL — 392 + 133 + 255 = 780.

---

## 9. Recalcul détaillé sur trois séjours

Trois séjours contrastés, suivis à la main de bout en bout. Toutes les valeurs
d'entrée se lisent directement dans `source-filestorage/`.

### S00004564 — séjour clos, cas médian

| | |
|---|---|
| service | CARDIO |
| admission | 2026-08-23 06:27 |
| sortie | 2026-08-27 19:27 |
| naissance | 1952-06-20 |
| sortie | domicile |

- **Durée** : `2026-08-27 − 2026-08-23` = **4 jours**. Le temps réellement
  écoulé est de 4 jours et 13 heures ; la durée retenue compte les
  **franchissements de minuit**, c'est-à-dire le nombre de nuits. C'est la
  convention hospitalière usuelle, et c'est ce que fait `dateDiff('day', ...)`.
- **Âge à l'admission** : 2026 − 1952 = **74 ans** → tranche **65-74**.
- Entrepôt : `duree_jours = 4`, `age_a_admission = 74`, `est_en_cours = 0`,
  diagnostics I50 (principal) et J44 en tranche `65-74`. ✓

### S00006031 — séjour long, terminé par un décès

| | |
|---|---|
| service | NEURO |
| admission | 2026-08-23 09:51 |
| sortie | 2026-09-05 01:51 |
| naissance | 2004-11-15 |
| sortie | décès |

- **Durée** : `2026-09-05 − 2026-08-23` = **13 jours**.
- **Âge** : 2026 − 2004 = **22 ans** → tranche **18-64**.
- Ce séjour est **au dénominateur de la DMS mais pas à celui de la
  réadmission** : une sortie par décès exclut le patient d'un retour possible.
- Entrepôt : `duree_jours = 13`, `age_a_admission = 22`, diagnostic F32 en
  tranche `18-64`. ✓

### S00000101 — séjour en cours

| | |
|---|---|
| service | NEURO |
| admission | 2026-08-16 10:11 |
| sortie | *(aucune)* |
| naissance | 1969-02-06 |

- Sortie vide : ce n'est **pas** une anomalie, c'est un patient encore
  hospitalisé. Le séjour est retenu, marqué `est_en_cours = 1`, et sa durée reste
  `NULL` — il ne pèse donc pas sur la DMS.
- **Âge** : 2026 − 1969 = **57 ans** → tranche **18-64**.
- Entrepôt : `duree_jours = NULL`, `est_en_cours = 1`, `age_a_admission = 57`,
  diagnostics I63 (principal) et I50 en tranche `18-64`. ✓

> **Sur l'âge.** Le jour et le mois de naissance sont supprimés dès l'entrée du
> lake, par généralisation RGPD : seule l'année subsiste. L'âge calculé est donc
> l'âge **atteint dans l'année** de l'admission, non l'âge exact à la date
> d'admission. Le biais est de +0,5 an en moyenne, et il est assumé : il ne
> déplace une tranche d'âge que pour les patients nés à quelques mois d'une
> borne, et le gain de protection est sans commune mesure.

---

## 10. Ce que le recalcul a corrigé

Le premier recalcul manuel **ne tombait pas juste** : il trouvait 15 séjours
rejetés au lieu de 68.

L'erreur était dans le recalcul, pas dans le pipeline. Le calcul manuel testait
`date(sortie) < date(admission)` ; la règle du silver teste
`discharge_ts < admission_ts`, sur les horodatages. **53 séjours sortent le même
jour civil que leur admission, mais à une heure antérieure** — admis à 16 h,
sortis à 8 h le matin même. La comparaison de dates les laissait passer.

C'est exactement ce qu'on attend d'une validation par mesure indépendante : le
désaccord n'a pas été supposé faux d'un côté ou de l'autre, il a été instruit, et
il a localisé une subtilité de définition qui méritait d'être écrite. La règle du
pipeline est la bonne : un séjour dont la sortie précède l'admission est
incohérent, que ce soit de deux heures ou de deux jours.

---

## 11. Test de rejeu

Le pipeline doit pouvoir être relancé sans que les chiffres bougent. Deux
`eds run` consécutifs, avec relevé des indicateurs avant, entre et après :

```
kpi avant  : 6729  5949  683  5.15  0.1289  3270  0.0809
kpi après 1: 6729  5949  683  5.15  0.1289  3270  0.0809
kpi après 2: 6729  5949  683  5.15  0.1289  3270  0.0809
```

Identiques. Le nombre de rejets l'est aussi : **926 par exécution**
(68 + 858), stable d'un run à l'autre.

Les compteurs du lake montrent le mécanisme d'idempotence à l'œuvre : les 89
fichiers déjà copiés sont reconnus sur leur date et **aucun octet n'en est
relu**. Les couches bronze, silver et gold sont, elles, **reconstruites
intégralement** à chaque passage (`TRUNCATE` puis `INSERT`) : c'est un choix, non
un oubli. Le volume le permet largement — la chaîne complète tourne en une
seconde et demie — et il garantit qu'une correction de règle s'applique
rétroactivement à tout l'historique, sans reprise incrémentale à écrire ni à
déboguer.

> La comparaison ci-dessus vérifie d'abord que les fichiers relevés ne sont pas
> vides. Un `diff` entre deux fichiers vides est silencieux et conclut à
> l'identité : c'est un piège dans lequel cette validation est tombée une fois,
> le moteur étant arrêté au moment du relevé.

---

## 12. Ce que cette validation ne couvre pas

Elle établit que **le pipeline calcule fidèlement ce qu'on lui a demandé de
calculer**. Elle n'établit pas que les définitions métier retenues sont les
bonnes : elles ont été choisies par des informaticiens, pas par des cliniciens.
Trois méritent une relecture médicale avant tout usage réel.

**1 — Les seuils d'alerte** (FC < 50 ou > 100, SpO2 < 92, T > 38,5 °C), fournis
par le commanditaire. Ils
tombent dans des plages entièrement vides des données observées, donc le décompte
des alertes ne dépend pas de leur réglage fin. Leur pertinence clinique reste à
confirmer.

**2 — La fenêtre de réadmission à 30 jours**, et les exclusions qui
l'accompagnent. Sur ce jeu, les écarts observés vont de **3 à 22 jours** et se
répartissent ainsi :

| écart | réadmissions |
|---|---:|
| 3 à 7 jours | 255 |
| 8 à 14 jours | 106 |
| 15 à 30 jours | 31 |

La fenêtre est donc réellement exercée, et aucun retour n'a lieu au-delà. Reste
que 12,9 % est un taux élevé pour un indicateur qui, dans un établissement réel,
se situerait plutôt autour de 5 % : il décrit ces données, pas la qualité de
soins d'un hôpital.

**3 — Les cinq tranches d'âge**, dont le découpage sert autant la lisibilité des
graphiques que la pertinence épidémiologique.

### Les 51 patients sans séjour

*Limite connue, mesurée et assumée.*

68 séjours sont écartés parce que leur date de sortie précède leur date
d'admission. Pour **51 patients, c'était leur unique séjour** : ils n'apparaissent
donc dans aucun indicateur de pilotage.

**Ce qu'ils deviennent exactement.** Ils ne disparaissent pas de l'entrepôt :

| couche | présents ? |
|---|---|
| `dim_patient` | oui — les 6 000 patients y sont |
| `fait_diagnostic` | **oui** — les 51, avec 94 codes CIM-10 |
| `fait_sejour` | non |
| `fait_monitoring` | non |

C'est la conséquence directe du rattachement des diagnostics à bronze : leur
information **clinique** survit, leur séjour **administratif** non. Ils pèsent
donc sur la prévalence, sur les distributions par âge et par sexe et sur les
comorbidités ; ils sont absents de l'activité, de la DMS, de l'occupation, de la
réadmission et de `coh_duree_pathologie`.

**Pourquoi le séjour est écarté.** Une sortie antérieure à l'admission rend
inexploitables les trois grandeurs que le séjour sert à produire : sa durée, sa
contribution à l'occupation d'une journée, et l'écart avec un retour éventuel.
Le conserver imposerait un troisième état, entre « clos » et « en cours », qui
se propagerait dans le dénominateur de chaque indicateur.

**L'alternative a été mesurée, pas supposée.** Garder ces séjours avec une date
de sortie nulle et un témoin dédié donnerait :

| | actuel | alternative |
|---|---:|---:|
| séjours | 6 729 | 6 797 |
| patients | 5 949 | 6 000 |
| séjours clos | 6 046 | 6 046 *(inchangé)* |
| DMS | 5,15 | 5,15 *(inchangée)* |
| relevés | 40 920 | 40 920 *(inchangé)* |
| diagnostics | 12 720 | 12 720 *(inchangé)* |
| réadmission | 12,89 % | 12,98 % |

L'écart s'est **réduit** depuis que les diagnostics et les relevés se rattachent
à bronze : leurs volumes ne dépendent plus du sort du séjour. Il ne reste que
`fait_sejour` — 68 lignes — et les 51 patients qui n'y ont que celle-là. La DMS
et les séjours clos ne bougent pas, ces séjours n'ayant pas de durée
exploitable ; la réadmission gagne 3 retours.

Le coût n'est donc pas dans les chiffres, il est dans le modèle : un troisième
état de séjour, entre « clos » et « en cours », à définir, à documenter et à
respecter dans chaque dénominateur.

**Le choix retenu**, pour ce rendu, est de conserver deux états et d'écarter le
séjour, en assumant que 0,8 % des patients n'apparaissent pas au pilotage. Il
est réversible, et le tableau ci-dessus dit exactement ce qu'il en coûterait de
changer d'avis. La règle est visible dans `ops.data_quality`
(`sortie_avant_admission`, 68 lignes sur 6 797) et les codes de ces séjours dans
`diagnostic_sans_sejour_retenu` (127 sur 12 720).

**Sur l'occupation.** La courbe s'arrête au **dernier dépôt** (2026-08-28), et non
à la date du jour. Au-delà de cette borne les admissions ne sont plus connues :
la courbe décroîtrait sans que l'hôpital se vide, ce qui n'est pas de
l'occupation mais l'extinction d'une cohorte fermée.

Ce point a été trouvé pendant cet audit. Les séjours en cours étaient comptés
présents jusqu'à `now()`, ce qui produisait une **falaise à la date du jour** —
et une falaise qui se déplaçait à chaque exécution. Un tableau de bord dont la
forme dépend de l'heure où on le regarde ne vaut rien ; trois tests gardent
désormais la borne (`TestHorizonDObservation`).

Le début de la courbe reste, lui, un artefact assumé : le premier dépôt est celui
du 1er août, personne n'est donc « déjà présent » ce jour-là, et la montée des
dix premiers jours est le remplissage de la fenêtre d'observation, non une
hausse d'activité.

Enfin, cette validation ne se prononce pas sur les **anomalies conservées**
décrites en [section 4](#4-anomalies-conservées-et-signalées) : elle mesure leur
ampleur et l'effet des règles qui les traitent, elle ne dit pas si ces données
devraient exister. 133 admissions postérieures à un décès appartiennent au jeu de
données, pas au pipeline. La réponse correcte n'est pas de les corriger en
silence, c'est de les remonter à l'émetteur — et, en attendant, de les afficher.
