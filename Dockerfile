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

# Appliquer les migrations puis lancer l'application Flask
CMD ["sh", "-c", "flask db upgrade && python app.py"]