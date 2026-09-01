# Conteneur de l'orchestrateur. Il n'exécute pas de calcul : il copie des
# fichiers et envoie du SQL. Une image mince suffit.
FROM python:3.12-slim

# Un compte sans privilèges. L'image officielle tourne en root par défaut ;
# pour un pipeline qui manipule des données de santé, une évasion du conteneur
# ne doit pas débuter avec les droits d'administrateur.
RUN useradd --create-home --uid 10001 eds

WORKDIR /app

# La SEULE installation de l'image, et elle est verrouillée sur trois plans :
#   --require-hashes      chaque paquet doit correspondre à son empreinte, ce
#                         qui ferme la porte à la substitution en amont ;
#   --only-binary :all:   aucune archive source, donc aucun script setup.py
#                         exécuté pendant la construction ;
#   requirements.lock     versions résolues, pas des minima : deux
#                         constructions à des semaines d'écart sont identiques.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --only-binary :all: -r requirements.lock

# Le code du projet n'est pas installé par pip : il est copié, et rendu
# importable par PYTHONPATH. Une seconde invocation de pip n'apporterait rien —
# les dépendances sont déjà là — et ouvrirait une résolution non verrouillée.
COPY eds ./eds
COPY config ./config
COPY sql ./sql
ENV PYTHONPATH=/app

# `eds` reste disponible dans le conteneur, sans passer par un point d'entrée
# installé : `docker compose exec scheduler eds status` fonctionne.
RUN printf '#!/bin/sh\nexec python -m eds "$@"\n' > /usr/local/bin/eds \
 && chmod +x /usr/local/bin/eds

# Les journaux doivent sortir immédiatement, sans tampon : sans cela
# `docker compose logs` ne montrerait rien tant que le tampon n'est pas plein.
ENV PYTHONUNBUFFERED=1

RUN chown -R eds:eds /app
USER eds

CMD ["eds", "scheduler"]
