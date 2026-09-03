# Raccourcis d'exploitation. Les commandes restent utilisables telles quelles
# sans make ; voir docs/EXPLOITATION.md.
.PHONY: aide env socle pipeline tests verrou capture rapport livrables infra

aide:
	@echo "env       écrit .env, sel et mots de passe tirés au sort"
	@echo "socle     démarre ClickHouse, Metabase et le planificateur"
	@echo "pipeline  joue la chaîne complète depuis l'hôte"
	@echo "tests     lance la suite"
	@echo "verrou    régénère requirements.lock, empreintes comprises"
	@echo "capture   régénère l'image de la démonstration du cloisonnement"
	@echo "rapport   assemble les documents en un PDF, hors du dépôt"
	@echo "livrables rapport PDF + archive du dépôt, dans ../rendu/"
	@echo "infra     vérifie l'infrastructure : terraform + manifestes k8s"

# N'écrase jamais un .env existant : les secrets en place sont irremplaçables
# (changer EDS_SALT casse la continuité des pseudonymes).
env:
	python3 docs/outils/generer_env.py

socle:
	docker compose up -d

pipeline:
	./.venv/bin/eds run

tests:
	./.venv/bin/python -m pytest tests/ -q

# La résolution se fait dans l'image CIBLE : les versions retenues dépendent de
# la version de Python et du système, pas de la machine qui lance make.
# Sans --no-deps : ce sont les dépendances transitives qu'il faut figer, pas
# seulement les six lignes de requirements.txt.
verrou:
	docker run --rm -v "$(PWD):/w" -w /w python:3.12-slim sh -c '\
	  pip install -q --upgrade pip >/dev/null 2>&1; \
	  pip download --only-binary :all: -q -d /tmp/roues -r requirements.txt && \
	  python docs/outils/verrouiller.py /tmp/roues requirements.lock'

capture:
	./.venv/bin/eds acces > docs/img/acces.txt || true
	./.venv/bin/python docs/outils/capture_terminal.py docs/img/acces.txt docs/img/cloisonnement-eds-acces.png
	@rm -f docs/img/acces.txt

# ─── Les deux livrables ──────────────────────────────────────────────────────
# Le rendu attend le dépôt ET un rapport lisible sans lui. Les deux sortent
# dans ../rendu/, hors du dépôt : un livrable ne se versionne pas lui-même.
rapport:
	python3 docs/outils/rapport.py

# git archive, et non zip : seul ce qui est VERSIONNÉ part: ni .venv, ni lake,
# ni logs, ni .env — les mêmes fichiers que ce qu'un correcteur obtient en
# clonant, ce qui rend l'archive et le dépôt indiscernables.
livrables: rapport
	mkdir -p ../rendu
	git archive --format=zip --prefix=eds-chu/ -o ../rendu/eds-chu-depot.zip HEAD
	@ls -lh ../rendu/ | tail -n +2 | awk '{printf "  %-28s %s\n", $$9, $$5}'

# ─── Vérification de l'infrastructure ────────────────────────────────────────
# Sans compte cloud, et sans rien installer : Terraform valide la configuration
# contre le schéma RÉEL du fournisseur, kubeconform valide les manifestes contre
# les schémas de l'API Kubernetes. C'est ce qui distingue une infrastructure
# décrite d'un YAML seulement plausible.
#
# Ce que cela NE vérifie pas : les chaînes libres — noms de jeux de permissions,
# gabarits de nœuds — qui n'existent que côté API. Seul un « terraform plan »
# contre un compte les confronterait.
infra:
	@echo "── format"
	terraform -chdir=infra/terraform fmt -check -recursive
	@echo "── schéma du fournisseur"
	terraform -chdir=infra/terraform init -backend=false -input=false >/dev/null
	terraform -chdir=infra/terraform validate
	@echo "── manifestes Kubernetes"
	docker run --rm -v "$(PWD)/infra/kubernetes:/w" ghcr.io/yannh/kubeconform:latest \
	  -summary -strict /w
