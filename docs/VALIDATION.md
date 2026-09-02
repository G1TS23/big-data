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
- [6. Recalcul des sept indicateurs de synthèse](#6-recalcul-des-sept-indicateurs-de-synthèse)
- [7. Recalcul détaillé sur trois séjours](#7-recalcul-détaillé-sur-trois-séjours)
- [8. Ce que le recalcul a corrigé](#8-ce-que-le-recalcul-a-corrigé)
- [9. Test de rejeu](#9-test-de-rejeu)
- [10. Ce que cette validation ne couvre pas](#10-ce-que-cette-validation-ne-couvre-pas)

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

89 fichiers, 3,2 Mo, versionnés dans `source-filestorage/`. Les séjours,
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
| diagnostics | 12 720 | 12 720 | 12 593 | 127 | 0 | ✓ |
| monitoring | 41 778 | 41 778 | 40 400 | 1 378 | 0 | ✓ |
| services | 8 | 8 | 8 | 0 | 0 | ✓ |
| cim10 | 13 | 13 | 13 | 0 | 0 | ✓ |

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

### Diagnostics : 127 rejets, tous en cascade

Les 127 codes rejetés portent le motif `sejour_inconnu` et se rattachent **tous**
aux 68 séjours rejetés à l'étape précédente. Aucun diagnostic ne cite un séjour
qui n'existerait nulle part : c'est la propagation attendue, un séjour incohérent
emportant ses diagnostics.

### Monitoring : 1 378 rejets, deux motifs à ne pas confondre

| motif | relevés | nature |
|---|---:|---|
| `fc_hors_bornes` | 858 | fréquence cardiaque hors de 20–250 bpm : la **valeur est fausse** (capteur, saisie) |
| `sejour_inconnu` | 520 | relevé rattaché à l'un des 68 séjours rejetés |

Les 520 relevés `sejour_inconnu` se répartissent sur 11 séjours seulement. En y
ajoutant les 8 relevés de séjours rejetés qui sortent d'abord sur les bornes
physiologiques, **528 relevés bronze appartiennent aux 68 séjours rejetés — et il
en survit 0 en silver**, ce qui vérifie l'intégrité référentielle par un second
chemin.

Aucun relevé n'est écarté pour une SpO2 ou une température hors bornes.

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
| `releve_en_alerte` | 3 270 | 40 400 | 8,1 % |
| `sejours_chevauchants` | **0** | 6 729 | 0 % |
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

## 6. Recalcul des sept indicateurs de synthèse

Chaque valeur de `gold_pilotage.kpi_synthese` a été recalculée depuis les
fichiers bruts, en réimplémentant la définition métier à la main.

| indicateur | entrepôt | recalcul manuel | |
|---|---:|---:|:--:|
| séjours | 6 729 | 6 729 | ✓ |
| patients distincts | 5 949 | 5 949 | ✓ |
| séjours en cours | 683 | 683 | ✓ |
| DMS (jours) | 5,15 | 31 140 / 6 046 = 5,1505 | ✓ |
| taux de réadmission 30 j | 0,1289 | 392 / 3 042 = 0,1289 | ✓ |
| relevés en alerte | 3 270 | 3 270 | ✓ |
| part de relevés en alerte | 0,0809 | 3 270 / 40 400 = 0,0809 | ✓ |

Les définitions retenues, telles que le recalcul les a reproduites :

**DMS** — moyenne de `duree_jours` sur les seuls séjours **clos** : 31 140 jours
pour 6 046 séjours. Inclure les 683 séjours en cours tronquerait leur durée à la
date d'observation et tirerait la moyenne vers le bas. Les durées observées vont
de 1 à 19 jours, médiane 4.

**Patients distincts : 5 949, et non 6 000.** `dim_patient` contient 6 000
patients, mais 51 n'ont aucun séjour retenu. L'indicateur compte les patients
**hospitalisés**, pas les patients connus.

**Taux de réadmission** — numérateur : les séjours dont le précédent séjour du
même patient s'est terminé entre 0 et 30 jours plus tôt, hors sorties par décès,
mutation ou transfert. Dénominateur : les 3 042 séjours clos dont la sortie n'est
pas l'une de ces trois — soit exactement les sorties à domicile.

**Alertes** — bradycardie < 60, tachycardie > 100, hypoxémie SpO2 < 92, fièvre
> 38,0 °C, appliquées aux 40 400 relevés retenus. Les seuils sont dans
`config/regles.yml` et consignés dans `ops.parametres` à chaque exécution : on
sait toujours avec quels seuils un chiffre a été produit.

---

## 7. Recalcul détaillé sur trois séjours

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

## 8. Ce que le recalcul a corrigé

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

## 9. Test de rejeu

Le pipeline doit pouvoir être relancé sans que les chiffres bougent. Deux
`eds run` consécutifs, avec relevé des indicateurs avant, entre et après :

```
kpi avant  : 6729  5949  683  5.15  0.1289  3270  0.0809
kpi après 1: 6729  5949  683  5.15  0.1289  3270  0.0809
kpi après 2: 6729  5949  683  5.15  0.1289  3270  0.0809
```

Identiques. Le nombre de rejets l'est aussi : **1 573 par exécution**
(68 + 127 + 1 378), stable d'un run à l'autre.

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

## 10. Ce que cette validation ne couvre pas

Elle établit que **le pipeline calcule fidèlement ce qu'on lui a demandé de
calculer**. Elle n'établit pas que les définitions métier retenues sont les
bonnes : elles ont été choisies par des informaticiens, pas par des cliniciens.
Trois méritent une relecture médicale avant tout usage réel.

**1 — Les seuils d'alerte** (FC < 60 ou > 100, SpO2 < 92, T > 38,0 °C). Ils
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
