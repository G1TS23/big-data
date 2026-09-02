# Validation des chiffres

Ce document répond à une seule question : **peut-on justifier chaque chiffre
affiché sur les tableaux de bord ?**

Il procède par mesure indépendante. Tous les nombres qui suivent ont été
recalculés à partir des fichiers déposés par le CHU, avec du code écrit pour
l'occasion — `csv`, `json`, `pyarrow` — qui n'importe pas une ligne du pipeline.
Deux mesures indépendantes qui concordent valent mieux qu'une mesure répétée
deux fois.

- [1. Réconciliation source ↔ entrepôt](#1-réconciliation-source--entrepôt)
- [2. Ce que chaque écart recouvre](#2-ce-que-chaque-écart-recouvre)
- [3. Anomalies conservées, et ce qu'elles coûtent](#3-anomalies-conservées-et-ce-quelles-coûtent)
- [4. Recalcul des sept indicateurs de synthèse](#4-recalcul-des-sept-indicateurs-de-synthèse)
- [5. Recalcul détaillé sur trois séjours](#5-recalcul-détaillé-sur-trois-séjours)
- [6. Ce que le recalcul a corrigé](#6-ce-que-le-recalcul-a-corrigé)
- [7. Test de rejeu](#7-test-de-rejeu)
- [8. Ce que cette validation ne couvre pas](#8-ce-que-cette-validation-ne-couvre-pas)

---

## 1. Réconciliation source ↔ entrepôt

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
| patients | 16 200 | 16 200 | 6 000 | 0 | 10 200 | ✓ |
| sejours | 15 000 | 15 000 | 14 864 | 136 | 0 | ✓ |
| diagnostics | 37 380 | 37 380 | 37 040 | 340 | 0 | ✓ |
| monitoring | 66 677 | 66 677 | 64 799 | 1 878 | 0 | ✓ |
| services | 8 | 8 | 8 | 0 | 0 | ✓ |
| cim10 | 10 | 10 | 10 | 0 | 0 | ✓ |

Deux colonnes méritent un mot.

**Bronze reproduit la source ligne pour ligne**, sur les six tables. C'est
l'engagement de la couche : elle recopie sans juger. La colonne `diagnostics`
compte ici les codes dépliés (`sum(length(diagnostics))`) et non les lignes :
bronze conserve le JSON imbriqué tel qu'il arrive, soit 15 000 objets portant
37 380 codes. Le dépliage est fait par le moteur en silver.

**Le détail des dépôts**, pour situer les ordres de grandeur :

| dépôt | patients | séjours | diagnostics | monitoring |
|---|---:|---:|---:|---:|
| 2026-08-26 | 4 800 | 5 000 | 5 000 objets | 24 631 |
| 2026-08-27 | 5 400 | 5 000 | 5 000 objets | 22 190 |
| 2026-08-28 | 6 000 | 5 000 | 5 000 objets | 19 856 |

---

## 2. Ce que chaque écart recouvre

### Patients : 16 200 → 6 000 (10 200 doublons)

Les trois dépôts de patients sont des **instantanés cumulatifs**, non des
deltas : 4 800, puis 5 400, puis 6 000 lignes, et le fichier du 28 contient
tous les patients des deux précédents. Recompté à la main, l'union des trois
fichiers ne porte que **6 000 identifiants distincts**, exactement le contenu
de `dim_patient`. L'écart de 10 200 lignes est donc entièrement de la
répétition, pas de la perte.

`dim_patient` conserve la version la plus récente de chaque patient
(`argMax(..., _ingestion_date)`) : si une correction arrive dans un dépôt
ultérieur, c'est elle qui l'emporte.

### Séjours : 136 rejets, tous de la même règle

Les 136 rejets portent le motif `sortie_avant_admission`. Aucun autre motif ne
se déclenche : pas d'admission absente, pas de service inconnu, pas de patient
inconnu. Le référentiel des services couvre donc l'intégralité des séjours, et
tous les `patient_id` cités trouvent leur patient.

### Diagnostics : 340 rejets, tous en cascade

Les 340 codes rejetés portent le motif `sejour_inconnu`, et se rattachent aux
**136 séjours rejetés à l'étape précédente** — vérifié : les 340 clés sans
exception. Aucun diagnostic ne cite un séjour qui n'existerait nulle part.
C'est la propagation attendue : un séjour incohérent emporte ses diagnostics.

### Monitoring : 1 878 rejets, deux motifs à ne pas confondre

| motif | relevés | nature |
|---|---:|---|
| `fc_hors_bornes` | 1 369 | fréquence cardiaque hors de 20–250 bpm : la **valeur est fausse** (capteur, saisie) |
| `sejour_inconnu` | 509 | relevé rattaché à l'un des 136 séjours rejetés |

Les 509 relevés `sejour_inconnu` se répartissent sur 12 séjours seulement. En
ajoutant les 11 relevés de séjours rejetés qui sortent d'abord sur les bornes
physiologiques, **520 relevés bronze appartiennent aux 136 séjours rejetés — et
il en survit 0 en silver**, ce qui vérifie l'intégrité référentielle par un
second chemin.

Aucun relevé n'est écarté pour une SpO2 ou une température hors bornes.

> La distinction est structurante. Une valeur **hors bornes physiologiques** est
> fausse : elle est écartée. Une valeur **hors seuils cliniques** est vraie mais
> mauvaise pour le patient : elle est conservée et marquée `est_alerte = 1`.
> Confondre les deux reviendrait à supprimer les patients qui vont mal.

---

## 3. Anomalies conservées, et ce qu'elles coûtent

Rejeter une ligne, c'est décider qu'elle est fausse. Trois anomalies de ces
données ne le sont pas : elles sont vraies, gênantes, et les écarter
fabriquerait une erreur au lieu d'en corriger une. Elles sont donc **conservées
et signalées** dans `ops.data_quality`, où `eds status` et le tableau de bord
Exploitation les donnent à voir.

| anomalie | volume | base | part |
|---|---:|---:|---:|
| séjours qui se chevauchent | 7 978 | 14 864 | 53,7 % |
| admissions postérieures à un décès | 1 231 | 14 864 | 8,3 % |
| séjours clos sans mode de sortie | 1 975 | 13 674 clos | 14,4 % |

### Le mode de sortie manquant

1 992 séjours de la source portent une date de sortie mais **aucun mode de
sortie** ; 17 tombent parmi les 136 rejets de cohérence temporelle, il en reste
1 975. Le patient est sorti, on ne sait pas comment.

Ce ne sont **pas** les séjours en cours. Les deux populations sont disjointes :

| | mode renseigné | mode vide |
|---|---:|---:|
| séjour clos (date de sortie présente) | 11 699 | **1 975** ← l'anomalie |
| séjour en cours (sortie `NULL`) | 0 | 1 190 ← attendu |

La case en haut à droite est l'anomalie ; celle en bas à droite est normale, un
patient encore hospitalisé n'ayant pas de mode de sortie. La case en bas à
gauche est vide, ce qui écarte l'incohérence symétrique — sortir sans être sorti.

**Ces 1 975 séjours sont comptés au dénominateur du taux de réadmission**, qui
exclut les sorties par décès, mutation et transfert, mais pas le mode vide. Le
choix n'est pas neutre : les exclure porterait le taux de 5,38 % à 7,21 %, soit
34 % d'écart relatif.

Les données tranchent. En comparant le taux de retour à 30 jours selon le mode
de sortie :

| mode de sortie | retours sous 30 j | séjours clos | taux |
|---|---:|---:|---:|
| domicile | 312 | 5 814 | **5,37 %** |
| *(mode inconnu)* | 107 | 1 975 | **5,42 %** |

Les séjours au mode inconnu se comportent **exactement** comme les sorties à
domicile. L'écart de 0,05 point est très largement contenu dans le bruit
d'échantillonnage — à n = 1 975 et p ≈ 5,4 %, l'erreur type vaut ±0,5 point. Les
exclure reviendrait donc à retirer une population indiscernable de celle qu'on
garde : cela ne corrigerait aucun biais, cela en créerait un. **Ils restent au
dénominateur**, et cette décision repose désormais sur une mesure plutôt que sur
une convention.

### Les retours après un décès

1 231 séjours suivent un séjour terminé par un décès. Leur écart avec ce décès
se répartit ainsi :

| écart | séjours | |
|---|---:|---|
| négatif (−12 à −1 j) | 1 122 | l'admission commence **avant** le décès : chevauchement |
| 0 jour | 91 | |
| 1 jour | 12 | dans la fenêtre des 30 jours |
| 2 jours | 6 | |

**109 séjours tombent donc dans la fenêtre** et seraient comptés comme des
réadmissions. La clause `mode_sortie_precedent NOT IN ('deces', ...)` les écarte
tous : `est_readmission_30j` vaut 0 pour l'intégralité d'entre eux.

L'effet sur le chiffre est dérisoire. Sans cette exclusion, le taux passerait de
**5,38 % à 5,40 %** — numérateur et dénominateur grossissent dans les mêmes
proportions (419 → 528, 7 789 → 9 785). **La règle ne défend pas la valeur, elle
défend son sens** : on pourrait la retirer sans que personne ne le voie sur un
tableau de bord, et publier 109 patients réadmis après leur propre décès.

### Le numérateur est auditable

Les deux exclusions sont consignées dans `ops.data_quality`, si bien que le
numérateur publié se reconstitue sans lire une ligne de SQL :

```
retours dans la fenêtre de 30 jours                748
  − retours après un décès           (écartés)   − 109
  − retours après mutation/transfert (écartés)   − 220
                                                 ─────
  = réadmissions publiées                          419
```

Un test le vérifie à chaque exécution (`TestExclusionsDuTauxDeReadmission`).

---

## 4. Recalcul des sept indicateurs de synthèse

Chaque valeur de `gold_pilotage.kpi_synthese` a été recalculée depuis les
fichiers bruts, en réimplémentant la définition métier à la main.

| indicateur | entrepôt | recalcul manuel | |
|---|---:|---:|:--:|
| séjours | 14 864 | 14 864 | ✓ |
| patients distincts | 5 358 | 5 358 | ✓ |
| séjours en cours | 1 190 | 1 190 | ✓ |
| DMS (jours) | 6,08 | 6,0847 | ✓ |
| taux de réadmission 30 j | 0,0538 | 419 / 7 789 = 0,0538 | ✓ |
| relevés en alerte | 5 192 | 5 192 | ✓ |
| part de relevés en alerte | 0,0801 | 5 192 / 64 799 = 0,0801 | ✓ |

Les définitions retenues, telles que le recalcul les a reproduites :

**DMS** — moyenne de `duree_jours` sur les seuls séjours **clos**. Somme des
durées 83 202 jours sur 13 674 séjours clos. Inclure les 1 190 séjours en cours
tronquerait leur durée à la date d'observation et tirerait la moyenne vers le
bas.

**Patients distincts : 5 358, et non 6 000.** `dim_patient` contient 6 000
patients, mais 642 d'entre eux n'ont aucun séjour retenu. L'indicateur compte
les patients **hospitalisés**, pas les patients connus.

**Taux de réadmission** — numérateur : les séjours dont le précédent séjour du
même patient s'est terminé entre 0 et 30 jours plus tôt, en excluant les sorties
par décès, mutation ou transfert. Dénominateur : les séjours clos dont la sortie
n'est pas l'une de ces trois. Un écart négatif signale deux séjours qui se
chevauchent, ce n'est pas un retour : il est exclu.

**Alertes** — bradycardie < 60, tachycardie > 100, hypoxémie SpO2 < 92, fièvre
> 38,0 °C, appliquées aux 64 799 relevés retenus. Les seuils sont dans
`config/regles.yml` et consignés dans `ops.parametres` à chaque exécution : on
sait toujours avec quels seuils un chiffre a été produit.

---

## 5. Recalcul détaillé sur trois séjours

Trois séjours contrastés, suivis à la main de bout en bout. Toutes les valeurs
d'entrée se lisent directement dans `source-filestorage/`.

### S00012916 — séjour clos, cas médian

| | |
|---|---|
| service | NEURO |
| admission | 2026-08-28 16:01 |
| sortie | 2026-09-03 05:01 |
| naissance | 1968-03-08 |

- **Durée** : du 28 août au 3 septembre, soit `2026-09-03 − 2026-08-28` = **6 jours**.
  Le temps réellement écoulé est de 5 jours et 13 heures ; la durée retenue
  compte les **franchissements de minuit**, c'est-à-dire le nombre de nuits.
  C'est la convention hospitalière usuelle, et c'est ce que fait
  `dateDiff('day', ...)`.
- **Âge à l'admission** : 2026 − 1968 = **58 ans**.
- **Tranche d'âge** : 58 < 65 → **18-64**.
- Entrepôt : `duree_jours = 6`, `age_a_admission = 58`, `est_en_cours = 0`,
  diagnostic I21 en tranche `18-64`. ✓

### S00013754 — séjour clos, long (99ᵉ centile)

| | |
|---|---|
| service | CHIR |
| admission | 2026-08-28 22:26 |
| sortie | 2026-09-09 09:26 |
| naissance | 2004-12-04 |

- **Durée** : `2026-09-09 − 2026-08-28` = **12 jours** (11 jours et 11 heures réelles).
- **Âge** : 2026 − 2004 = **22 ans** → tranche **18-64**.
- Entrepôt : `duree_jours = 12`, `age_a_admission = 22`, diagnostic J18 en
  tranche `18-64`. ✓

### S00010029 — séjour en cours

| | |
|---|---|
| service | ONCO |
| admission | 2026-08-28 00:48 |
| sortie | *(aucune)* |
| naissance | 1931-07-14 |

- Sortie vide : ce n'est **pas** une anomalie, c'est un patient encore
  hospitalisé. Le séjour est retenu, marqué `est_en_cours = 1`, et sa durée
  reste `NULL` — il ne pèse donc pas sur la DMS.
- **Âge** : 2026 − 1931 = **95 ans** → tranche **85+**.
- Entrepôt : `duree_jours = NULL`, `est_en_cours = 1`, `age_a_admission = 95`,
  ses quatre diagnostics (K35 principal, C34, E11, I21 associés) tous en
  tranche `85+`. ✓

> **Sur l'âge.** Le jour et le mois de naissance sont supprimés dès l'entrée du
> lake, par généralisation RGPD : seule l'année subsiste. L'âge calculé est donc
> l'âge **atteint dans l'année** de l'admission, non l'âge exact à la date
> d'admission. Le biais est de +0,5 an en moyenne, et il est assumé : il ne
> déplace une tranche d'âge que pour les patients nés à quelques mois d'une
> borne, et le gain de protection est sans commune mesure.

---

## 6. Ce que le recalcul a corrigé

Le premier recalcul manuel **ne tombait pas juste** : il trouvait 15 séjours
rejetés au lieu de 136, et une DMS de 6,03 au lieu de 6,08.

L'erreur était dans le recalcul, pas dans le pipeline. Le calcul manuel testait
`date(sortie) < date(admission)` ; la règle du silver teste
`discharge_ts < admission_ts`, sur les horodatages. **121 séjours sortent le
même jour civil que leur admission, mais à une heure antérieure** — admis à
16 h, sortis à 8 h le matin même. La comparaison de dates les laissait passer,
et leurs durées de 0 jour tiraient la DMS vers le bas.

C'est exactement ce qu'on attend d'une validation par mesure indépendante : le
désaccord n'a pas été supposé faux d'un côté ou de l'autre, il a été instruit,
et il a localisé une subtilité de définition qui méritait d'être écrite. La
règle du pipeline est la bonne : un séjour dont la sortie précède l'admission
est incohérent, que ce soit de deux heures ou de deux jours.

---

## 7. Test de rejeu

Le pipeline doit pouvoir être relancé sans que les chiffres bougent. Deux
`eds run` consécutifs, avec relevé des indicateurs avant, entre et après :

```
kpi avant  : 14864  5358  1190  6.08  0.0538  5192  0.0801
kpi après 1: 14864  5358  1190  6.08  0.0538  5192  0.0801
kpi après 2: 14864  5358  1190  6.08  0.0538  5192  0.0801
```

Identiques. Le nombre de rejets l'est aussi : **2 354 par exécution**
(136 + 340 + 1 878), stable d'un run à l'autre.

Les compteurs du lake montrent le mécanisme d'idempotence à l'œuvre :

```
lake     vus=14  traites=0   ignores=14     ← dépôts déjà ingérés, écartés sur leur date
bronze   vus=14  traites=14  ignores=0
silver   vus=28  traites=28  ignores=0
gold     vus=29  traites=29  ignores=0
```

Le lake reconnaît les 14 fichiers déjà copiés et n'en relit aucun octet. Les
couches bronze, silver et gold sont, elles, **reconstruites intégralement** à
chaque passage (`TRUNCATE` puis `INSERT`) : c'est un choix, et non un oubli. Le
volume le permet largement — la chaîne complète tourne en moins d'une seconde —
et il garantit qu'une correction de règle s'applique rétroactivement à tout
l'historique, sans reprise incrémentale à écrire ni à déboguer.

> La comparaison ci-dessus vérifie d'abord que les fichiers relevés ne sont pas
> vides. Un `diff` entre deux fichiers vides est silencieux et conclut à
> l'identité : c'est un piège dans lequel cette validation est tombée une fois,
> le moteur étant arrêté au moment du relevé.

---

## 8. Ce que cette validation ne couvre pas

Elle établit que **le pipeline calcule fidèlement ce qu'on lui a demandé de
calculer**. Elle n'établit pas que les définitions métier retenues sont les
bonnes : elles ont été choisies par des informaticiens, pas par des cliniciens.
Trois méritent une relecture médicale avant tout usage réel.

**1 — Les seuils d'alerte** (FC < 60 ou > 100, SpO2 < 92, T > 38,0 °C). Ils
tombent dans des plages entièrement vides des données observées, donc le
décompte des alertes ne dépend pas de leur réglage fin. Leur pertinence clinique
reste à confirmer.

**2 — La fenêtre de réadmission à 30 jours n'est pas exercée par ces données.**
Sur les 8 726 séjours ayant un séjour précédent, l'écart maximal observé est de
**2 jours**, et il n'existe **aucun** retour entre 3 et 30 jours :

| écart | réadmissions |
|---|---:|
| 0 jour | 309 |
| 1 jour | 98 |
| 2 jours | 12 |
| 3 à 30 jours | **0** |

Une fenêtre à 2 jours produirait donc exactement le même chiffre. Les 30 jours
sont une convention défendable — c'est l'usage courant — mais ces données ne la
discriminent d'aucune autre. Le taux publié mesure en réalité des **retours sous
48 heures**, ce qui, dans un hôpital réel, relèverait plus souvent du transfert
mal codé que de la réadmission. La valeur de 5,4 % ne doit pas être lue comme un
indicateur de qualité des soins.

**3 — Les cinq tranches d'âge**, dont le découpage sert autant la lisibilité des
graphiques que la pertinence épidémiologique.

Enfin, cette validation ne se prononce pas sur les **anomalies conservées**
décrites en [section 3](#3-anomalies-conservées-et-ce-quelles-coûtent) : elle
mesure leur ampleur et l'effet des règles qui les traitent, elle ne dit pas si
ces données devraient exister. 53,7 % de séjours qui se chevauchent et 8,3 %
d'admissions postérieures à un décès sont des taux qu'aucun établissement réel
ne présenterait ; ils appartiennent au jeu de données, pas au pipeline. La
réponse correcte n'est pas de les corriger en silence, c'est de les remonter à
l'émetteur — et, en attendant, de les afficher.
