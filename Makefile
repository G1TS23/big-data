# Raccourcis d'exploitation. Les commandes restent utilisables telles quelles
# sans make ; voir docs/EXPLOITATION.md.
.PHONY: aide env socle pipeline tests verrou capture

aide:
	@echo "env       écrit .env, sel et mots de passe tirés au sort"
	@echo "socle     démarre ClickHouse, Metabase et le planificateur"
	@echo "pipeline  joue la chaîne complète depuis l'hôte"
	@echo "tests     lance la suite"
	@echo "verrou    régénère requirements.lock, empreintes comprises"
	@echo "capture   régénère l'image de la démonstration du cloisonnement"

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
