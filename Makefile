# Raccourcis d'exploitation. Les commandes restent utilisables telles quelles
# sans make ; voir docs/EXPLOITATION.md.
.PHONY: aide socle pipeline tests verrou capture

aide:
	@echo "socle     démarre ClickHouse, Metabase et le planificateur"
	@echo "pipeline  joue la chaîne complète depuis l'hôte"
	@echo "tests     lance la suite"
	@echo "verrou    régénère requirements.lock, empreintes comprises"
	@echo "capture   régénère l'image de la démonstration du cloisonnement"

socle:
	docker compose up -d

pipeline:
	./.venv/bin/eds run

tests:
	./.venv/bin/python -m pytest tests/ -q

# Les empreintes sont calculées dans l'image CIBLE : une roue Linux n'a pas la
# même empreinte que son équivalent macOS, et pip les refuserait.
verrou:
	docker run --rm -v "$(PWD):/w" -w /w python:3.12-slim sh -c '\
	  pip install -q --upgrade pip >/dev/null 2>&1; \
	  pip download --only-binary :all: --no-deps -q -d /tmp/roues -r requirements.txt && \
	  python docs/outils/verrouiller.py /tmp/roues requirements.lock'

capture:
	./.venv/bin/eds acces > docs/img/acces.txt || true
	./.venv/bin/python docs/outils/capture_terminal.py docs/img/acces.txt docs/img/cloisonnement-eds-acces.png
	@rm -f docs/img/acces.txt
