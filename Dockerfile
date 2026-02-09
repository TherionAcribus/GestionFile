# Utilisez une image de base Python
FROM python:3.10.4

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

# Exposez le port sur lequel l'application Flask s'exécute
EXPOSE ${PORT:-5000}

# Appliquer les migrations puis lancer l'application Flask
CMD ["sh", "-c", "SKIP_STARTUP_HOOKS=1 SKIP_EVENTLET_PATCH=1 flask db upgrade && python app.py"]
