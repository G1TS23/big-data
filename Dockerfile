# Conteneur de l'orchestrateur. Il n'exécute pas de calcul : il copie des
# fichiers et envoie du SQL. Une image mince suffit.
FROM python:3.12-slim

WORKDIR /app

# Les dépendances d'abord : elles changent moins souvent que le code, et Docker
# réutilise cette couche à chaque reconstruction.
COPY requirements.txt pyproject.toml ./
COPY eds ./eds
RUN pip install --no-cache-dir -e .

# Déclarations et scripts. Montés en lecture seule par docker-compose en
# développement, embarqués ici pour que l'image soit autonome.
COPY config ./config
COPY sql ./sql

# Les journaux doivent sortir immédiatement, sans tampon : sans cela
# `docker compose logs` ne montrerait rien tant que le tampon n'est pas plein.
ENV PYTHONUNBUFFERED=1

CMD ["eds", "scheduler"]
