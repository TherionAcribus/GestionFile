# Utilisez une image de base Python
FROM python:3.10.4

# Définissez le répertoire de travail dans le conteneur
WORKDIR /app

# Copiez le fichier requirements.txt et installez les dépendances
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copiez le reste de l'application
COPY . .

# Ajouter un message spécifique pour vérifier que Docker est utilisé
RUN echo "Building Docker Image"

# Exposez le port sur lequel l'application Flask s'exécute
EXPOSE 5000

# Commande pour exécuter les scripts de test et l'application Flask
CMD ["sh", "-c", "python test_dns.py && python test_connectivity.py && python app.py"]