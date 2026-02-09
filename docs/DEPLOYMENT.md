# Deploiement Rapide

Ce guide propose 2 chemins de deploiement:

- Option A (recommandee): VPS + Coolify
- Option B: PaaS (Render)

## Prerequis communs

1. Copier `.env.example` vers `.env`.
2. Completer les variables sensibles:
   `MYSQL_*`, `BASE32_KEY`, `APP_SECRET`, `SECRET_KEY`, `SECURITY_PASSWORD_SALT`.
3. Verifier que le port applicatif est `PORT=5000` (ou adapte a ton infra).
4. **La base de donnees MySQL doit etre provisionnee au prealable** (la base
   elle-meme, pas les tables). L'application ne cree pas la base; elle applique
   uniquement les migrations Alembic pour creer/mettre a jour les tables.

## Migrations de base de donnees

Les migrations sont gerees par **Flask-Migrate** (Alembic). Elles sont
executees **automatiquement au demarrage** de l'application :

- **Docker / Coolify / docker-compose** : les roles `web` et `scheduler` executent
  `python manage.py migrate && python app.py`.
- **PaaS (Render, Heroku...)** : le `Procfile` contient egalement
  `python manage.py migrate && python app.py`.
- **Demarrage local** : lancer `python manage.py migrate` avant `python app.py`.

Pourquoi `manage.py migrate` ? Les migrations existantes ne contiennent pas
actuellement une "baseline" fiable pour creer toutes les tables sur une base
totalement vide (ex: la revision initiale `cbc458...` fait un `ALTER TABLE patient`).

Comportement:
- **Base existante avec `alembic_version`**: applique `alembic upgrade` normalement.
- **Base vide**: cree les tables depuis les modeles SQLAlchemy (`db.create_all()`),
  puis "stamp" Alembic sur `head`.

Note: si la base contient deja des tables mais pas `alembic_version`, le script
refuse de "stamper" par defaut (pour eviter de casser une base legacy). Dans ce cas,
utiliser une base vide ou definir `FORCE_BOOTSTRAP_DB=1` si vous savez ce que vous faites.

### Commandes manuelles utiles

```bash
# Appliquer les migrations / bootstrap manuellement
python manage.py migrate

# Voir l'etat actuel des migrations
SKIP_STARTUP_HOOKS=1 SKIP_EVENTLET_PATCH=1 flask db current

# Generer une nouvelle migration apres modification des modeles
SKIP_STARTUP_HOOKS=1 SKIP_EVENTLET_PATCH=1 flask db migrate -m "description du changement"
```

## Option A - VPS + Coolify (recommande)

Objectif: installation la plus simple pour un client final, avec stack complete.

1. Pousser le repo Git.
2. Dans Coolify, creer une application de type `Docker Compose`.
3. Utiliser le fichier `docker-compose.coolify.yaml`.
4. Definir les variables d'environnement dans l'UI Coolify:
   `MYSQL_ROOT_PASSWORD`, `MYSQL_USER`, `MYSQL_PASSWORD`,
   `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
   `SECRET_KEY`, `SECURITY_PASSWORD_SALT`, `APP_SECRET`, `BASE32_KEY`.
5. Deployer. Les migrations de base de donnees s'executent automatiquement
   au demarrage des conteneurs applicatifs.
6. Verifier les probes:
   - Liveness: `GET /healthz` doit renvoyer `200`.
   - Readiness: `GET /readyz` doit renvoyer `200` quand DB (+ RabbitMQ si active) est prete.

Notes:
- Ce compose est **universel**: pas besoin de l'editer selon le client.
- Mode par defaut: services externes (MySQL/RabbitMQ deja provisionnes dans Coolify).
- Mode "tout-en-un": definir `COMPOSE_PROFILES=bundled` pour lancer aussi
  les services `mysql` et `rabbitmq` inclus dans le compose.
- Le compose separe les roles applicatifs:
  - `web` (`APP_ROLE=web`): sert le trafic HTTP.
  - `scheduler` (`APP_ROLE=scheduler`): execute les jobs APScheduler.
- Pour scaler horizontalement, augmenter seulement `web` et garder
  `scheduler` a **1 replica**.
- Si tu utilises un MySQL externe, renseigner `MYSQL_HOST` avec l'host
  interne Coolify de la ressource MySQL.
- Si tu utilises `COMPOSE_PROFILES=bundled`, la base `queuedatabase` est
  creee automatiquement par le conteneur MySQL grace a `MYSQL_DATABASE`.

## Option B - Render (PaaS)

Objectif: garder une option PaaS comme aujourd'hui.

1. Dans Render, creer un deploy `Blueprint`.
2. Selectionner le fichier `render.yaml`.
3. Renseigner les variables marquees `sync: false`.
4. **S'assurer que la base MySQL est provisionnee** (Render ne fournit pas
   MySQL nativement; utiliser un add-on ou un service externe).
5. Deployer. Les migrations s'executent automatiquement via le `Procfile`.
6. Verifier `GET /healthz`.

Notes:
- Le fichier `Procfile` est fourni pour les PaaS qui le lisent.
- Si RabbitMQ est desactive dans l'app, `RABBITMQ_URL` peut etre laisse vide.

## Fichiers fournis

- `.env.example`: exemple de configuration.
- `docker-compose.coolify.yaml`: stack VPS/Coolify universelle
  (mode externe par defaut, mode bundled via profil).
- `render.yaml`: blueprint Render.
- `Procfile`: commande de demarrage compatible PaaS.
- `migrations/`: dossier Alembic contenant les scripts de migration.
