# Entrepôt de Données de Santé — CHU

Projet fil rouge · Module Big Data · M2 · Épreuve E05 (BC05, compétences C27 → C31)

Construction d'un entrepôt de données de santé pour un CHU, depuis le dépôt quotidien
de fichiers hétérogènes jusqu'aux tableaux de bord de pilotage et de recherche clinique,
avec une chaîne de traitement automatisée et conforme au RGPD.

## Architecture

Patron médaillon, transformation en SQL dans le moteur (ELT) :

```
filestorage  →  lake  →  bronze  →  silver  →  gold  →  dashboards
(CHU, RO)     (copie +   (typé)    (nettoyé,  (KPI par   (Metabase)
            pseudonymisé)          dédupliqué) usage)
```

| Composant | Rôle |
|---|---|
| Python | orchestration : copie des fichiers, envoi du SQL, planification |
| ClickHouse | entrepôt colonnaire — c'est lui qui transforme |
| Metabase | restitution : dashboards Pilotage, Recherche, Exploitation |

Les transformations ne sortent jamais du moteur : Python pilote, il ne calcule pas.

## Prérequis

- Docker et Docker Compose
- Python ≥ 3.11

## Installation

Le dépôt se suffit à lui-même : les fichiers du CHU y sont versionnés, il n'y a
rien à récupérer ailleurs.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # les dépendances, et la commande « eds »
make env                      # écrit .env : sel et mots de passe tirés au sort
docker compose up -d          # ClickHouse, Metabase, planificateur
eds init                      # bases, tables, journal des exécutions
eds run                       # la chaîne : lake → bronze → silver → gold
eds acces                     # comptes cloisonnés, et la preuve du cloisonnement
eds metabase                  # connexions et tableaux de bord
```

Sous Windows, seules les deux premières lignes changent — `make` n'y est pas,
et le script qu'il appelle ne dépend que de la bibliothèque standard :

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e .
py docs\outils\generer_env.py    # l'équivalent de « make env »
docker compose up -d
```

La suite (`eds init`, `eds run`, `eds acces`, `eds metabase`) est identique.

Les quatre commandes `eds` sont l'installation complète : `eds run` remplit
l'entrepôt, `eds acces` crée les comptes par usage, `eds metabase` publie les
tableaux de bord. Sans `eds acces`, le cloisonnement n'existe pas encore et la
suite de tests le signale.

L'ordre compte : `eds metabase` a besoin des comptes que `eds acces` vient de
créer. Au premier passage, `eds acces` ne peut donc vérifier que le moteur ;
le rejouer une fois les tableaux de bord publiés couvre aussi la restitution,
soit 41 contrôles.

`make env` refuse d'écraser un `.env` existant : changer `EDS_SALT` romprait la
continuité des pseudonymes déjà chargés.

Chaque étape se rejoue aussi seule — `eds lake`, `eds bronze`, `eds silver`,
`eds gold` — et `eds status` donne l'état des dépôts et des exécutions.
Pour activer le garde-fou RGPD sur les commits : `git config core.hooksPath .githooks`.

- ClickHouse : http://localhost:8123/play
- Metabase : http://localhost:3000

Le pipeline tourne ensuite **seul** : le service `scheduler` déclenche `eds run`
selon `EDS_PLANIFICATION` (par défaut chaque nuit à 2 h, heure de Paris).

```bash
docker compose logs -f scheduler                  # le suivre en direct
EDS_PLANIFICATION="*/2 * * * *" docker compose up -d scheduler   # accélérer, pour une démonstration
```

## Les données

Le dépôt quotidien du CHU est versionné dans `source-filestorage/`, à la demande
du commanditaire : cloner puis lancer suffit, sans dépendance à un chemin externe.

Chaque flux a son propre calendrier — 92 fichiers, 3,3 Mo :

| flux | dépôts | période | volume |
|---|---:|---|---:|
| `sejours/<date>/sejours.csv` | 28 | 2026-08-01 → 08-28 | 6 797 séjours |
| `diagnostics/<date>/diagnostics.json` | 28 | 2026-08-01 → 08-28 | 12 720 codes, JSON imbriqué |
| `monitoring/<date>/monitoring.parquet` | 28 | 2026-08-01 → 08-28 | 41 778 relevés |
| `patients/<date>/patients.csv` | 3 | 2026-08-26 → 08-28 | 6 000 patients, en instantanés |
| `referentiels/<date>/{services,cim10}.csv` | 1 | 2026-08-01 | 8 services, 13 codes CIM-10 |
| `actes/<date>/actes.parquet` | 1 | 2026-08-29 | 8 112 actes médicaux |
| `referentiels/<date>/{description_service,ccam}.csv` | 1 | 2026-08-29 | 7 services décrits, 8 actes CCAM |

Les deux derniers flux sont arrivés après coup, et n'ont demandé aucune
adaptation du socle : la découverte teste l'existence de chaque fichier et
ignore les dates où il manque, si bien qu'un référentiel qui arrive en cours de
route s'ingère comme les autres — et seuls les 3 fichiers nouveaux ont été
copiés dans le lake.

Le détail des volumes et leur réconciliation couche par couche sont dans
[la validation des chiffres](docs/VALIDATION.md).

Ces fichiers sont **synthétiques**, et le commanditaire a confirmé par écrit
qu'ils ne portent aucune donnée réelle. Le pipeline les traite néanmoins comme
s'ils étaient réels : ils contiennent des colonnes d'identité (nom, prénom, NIR),
et celles-ci sont supprimées à l'entrée du lake, jamais rechargées ensuite. Le
crochet `pre-commit` continue de refuser toute donnée identifiante ailleurs dans
le dépôt — `source-filestorage/` est la seule exception, explicite et bornée.

En production ce répertoire n'aurait pas sa place ici : la source resterait un
espace en lecture seule extérieur au projet, désigné par `EDS_SOURCE_PATH`. Le
code ne fait pas la différence entre les deux — seule cette variable change.

Les fichiers sont conservés **octet pour octet** : `.gitattributes` interdit à
Git de normaliser leurs fins de ligne, faute de quoi un dépôt cloné recevrait
une version réécrite et les contrôles de copie fidèle du lake tomberaient.

La suite de tests, elle, ne dépend pas d'eux : `tests/fixtures/` contient des
échantillons réduits.

## Conformité

| Exigence | Mise en œuvre |
|---|---|
| Pseudonymisation | HMAC-SHA256 salé sur `patient_id`, appliqué **à l'entrée du lake** — déterministe (les jointures survivent), non réversible sans le sel |
| Minimisation | `nom`, `prenom`, `nir` supprimés ; `birth_date` généralisée en `birth_year` |
| Cloisonnement | trois usages, trois comptes ClickHouse, trois collections Metabase ; `eds acces` en fait la démonstration (41 contrôles) |
| Petits effectifs | seuil k = 5 **scellé dans les vues** de recherche, en `SQL SECURITY DEFINER` — ni paramétrable, ni contournable |
| Traçabilité | `_batch_id` sur chaque ligne → `ops.run_log` → `ops.ingestion_log` → chemin du fichier source, taille, date de dépôt et horodatage |
| Reprise sur incident | écriture sous nom provisoire puis renommage atomique : à son emplacement définitif, un fichier est toujours complet |
| Garde-fou outillé | `.githooks/pre-commit` refuse tout commit contenant un NIR ou une zone de données |
| Liaison chiffrée | `CLICKHOUSE_SECURE=true` bascule en TLS ; le passage à un moteur managé ne touche pas au code |
| Conteneur sans privilèges | l'orchestrateur tourne sous un compte dédié, jamais en root |
| Dépendances figées | `requirements.lock` épingle les versions résolues, pour que deux constructions donnent la même image |

Le sel (`EDS_SALT`) n'est pas versionné : une réinstallation produit des pseudonymes
différents des nôtres. C'est le comportement attendu — voir le
[guide d'exploitation](docs/EXPLOITATION.md).

## Structure

```
sql/         DDL et transformations bronze → silver → gold
config/      flux sources et règles métier (seuils, bornes, fenêtres)
eds/         orchestrateur — un module par étape du pipeline :
               lake · bronze · silver · gold, plus config, execution,
               pseudonymize, sql, verrou, access, metabase, cli

             silver.py et gold.py sont volontairement minces : ces couches se
             transforment en SQL, dans sql/. Trente lignes de Python en face de
             trois cents de SQL, c'est la preuve que le calcul est resté dans
             le moteur.
tests/       tests des règles métier et de la pseudonymisation
docs/        DOSSIER.md (Partie 1 + évolution) · EXPLOITATION.md (Partie 2)
             VALIDATION.md (réconciliation et recalculs) · captures
             outils/ reconcilier.py, generer_env.py, capture_terminal.py,
                     verrouiller.py
sql/dashboards/  requêtes des cartes, versionnées
config/dashboards.yml  spécification des tableaux de bord
```

## Les deux livrables

Le rendu se compose du dépôt et d'un rapport lisible sans lui. Les deux se
fabriquent d'une commande, et sortent hors du dépôt — un livrable ne se
versionne pas lui-même :

```bash
make livrables        # ../rendu/rapport-eds-chu.pdf + ../rendu/eds-chu-depot.zip
```

Le **rapport** assemble les trois documents de `docs/`, images et schémas
compris, en un PDF d'une quarantaine de pages. Il est produit *depuis* ces
documents, donc il ne peut pas en diverger. Chrome l'imprime en mode headless :
c'est le seul moteur qui rende les schémas Mermaid.

L'**archive** est faite par `git archive`, et non par `zip` : seul ce qui est
versionné y entre — ni `.venv`, ni `lake/`, ni `logs/`, ni `.env`. Le correcteur
y trouve exactement ce qu'un `git clone` lui donnerait, données du CHU comprises.

## Documentation

- [Dossier de conception](docs/DOSSIER.md) — Partie 1 (besoin, architecture, traitements, indicateurs, limites) et l'évolution demandée par le CHU
- [Guide d'exploitation](docs/EXPLOITATION.md) — lancement, planification, reprise sur incident
- [Validation des chiffres](docs/VALIDATION.md) — réconciliation source ↔ entrepôt, recalcul manuel des indicateurs, confrontation au corrigé, justification de l'évolution, limites chiffrées
