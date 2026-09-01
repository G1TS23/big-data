# Conteneur de l'orchestrateur. Il n'exécute pas de calcul : il copie des
# fichiers et envoie du SQL. Une image mince suffit.
FROM python:3.12-slim

# Un compte sans privilèges. L'image officielle tourne en root par défaut ;
# pour un pipeline qui manipule des données de santé, une évasion du conteneur
# ne doit pas débuter avec les droits d'administrateur.
RUN useradd --create-home --uid 10001 eds

WORKDIR /app

# Versions RÉSOLUES, figées dans requirements.lock : deux constructions de
# l'image à des semaines d'intervalle installent exactement les mêmes paquets.
# requirements.txt n'exprime que des minima, ce qui suffit au développement
# mais pas à la reproductibilité d'un livrable.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY requirements.txt pyproject.toml ./
COPY eds ./eds
RUN pip install --no-cache-dir --no-deps -e .

# Déclarations et scripts. Montés en lecture seule par docker-compose en
# développement, embarqués ici pour que l'image soit autonome.
COPY config ./config
COPY sql ./sql

# Les journaux doivent sortir immédiatement, sans tampon : sans cela
# `docker compose logs` ne montrerait rien tant que le tampon n'est pas plein.
ENV PYTHONUNBUFFERED=1

RUN chown -R eds:eds /app
USER eds

CMD ["eds", "scheduler"]
