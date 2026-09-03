# Passage au cloud

CHU · Entrepôt de Données de Santé · Partie 3 — hébergement

Ce chapitre décrit l'infrastructure cible, ce qu'elle change au projet, et
surtout ce qu'elle **ne** change pas. Le code correspondant est dans `infra/`,
et se vérifie sans compte cloud par `make infra`.

- [1. Pourquoi le HDS commande tout](#1-pourquoi-le-hds-commande-tout)
- [2. L'architecture cible](#2-larchitecture-cible)
- [3. La zone de dépôt](#3-la-zone-de-dépôt)
- [4. Ce qui ne change pas](#4-ce-qui-ne-change-pas)
- [5. Ce qui change vraiment](#5-ce-qui-change-vraiment)
- [6. Comment cette infrastructure se vérifie](#6-comment-cette-infrastructure-se-vérifie)
- [7. Le plan de migration](#7-le-plan-de-migration)
- [8. Ce que cela coûte](#8-ce-que-cela-coûte)
- [9. Ce qui reste à faire](#9-ce-qui-reste-à-faire)

---

## 1. Pourquoi le HDS commande tout

Héberger des données de santé à caractère personnel pour le compte d'un
établissement impose, en France, de passer par un **hébergeur certifié HDS**
(article L1111-8 du Code de la santé publique). Ce n'est pas une exigence
d'infrastructure parmi d'autres : c'est celle qui élimine d'emblée la plupart
des offres, et qui doit donc être tranchée **avant** tout choix technique.

Trois conséquences en découlent, et elles se lisent directement dans `infra/` :

**La région est contrainte.** Une variable ne suffit pas, il faut une garantie :
`variables.tf` refuse toute région hors de France par une règle de validation.
Un déploiement mal paramétré échoue au lieu de sortir les données du territoire.

**La certification porte sur des services, pas sur un fournisseur.** Un
hébergeur certifié ne l'est pas pour l'intégralité de son catalogue. Le périmètre
exact doit être vérifié service par service au moment de contracter — c'est un
point de vigilance contractuel, que ce document signale sans pouvoir le trancher.

**Le chiffrement au repos n'est plus optionnel.** Il s'applique aux disques
managés qui portent l'entrepôt, au partage où le CHU dépose, et à la base
applicative de Metabase — y compris à celle-ci, qui ne contient pourtant aucune
donnée de santé : la liste des comptes qui accèdent à un entrepôt mérite la même
protection que l'entrepôt.

## 2. L'architecture cible

```mermaid
flowchart TB
    subgraph internet [" "]
        U["Utilisateurs<br/>pilotage · recherche"]:::ext
    end
    subgraph vpc ["Réseau privé — région française, hébergeur HDS"]
        LB["Ingress<br/>TLS"]:::net
        subgraph k8s ["Kubernetes"]
            MB["Metabase<br/>Deployment"]:::app
            CH["ClickHouse<br/>StatefulSet + disque"]:::db
            CJ["Pipeline<br/>CronJob quotidien"]:::job
            LK[("lake<br/>disque managé")]:::sto
        end
        PG["PostgreSQL managé<br/>état de Metabase"]:::db
        AF["Partage de fichiers<br/>zone de dépôt du CHU"]:::sto
        SM["Gestionnaire<br/>de secrets"]:::sec
    end
    U -->|https| LB --> MB
    MB --> CH
    CJ --> CH
    CJ --> LK
    AF -->|lecture seule| CJ
    MB --> PG
    SM -.->|sel, mots de passe| CJ
    SM -.-> MB

    classDef ext fill:#e8e8e8,stroke:#666
    classDef net fill:#dbeafe,stroke:#2a78d6
    classDef app fill:#d7f0e6,stroke:#1baf7a
    classDef db fill:#fde8d7,stroke:#eb6834
    classDef job fill:#fdf0cc,stroke:#eda100
    classDef sto fill:#e5e0f7,stroke:#7c5cd6
    classDef sec fill:#f7e0e8,stroke:#c0507a
```

**ClickHouse n'a aucune adresse publique.** Il n'est joignable que depuis
l'intérieur du cluster, et seulement par Metabase et le pipeline — une politique
réseau le garantit indépendamment des mots de passe. C'est une **troisième
barrière** de cloisonnement, qui s'ajoute aux comptes du moteur et aux
collections Metabase.
---

## 3. La zone de dépôt

C'est l'endroit **le plus sensible de tout le système**, et il mérite d'être
traité à part.

Le CHU y dépose ses exports quotidiens, qui portent `nir`, `nom`, `prenom` et
`birth_date` **en clair**. C'est le seul endroit de la chaîne où une
ré-identification est possible : tout ce qui se trouve en aval a déjà traversé
la pseudonymisation à l'entrée du lake.

**Et c'est précisément ce qui rend l'ensemble défendable.** Puisque l'identité
disparaît dès la première zone que nous maîtrisons, sécuriser tout l'entrepôt
revient à sécuriser **un seul endroit**. Le reste — bronze, silver, gold, les
tableaux de bord, les sauvegardes — ne contient rien d'identifiant, et une fuite
y serait sans gravité au sens du RGPD.

### Où le CHU dépose, en pratique

| schéma | ce que ça implique |
|---|---|
| **Le CHU reste chez lui, l'EDS vient chercher** | l'hôpital exporte sur son propre réseau, le pipeline lit à travers une liaison privée. Aucune donnée identifiante ne transite par l'internet public, et l'hôpital garde la main sur ce qu'il expose. |
| **Le CHU dépose dans une zone d'atterrissage cloud** | un partage ou un conteneur dédié, sans accès public, dans le périmètre HDS. Plus simple à exploiter, mais les identités vivent désormais chez l'hébergeur. |

Le premier est le plus courant en France, et le plus facile à défendre devant un
délégué à la protection des données. Le second est celui que cette
infrastructure décrit, parce qu'elle doit pouvoir se déployer seule.

### Un compte de stockage séparé, et pourquoi

`infra/terraform/zone_de_depot.tf` crée un compte **distinct** de celui du lake.
Ce n'est pas de la coquetterie : qui peut lire l'entrepôt ne doit pas pouvoir
lire les identités, et une séparation des droits n'a de sens que si elle porte
sur des objets distincts. Un simple dossier dans le compte du lake partagerait
ses droits.

Le partage est monté **en lecture seule** par le pipeline, avec des permissions
qui interdisent l'écriture jusque dans le système de fichiers du conteneur —
`dir_mode=0555, file_mode=0444`. Le conteneur ne peut pas altérer la source,
même par erreur de programmation.

### La rétention doit être COURTE, et c'est contre-intuitif

Le lake conserve dix ans. La zone de dépôt devrait conserver **quelques jours**.

Une fois le fichier ingéré et pseudonymisé, le brut n'a plus de raison
d'exister : le garder revient à conserver des identités dont on n'a plus
l'usage, ce que le principe de minimisation interdit. C'est l'inverse du
réflexe habituel, qui pousse à tout garder « au cas où ».

**Cette purge n'est pas implémentée**, et il faut le dire. Azure Files n'offre
pas de règle de cycle de vie déclarative : la purge doit être portée par une
tâche planifiée, qui reste à écrire. C'est une dette assumée — la nommer ici, et
dans le fichier Terraform, évite qu'elle se perde.

Une variante mérite d'être étudiée en production : un conteneur objet avec SFTP
activé plutôt qu'un partage SMB. Le CHU y pousserait par SFTP, et la rétention
redeviendrait déclarative — au prix d'un montage plus complexe côté Kubernetes.

### Ce que cette zone impose par ailleurs

- **Tracer chaque lecture.** C'est le seul endroit où la ré-identification est
  possible ; savoir qui y accède, et quand, fait partie du dispositif.
- **Ne jamais l'exposer publiquement.** Ni le compte, ni le partage.
- **La traiter comme un périmètre à part** dans l'analyse d'impact : c'est la
  zone qui porte le risque, les autres n'en portent presque plus.


## 4. Ce qui ne change pas

C'est la partie la plus importante du chapitre, et elle se démontre.

**Toute la configuration tient dans 18 variables d'environnement.** Le code ne
contient aucune adresse en dur : les deux occurrences de `localhost` sont des
**valeurs par défaut** de `os.getenv`, remplacées dès qu'une variable est
fournie.

**La portabilité est déjà éprouvée, tous les jours.** Le conteneur `scheduler`
de `docker-compose.yml` tourne avec `CLICKHOUSE_HOST=clickhouse`,
`EDS_SOURCE_PATH=/filestorage` et `EDS_LAKE_PATH=/app/lake` — un hôte et deux
chemins qui n'existent nulle part sur le poste de développement. Aucune ligne de
code ne distingue ce cas du cas cloud.

| composant | ce qu'il faut changer |
|---|---|
| Connexion au moteur | `CLICKHOUSE_HOST`, `CLICKHOUSE_SECURE=true`, port 8443 |
| Restitution | `METABASE_URL` |
| Secrets | injectés par Kubernetes au lieu d'un fichier `.env` |
| Ordonnancement | un `CronJob` au lieu d'APScheduler |
| **Code Python** | **rien** |

`CLICKHOUSE_SECURE` bascule la liaison en TLS sans toucher au code : le client
lit cette variable et adapte son transport.

## 5. Ce qui change vraiment

### L'ordonnancement passe au cluster

APScheduler tenait la cadence dans un processus qui devait rester vivant.
Kubernetes la tient lui-même, et le conteneur ne vit plus que le temps d'une
exécution — ce qui supprime la question du redémarrage. Les trois réglages du
planificateur local ont chacun leur équivalent :

| en local | sur Kubernetes | ce que cela garantit |
|---|---|---|
| `max_instances = 1` | `concurrencyPolicy: Forbid` | une exécution à la fois |
| `coalesce = true` | `startingDeadlineSeconds` | un seul rattrapage, pas un par créneau perdu |
| `misfire_grace_time = 3600` | `startingDeadlineSeconds: 3600` | un démarrage tardif rattrape le créneau du jour |

Le verrou de fichier reste en place à l'intérieur du conteneur : il continue de
garantir qu'une exécution lancée à la main ne croise pas la planifiée.

### Metabase change de base applicative

En local, Metabase garde son état dans un H2 posé sur un volume. C'est
acceptable pour une démonstration, pas sur Kubernetes où un pod peut être
déplacé à tout moment : H2 ne supporte ni le déplacement à chaud ni deux
écrivains, et la panne se manifeste par une base corrompue plutôt que par une
erreur claire. L'état passe donc sur une **base PostgreSQL managée**.

Cette base ne contient aucune donnée de santé — seulement des définitions de
cartes, des comptes et des permissions. Le chiffrement au repos y est activé
malgré tout : la liste des comptes qui accèdent à un entrepôt de santé mérite la
même protection que l'entrepôt.

### Les journaux sont bornés

Sur l'installation locale, les journaux de ClickHouse pesaient **487 Mio pour
155 Mio de données** — conséquence de centaines d'exécutions. Sur le cluster ils
vivent sur un volume **éphémère borné à 2 Gio** : ils ne sont pas précieux, et
sans borne ils rempliraient le disque avant l'entrepôt.

### Les secrets quittent le fichier `.env`

Le dossier annonçait cette limite : « en production, ce sel appartient à un
coffre, pas à un fichier `.env` ». C'est fait — les secrets sont déclarés dans
le gestionnaire, et injectés dans les pods.

Ils sont **déclarés vides** par Terraform et remplis en dehors de lui. La raison
tient en une phrase : le fichier d'état de Terraform contient en clair tout ce
qu'on lui confie. Y écrire le sel de pseudonymisation reviendrait à le publier.

Une exception est assumée, parce qu'elle est inévitable : le mot de passe de la
base managée, que Terraform **doit** fournir à la création. Il figure donc dans
l'état, et la conséquence est tirée plutôt qu'ignorée — **l'état lui-même est un
secret**, il ne va pas dans git, il vit dans un stockage distant chiffré, et son
accès se traite comme celui de la base.

## 6. Comment cette infrastructure se vérifie

Une infrastructure décrite mais invérifiable ne vaut guère mieux qu'un schéma.
Trois niveaux, du plus accessible au plus coûteux.

### Niveau 1 — sans compte, hors ligne

```bash
make infra
```

`terraform validate` confronte la configuration au **schéma réel du
fournisseur** : noms de ressources, attributs, types, blocs imbriqués.
`kubeconform` confronte les manifestes aux schémas de l'API Kubernetes. Ni l'un
ni l'autre ne demande de compte, et le second tourne en conteneur — rien à
installer.

Cette vérification **mord**, et cela a été éprouvé : un attribut Terraform mal
nommé et un `schedule` mal orthographié dans le `CronJob` font tomber la cible,
avec un message qui désigne la ligne.

**Ce qu'elle ne vérifie pas**, et qu'il faut dire : les chaînes libres. Les noms
de jeux de permissions IAM et les gabarits de nœuds n'existent que côté API ; la
validation en contrôle la syntaxe, pas l'existence.

### Niveau 2 — un plan, sans rien créer

`terraform plan` demande un compte mais ne crée aucune ressource et ne coûte
rien. Il confronte la configuration à l'**API**, donc il attrape précisément ce
que le niveau 1 laisse passer : gabarits inexistants, noms de rôles erronés,
quotas insuffisants.

**Exécuté sur la souscription du projet**, le plan aboutit :

```
Plan: 19 to add, 0 to change, 0 to destroy.
```

| ressource | rôle |
|---|---|
| `resource_group` | tout l'entrepôt y vit, rien ailleurs |
| `virtual_network` + `subnet` | le réseau privé |
| `kubernetes_cluster` | 2 nœuds, 4 vCPU sur les 6 du quota |
| `container_registry` | l'image du pipeline |
| `key_vault` + `key_vault_secret` | le coffre, et le seul secret que Terraform connaisse |
| `storage_account` + `share` + secret | **la zone de dépôt, sur un compte séparé** |
| `postgresql_flexible_server` + `database` | l'état de Metabase |
| 3 × `role_assignment` | moindre privilège : lire le coffre, tirer l'image |
| `random_password` | le mot de passe de la base, généré |

Ce que ce plan démontre et que la validation hors ligne ne pouvait pas : les
noms de rôles — `Key Vault Secrets User`, `AcrPull` — existent, le gabarit
`Standard_B2s_v2` est disponible dans la région, et la souscription accepte
chacune de ces créations.

### Niveau 3 — déployer, capturer, détruire

`apply`, puis captures, puis `destroy`. Le journal de la destruction est
lui-même une pièce : il prouve qu'aucune ressource n'est restée orpheline, ni
aucun disque portant des données de santé.

> **Sur l'accès en direct.** Ouvrir l'environnement déployé à un tiers serait
> contradictoire avec le sujet même de ce projet. Une infrastructure de
> démonstration portant des données de santé se détruit après usage ; c'est ce
> qu'on ferait pour un vrai CHU, et c'est ce que nous recommandons.

## 7. Le plan de migration

Chaque étape est **réversible** et laisse l'installation locale intacte.

| # | étape | vérification |
|---|---|---|
| 1 | Contracter chez un hébergeur certifié HDS, vérifier le périmètre par service | contrat |
| 2 | `terraform apply` — réseau, zone de dépôt, secrets, cluster, registre | `terraform plan` vide ensuite |
| 3 | Déposer les secrets dans le gestionnaire, hors de Terraform | lecture depuis un pod |
| 4 | Construire et pousser l'image en `linux/amd64` | `docker pull` depuis le cluster |
| 5 | Appliquer les manifestes, dans l'ordre | `kubectl get pods` |
| 6 | `eds init`, `eds acces`, `eds metabase` | 41 contrôles de cloisonnement |
| 7 | Charger un premier dépôt, comparer les indicateurs au local | les sept KPI identiques |
| 8 | Activer le `CronJob` | une exécution nocturne journalisée |

**L'étape 7 est celle qui compte.** Le pipeline étant déterministe et
reconstruisant silver et gold à chaque passage, les mêmes fichiers doivent
produire exactement les mêmes chiffres — 6 729 séjours, DMS 5,15 j, 3 314
relevés en alerte. Un écart signalerait une différence d'environnement, pas une
différence de données.

## 8. Ce que cela coûte

Les tarifs changent : ce chapitre donne la **structure** du coût plutôt que des
montants qui seraient périmés à la lecture.

| poste | ce qui le détermine | ordre |
|---|---|---|
| Nœuds Kubernetes | 3 nœuds en permanence | **dominant** |
| Base managée | un nœud, doublé en production | notable |
| Stockage | disque du lake et partage de dépôt — 3,3 Mo aujourd'hui | négligeable |
| Gestionnaire de secrets | quelques secrets | négligeable |
| Sortie réseau | consultation des tableaux de bord | faible |

**Le constat le plus utile n'est pas un montant.** Le pipeline s'exécute en
**une seconde et demie par jour** : son propre calcul est gratuit à toute échelle
raisonnable. Ce qui coûte, c'est de **garder le moteur et la restitution
disponibles** le reste du temps — soit 99,998 % d'un cluster dimensionné pour
une charge qui n'arrive jamais.

Trois pistes en découlent, par ordre de gain :

1. **Mutualiser le cluster** avec d'autres applications du CHU. C'est de loin la
   plus rentable : l'entrepôt paie alors sa part et non un cluster entier.
2. **Réduire le pool hors heures ouvrées.** La recherche et le pilotage se
   consultent en journée ; le pipeline tourne à 2 h et pourrait réveiller le
   cluster.
3. **Ne pas dimensionner sur le pipeline.** Il ne dicte rien : c'est la
   consultation simultanée des tableaux de bord qui décide de la taille.

## 9. Ce qui reste à faire

### Porter le lake sur stockage objet — non fait, et pourquoi

**Aujourd'hui le lake n'est PAS sur du stockage objet.** Il vit sur un **disque
managé** attaché au cluster : un vrai système de fichiers, où `eds/lake.py` fonctionne sans modification. C'est ce qui
permet de déployer sans toucher au code.

**Nous n'avons délibérément pas provisionné de stockage objet inutilisé.** Une
infrastructure décrit ce qui tourne ; provisionner un conteneur que rien n'écrit
créerait une ressource facturée, sauvegardée et auditée pour rien — et
laisserait croire que le lake y vit.

**Ce choix a une conséquence qu'il faut assumer.** Un disque managé se monte en
accès exclusif : il n'est attaché qu'à un nœud à la fois. Le pipeline est donc
**épinglé à un nœud**, et la perte de ce nœud rend le lake indisponible jusqu'à
ce que le volume soit rattaché ailleurs.

À la volumétrie observée — 3,2 Mo de lake — c'est un risque acceptable : le lake
se reconstruit intégralement depuis la source du CHU, qui vit ailleurs, et la
reconstruction prend une seconde et demie. Mais c'est bien la raison principale
de passer au stockage objet le jour venu, davantage que la durabilité ou le
coût : **découpler le pipeline d'un nœud**.

#### Pourquoi ne pas simplement monter un conteneur objet comme un disque

C'est la fausse bonne idée, et elle mérite d'être écartée explicitement. Azure
sait présenter un conteneur objet comme un système de fichiers ; le pipeline
tournerait alors sans une ligne de changement.

Il **casserait pourtant silencieusement** la garantie sur laquelle repose le
lake. Ces passerelles émulent le renommage par une copie suivie d'une
suppression — donc **pas atomiquement**. Un fichier pourrait apparaître sous son
nom définitif alors que sa copie n'est pas terminée, et `est_publie()`
conclurait à tort qu'il est complet. Le pipeline ne lèverait aucune erreur : il
chargerait un fichier tronqué.

Une garantie que l'on croit tenir et qui ne tient plus est pire qu'une garantie
absente. Le portage doit donc passer par le **client objet**, qui rend
l'atomicité nativement — et non par un montage qui la simule.

#### Le jour où le portage aura lieu, les `.partiel` disparaîtront

Ce qui suit décrit une situation **future**. Tant que le lake est un disque, le
mécanisme actuel reste en place et reste nécessaire.

Aujourd'hui, une copie s'écrit sous `<nom>.partiel` puis est **renommée**. Le
renommage étant atomique, un fichier présent sous son nom définitif est
forcément complet, et une copie interrompue laisse un résidu que `eds lake`
efface au démarrage. **C'est ce qui tourne, en local comme sur le cluster.**

Le jour où le lake passera en stockage objet, cette précaution deviendra
**inutile** — et c'est une bonne nouvelle, pas une difficulté :

| | système de fichiers | stockage objet |
|---|---|---|
| écriture | visible au fur et à mesure | **invisible jusqu'à la validation** |
| publication | renommage atomique | la validation *est* atomique |
| écriture interrompue | laisse un fichier tronqué | ne laisse **rien de visible** |
| résidus à nettoyer | les `.partiel` | aucun |

Un envoi en une requête remplace le blob entièrement : un lecteur voit l'ancien
ou le nouveau, jamais un état intermédiaire. Un envoi en blocs, pour les gros
fichiers, dépose des blocs qui **n'apparaissent pas** tant qu'ils ne sont pas
validés, et cette validation est elle aussi atomique. Des blocs jamais validés
restent invisibles au listage et sont purgés par le fournisseur au bout de sept
jours.

Autrement dit, la garantie que le lake construit aujourd'hui à la main — *ce qui
porte son nom définitif est complet* — serait alors **rendue par le stockage
lui-même**. On écrirait directement sous le nom final, et `nettoyer_residus()`
n'aurait plus rien à nettoyer.

#### Ce qui reste réellement à faire

- **La vérification d'existence devient un appel réseau.** C'est elle qui rend
  un lake purgé auto-réparable ; elle passe d'un `stat` local à une requête, avec
  la latence et le mode de panne que cela ajoute.
- **Une dépendance s'ajoute** au projet, qui n'en compte aujourd'hui que six
  directes.
- **Une écriture concurrente mérite d'être bornée.** Le verrou de fichier ne
  protège que d'un second processus sur la même machine ; une condition
  d'écriture — n'écrire que si l'objet n'existe pas — rendrait la publication
  sûre même entre deux nœuds.

En attendant, le lake reste sur un volume persistant — ce qui fonctionne, et où
le mécanisme des `.partiel` garde tout son sens, puisqu'il s'agit d'un vrai
système de fichiers.

### Ce que nous ne recommandons pas

**Migrer ClickHouse vers une base managée.** Aucun hébergeur français certifié
HDS n'en propose. Les alternatives managées imposeraient de réécrire les
transformations, qui représentent l'essentiel de la valeur du projet.

**Multiplier les répliques de ClickHouse** avant d'en avoir le besoin. Une seule
instance tient largement la volumétrie observée, et la réplication ajoute une
classe entière de pannes.

**Ouvrir l'API du cluster.** L'ACL ferme l'accès quand aucune adresse n'est
déclarée. C'est délibérément l'inverse du défaut habituel : sur un entrepôt de
santé, un oubli doit fermer.
