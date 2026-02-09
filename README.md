# GestionFile — serveur de gestion de file d’attente (pharmacie)

Ce dépôt contient le **serveur** (Flask + Socket.IO) d’une solution de gestion de file d’attente pour pharmacie.
Les différentes apps (comptoirs, écran d’affichage, borne/patient/phone) se connectent au serveur pour afficher/mettre à jour les informations.

## Prérequis

- Python 3.10+
- Une base de données :
  - **MySQL** (par défaut), ou
  - **SQLite** (mode simple / dev)
- (Optionnel) **RabbitMQ** si `USE_RABBITMQ` est activé

## Configuration (variables d’environnement)

Le projet utilise `python-dotenv` : tu peux créer un fichier `.env` à la racine (il est ignoré par git).

### Variables minimales (MySQL)

Exemple :

```dotenv
DATABASE_TYPE=mysql
SITE=prod

MYSQL_HOST=localhost
MYSQL_DATABASE=queuedatabase
MYSQL_USER=admin
MYSQL_PASSWORD=change-me

RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F
BASE32_KEY=change-me-too

# Prod: mettre des secrets explicites (recommandé)
SECRET_KEY=change-me
SECURITY_PASSWORD_SALT=change-me
```

### Démarrage sans MySQL (SQLite)

Si MySQL n’est pas disponible sur la machine, tu peux démarrer en SQLite :

```dotenv
DATABASE_TYPE=sqlite
```

## Dépannage MySQL

Erreur typique :

- `Can't connect to MySQL server on 'localhost' ([Errno 10061] WSAECONNREFUSED)`

Signifie que MySQL **n’écoute pas** sur le port (souvent service arrêté). Sur Windows, vérifie le service MySQL (ex: `MySQL80`) dans `services.msc` et démarre-le avec les droits admin, ou lance ton MySQL via Docker.

### Mode debug

Le debug n’est **plus forcé** dans le code.

- `FLASK_DEBUG=1` → debug Flask + logs DEBUG
- sinon → logs INFO

## Socket.IO (CORS)

Par défaut, Socket.IO n’accepte que le **same-origin** (plus de `cors_allowed_origins="*"`).

Si tu dois autoriser des clients web depuis d’autres origines (reverse-proxy, domaine différent, etc.), définis :

```dotenv
SOCKETIO_CORS_ALLOWED_ORIGINS=https://example.com,https://intranet.example.com
```

En dev uniquement, tu peux mettre `*` :

```dotenv
FLASK_DEBUG=1
SOCKETIO_CORS_ALLOWED_ORIGINS=*
```

## Secrets (IMPORTANT)

Si `SECRET_KEY` / `SECURITY_PASSWORD_SALT` ne sont **pas** fournis via variables d’environnement, le serveur :

1) génère une valeur aléatoire,
2) la persiste dans `instance/` pour qu’elle reste stable entre redémarrages :

- `instance/flask_secret_key.txt`
- `instance/security_password_salt.txt`

Ces fichiers sont ignorés par git (voir `.gitignore`).

Recommandation : pour une distribution “propre”, définis toujours `SECRET_KEY` et `SECURITY_PASSWORD_SALT` via variables d’environnement (ou secret manager), et **ne partage jamais** un `.env` contenant des secrets.

## Lancer en local (sans Docker)

1) Installer les dépendances :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) Définir les variables (`.env`) puis démarrer :

```powershell
python app.py
```

Le serveur écoute sur le port exposé par l’app (voir `app.py`).

## Lancer avec Docker

Le repo contient un `Dockerfile` et un `docker-compose.yaml`.

```powershell
docker compose up --build
```

Note : selon ton environnement, tu devras ajuster `docker-compose.yaml` (ex: URL RabbitMQ, ajout d’un service MySQL/RabbitMQ, volumes, etc.).

## Tests

- `tests/test_basic.py` : test simple Flask
- `tests/test_add_patient.py` : test E2E Playwright (nécessite un serveur lancé + MySQL configuré)

## Structure (grandes lignes)

- `app.py` : création de l’app Flask + Socket.IO + routes
- `routes/` : blueprints (admin, comptoir, patient, etc.)
- `python/engine.py` : logique “métier” (appel patient, audio, QR, etc.)
- `models.py` : modèles SQLAlchemy

## Deploiement simplifie

Pour une installation "copier-coller" avec 2 options d'hebergement:

- VPS + Coolify (recommande): `docs/DEPLOYMENT.md` + `docker-compose.coolify.yaml`
- PaaS Render: `docs/DEPLOYMENT.md` + `render.yaml`

Fichiers fournis:

- `.env.example` : variables d'environnement de base
- `render.yaml` : blueprint Render
- `Procfile` : fallback PaaS
- `docker-compose.coolify.yaml` : compose universel (MySQL/RabbitMQ externes par defaut, profil `bundled` en tout-en-un) + split `web`/`scheduler`
