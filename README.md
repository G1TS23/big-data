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

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # → EDS_SALT dans .env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath .githooks                          # garde-fou RGPD
docker compose up -d
eds init                                                     # bases, tables, traçabilité
eds lake                                                     # filestorage → lake, pseudonymisé
eds bronze                                                   # lake → tables typées
eds silver                                                   # modèle métier fiable, en SQL
eds gold                                                     # indicateurs, cloisonnés par usage
eds acces                                                    # comptes + preuve du cloisonnement
eds metabase                                                 # connexions et tableaux de bord
eds run                                                      # la chaîne complète, en une commande
eds status                                                   # état des dépôts et des runs
```

- ClickHouse : http://localhost:8123/play
- Metabase : http://localhost:3000

Le pipeline tourne ensuite **seul** : le service `scheduler` déclenche `eds run`
selon `EDS_PLANIFICATION` (par défaut chaque nuit à 2 h, heure de Paris).

```bash
docker compose logs -f scheduler                  # le suivre en direct
EDS_PLANIFICATION="*/2 * * * *" docker compose up -d scheduler   # accélérer, pour une démonstration
```

## Les données ne sont pas dans ce dépôt

Le CHU dépose ses fichiers dans un espace en **lecture seule**, extérieur au projet.
Ces fichiers contiennent l'identité réelle des patients (nom, prénom, NIR) : ils ne
sont **jamais** versionnés, jamais copiés tels quels, et l'identité est supprimée dès
l'entrée du lake.

Renseigner leur emplacement dans `.env` :

```
EDS_SOURCE_PATH=../eds-chu-sujet/source-filestorage
```

Attendu à ce chemin :

```
source-filestorage/
├── patients/<AAAA-MM-JJ>/patients.csv
├── sejours/<AAAA-MM-JJ>/sejours.csv
├── diagnostics/<AAAA-MM-JJ>/diagnostics.json
├── monitoring/<AAAA-MM-JJ>/monitoring.parquet
└── referentiels/<AAAA-MM-JJ>/{services,cim10}.csv
```

La suite de tests, elle, tourne sans ces données : `tests/fixtures/` contient des
échantillons synthétiques et anonymisés.

## Conformité

| Exigence | Mise en œuvre |
|---|---|
| Pseudonymisation | HMAC-SHA256 salé sur `patient_id`, appliqué **à l'entrée du lake** — déterministe (les jointures survivent), non réversible sans le sel |
| Minimisation | `nom`, `prenom`, `nir` supprimés ; `birth_date` généralisée en `birth_year` |
| Cloisonnement | trois usages, trois comptes ClickHouse, trois collections Metabase ; `eds acces` en fait la démonstration (38 contrôles) |
| Petits effectifs | seuil k = 5 **scellé dans les vues** de recherche, en `SQL SECURITY DEFINER` — ni paramétrable, ni contournable |
| Traçabilité | `_batch_id` sur chaque ligne → `ops.run_log` → `ops.ingestion_log` → chemin du fichier source, taille, date de dépôt et horodatage |
| Reprise sur incident | écriture sous nom provisoire puis renommage atomique : à son emplacement définitif, un fichier est toujours complet |
| Garde-fou outillé | `.githooks/pre-commit` refuse tout commit contenant un NIR ou une zone de données |

Le sel (`EDS_SALT`) n'est pas versionné : une réinstallation produit des pseudonymes
différents des nôtres. C'est le comportement attendu — voir `docs/EXPLOITATION.md`.

## Structure

```
sql/         DDL et transformations bronze → silver → gold
config/      flux sources et règles métier (seuils, bornes, fenêtres)
eds/         orchestrateur — un module par étape du pipeline :
               lake · bronze · silver · gold, plus config, execution,
               pseudonymize, sql, access, metabase, cli

             silver.py et gold.py sont volontairement minces : ces couches se
             transforment en SQL, dans sql/. Trente lignes de Python en face de
             trois cents de SQL, c'est la preuve que le calcul est resté dans
             le moteur.
tests/       tests des règles métier et de la pseudonymisation
docs/        DOSSIER.md (Partie 1) · EXPLOITATION.md (Partie 2) · captures
sql/dashboards/  requêtes des cartes, versionnées
config/dashboards.yml  spécification des tableaux de bord
```

## Documentation

- `docs/DOSSIER.md` — besoin, architecture justifiée, traitements, indicateurs, limites
- `docs/EXPLOITATION.md` — lancement, planification, reprise sur incident
