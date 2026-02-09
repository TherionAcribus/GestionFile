# Deploiement Rapide

Ce guide propose 2 chemins de deploiement:

- Option A (recommandee): VPS + Coolify
- Option B: PaaS (Render)

## Prerequis communs

1. Copier `.env.example` vers `.env`.
2. Completer les variables sensibles:
   `MYSQL_*`, `BASE32_KEY`, `APP_SECRET`, `SECRET_KEY`, `SECURITY_PASSWORD_SALT`.
3. Verifier que le port applicatif est `PORT=5000` (ou adapte a ton infra).

## Option A - VPS + Coolify (recommande)

Objectif: installation la plus simple pour un client final, avec stack complete.

1. Pousser le repo Git.
2. Dans Coolify, creer une application de type `Docker Compose`.
3. Utiliser le fichier `docker-compose.coolify.yaml`.
4. Definir les variables d'environnement (UI Coolify ou `.env`):
   `MYSQL_ROOT_PASSWORD`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
   `SECRET_KEY`, `SECURITY_PASSWORD_SALT`, `APP_SECRET`, `BASE32_KEY`.
5. Deployer.
6. Verifier les probes:
   - Liveness: `GET /healthz` doit renvoyer `200`.
   - Readiness: `GET /readyz` doit renvoyer `200` quand DB (+ RabbitMQ si active) est prete.

Notes:
- `mysql` et `rabbitmq` sont inclus dans le compose Coolify pour un setup "tout-en-un".
- RabbitMQ peut rester provisionne meme si desactive dans la config applicative.

## Option B - Render (PaaS)

Objectif: garder une option PaaS comme aujourd'hui.

1. Dans Render, creer un deploy `Blueprint`.
2. Selectionner le fichier `render.yaml`.
3. Renseigner les variables marquees `sync: false`.
4. Deployer.
5. Verifier `GET /healthz`.

Notes:
- Le fichier `Procfile` est fourni pour les PaaS qui le lisent.
- Si RabbitMQ est desactive dans l'app, `RABBITMQ_URL` peut etre laisse vide.

## Fichiers fournis

- `.env.example`: exemple de configuration.
- `docker-compose.coolify.yaml`: stack VPS/Coolify prete a deployer.
- `render.yaml`: blueprint Render.
- `Procfile`: commande de demarrage compatible PaaS.
