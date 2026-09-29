# Image de base Python. python:3.10.4 (Debian bullseye) n'est plus
# constructible : les paquets curl de sa branche security ont été retirés
# des miroirs (404 au build). requirements.txt est résolu pour Python 3.12 ;
# slim-bookworm fournit une base à jour sans les outils de compilation.
FROM python:3.12-slim-bookworm

# Définissez le répertoire de travail dans le conteneur
WORKDIR /app

# curl is used by container healthchecks (docker-compose.coolify.yaml).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copiez le fichier requirements.txt et installez les dépendances
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copiez le reste de l'application
COPY . .

# Copier les médias par défaut (images boutons, drapeaux, annonces, sons)
# vers un dossier hors volume. docker-entrypoint.sh les restaurera dans les
# volumes persistants au premier démarrage (sans écraser les uploads).
RUN mkdir -p /app/default_media && \
    cp -r static/images /app/default_media/images && \
    cp -r static/audio /app/default_media/audio

# Rendre l'entrypoint exécutable.
RUN chmod +x docker-entrypoint.sh

# Exposez le port sur lequel l'application Flask s'exécute
EXPOSE ${PORT:-5000}

# Entrypoint : synchronise les médias par défaut vers les volumes vides,
# puis lance la commande (migrations + app).
ENTRYPOINT ["./docker-entrypoint.sh"]

# Appliquer les migrations puis lancer l'application Flask
CMD ["sh", "-c", "python manage.py migrate && python app.py"]
