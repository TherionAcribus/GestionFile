# Utilisez une image de base Python
FROM python:3.10.4

# Définissez le répertoire de travail dans le conteneur
WORKDIR /app

# Copiez le fichier requirements.txt et installez les dépendances
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copiez le reste de l'application
COPY . .

# Exposez le port sur lequel l'application Flask s'exécute
EXPOSE ${PORT:-5000}

# Commande pour exécuter l'application Flask
CMD ["python", "app.py"]