# Déploiement — GestionFile Serveur

Deux parcours de déploiement :

- **Option A (recommandée) : VPS + Coolify** — stack tout-en-un, rien à
  provisionner à la main. Ce guide est écrit pour elle.
- **Option B : PaaS (Render)** — conservée comme alternative (`render.yaml`).

Le document contient deux niveaux de lecture :

- **Partie I — Guide utilisateur** : installation pas à pas, sans connaissance
  technique de Docker.
- **Partie II — Checklist agent IA** : le même parcours formalisé pour qu'un
  agent de codage puisse préparer ou vérifier l'installation de façon fiable.

---

# Partie I — Guide utilisateur

## 1. Ce qui va être installé

Une application Coolify unique qui lance 5 conteneurs sur votre serveur :

| Conteneur | Rôle | Exposé publiquement ? |
|---|---|---|
| `init` | Migrations + données initiales + premier admin, puis s'arrête | Non |
| `web` | Application Flask (HTTP + WebSocket), rôle `APP_ROLE=web` | Oui, via le proxy HTTPS de Coolify |
| `scheduler` | Tâches planifiées (nettoyages, cron), rôle `APP_ROLE=scheduler` | Non |
| `mysql` | Base de données MySQL 8 | Non (réseau privé) |
| `rabbitmq` | Relais temps réel entre web et scheduler | Non (réseau privé) |

Toutes les données persistent dans des volumes Docker (`mysql_data`,
`instance_data`, médias…) : elles survivent aux redéploiements.

## 2. Prérequis

- [ ] Une instance **Coolify** fonctionnelle avec un **serveur** connecté
      (VPS avec Docker géré par Coolify).
- [ ] Un **dépôt Git** accessible contenant ce code (public ou privé avec
      clé de déploiement configurée dans Coolify).
- [ ] Un **nom de domaine** dont l'enregistrement DNS (type A) pointe vers
      l'IP du serveur. Exemple : `files.mapharmacie.fr`.
- [ ] Un **mot de passe fort** prêt pour le premier administrateur
      (minimum 10 caractères, pas un mot courant, différent de
      `admin`).

## 3. Installation pas à pas

### Étape 1 — Créer la ressource

1. Dans Coolify, ouvrez votre **Project** puis l'**Environment** (`production`).
2. Cliquez **+ New** → **Application**.
3. Choisissez la source Git :
   - dépôt **public** : collez l'URL HTTPS du dépôt, **Check Repository** ;
   - dépôt **privé** : sélectionnez votre Git App / deploy key existante.
4. Dans **Configuration > General** :
   - **Build Pack** : `Docker Compose` ;
   - **Base Directory** : `/` (ou le sous-dossier contenant le compose si le
     dépôt est un monorepo) ;
   - **Docker Compose Location** : `docker-compose.coolify.yaml`.
5. **Save**, puis vérifiez **Docker Compose Content** : les 5 services
   (`init`, `web`, `scheduler`, `mysql`, `rabbitmq`) doivent y figurer.

### Étape 2 — Domaine

1. Toujours dans la configuration de l'application, repérez le service
   **`web`** et renseignez son champ **Domains** :
   `https://files.mapharmacie.fr:5000`
   - Le suffixe `:5000` indique au proxy le port **interne** du conteneur ;
     vos visiteurs utiliseront simplement `https://files.mapharmacie.fr`.
   - Coolify délivre et renouvelle le certificat TLS automatiquement
     (Let's Encrypt) dès que le domaine répond.
2. Les autres services n'ont **aucun domaine** : ils restent en réseau privé.

### Étape 3 — Variables d'environnement

Ouvrez **Environment Variables** de la ressource. Une seule variable est
obligatoire à saisir :

| Variable | Obligatoire ? | Contenu |
|---|---|---|
| `ADMIN_INITIAL_PASSWORD` | **Oui** — le déploiement est bloqué sans elle | Mot de passe du premier compte admin (>= 10 car., politique interne) |
| `ADMIN_USERNAME` | Non | Identifiant du premier admin (défaut : `admin`) |
| `APP_SECRET` | Non (auto-généré) | Secret partagé avec les clients (App comptoir, borne, imprimante). Auto-généré par Coolify ; **notez-le**, vous le saisirez dans chaque client |
| `SECRET_KEY`, `SECURITY_PASSWORD_SALT` | Non (auto-générés) | Secrets Flask — ne pas saisir, laisser Coolify générer |
| `BASE32_KEY` | Non (auto-générée) | Clé de chiffrement Fernet des clés de service — auto-générée |
| `MYSQL_*`, `RABBITMQ_*` | Non (auto-générés) | Mots de passe internes des services inclus |
| `COOKIE_SECURE` | Non | `1` par défaut (HTTPS). Ne mettre `0` que pour un test HTTP sans TLS |
| `DATABASE_URL`, `DATABASE_URL_SCHEDULER` | Non | Uniquement pour une base MySQL externe (voir §7) |

> Pour lire un secret auto-généré (ex. `APP_SECRET`), ouvrez la variable
> dans **Environment Variables** : sa valeur y est affichée.

### Étape 4 — Déployer

1. Cliquez **Deploy**.
2. Suivez **Deployments** jusqu'au statut terminé. Ordre attendu :
   `mysql` et `rabbitmq` deviennent *healthy* → `init` exécute les migrations
   et le bootstrap puis s'arrête (*exit 0*) → `web` et `scheduler` démarrent.
3. Vérifiez dans **Logs** du service `web` la ligne `Starting with APP_ROLE=web`.

### Étape 5 — Vérifier

- `https://files.mapharmacie.fr/healthz` → `{"status": "alive"}` (processus vivant).
- `https://files.mapharmacie.fr/readyz` → `200` (base joignable ; RabbitMQ
  aussi si `START_RABBITMQ` est activé).
- La page d'accueil et `/admin_security/login` s'affichent en HTTPS.

### Étape 6 — Premier administrateur

1. Connectez-vous sur `/admin_security/login` avec `ADMIN_USERNAME` /
   `ADMIN_INITIAL_PASSWORD`.
2. **Changez immédiatement le mot de passe** depuis l'administration
   (onglet Sécurité).
3. Configurez la pharmacie, les comptoirs, les activités.

### Étape 7 — Connecter les clients

Dans chaque client (App comptoir, borne, imprimante), renseignez :

- l'URL du serveur : `https://files.mapharmacie.fr` ;
- le secret applicatif : la valeur d'`APP_SECRET` dans les variables Coolify.

Puis faites un test métier complet : ticket → file d'attente → appel →
affichage écran (+ impression si une borne est connectée).

## 4. Maintenance courante

| Opération | Comment |
|---|---|
| **Mettre à jour** | Pousser sur la branche suivie puis **Redeploy** dans Coolify (ou webhook Git). `init` rejoue les migrations automatiquement. |
| **Changer une variable** | Modifier dans **Environment Variables**, **Save**, puis **Restart** (runtime) ou **Redeploy** (build). |
| **Restaurer** | Garder une sauvegarde des volumes `mysql_data` + `instance_data` + médias. Restaurer = recréer ces volumes avant le déploiement. **Attention :** un rollback de code ne ramène pas la base — restaurer aussi le dump MySQL. |
| **Logs** | Onglet **Logs** par service. Le bootstrap apparaît dans `init`. |
| **Sauvegarde applicative** | La fonction d'export intégrée (admin) complète, mais ne remplace pas la sauvegarde des volumes. |

## 5. Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| Déploiement refusé avant de démarrer | `ADMIN_INITIAL_PASSWORD` vide (variable `:?`) | La renseigner dans Environment Variables |
| `init` échoue, message `ADMIN_INITIAL_PASSWORD invalide` | Mot de passe trop faible / égal au nom d'utilisateur | Choisir un mot de passe conforme puis Redeploy |
| `init` échoue sur `manage.py migrate` | MySQL pas prêt ou identifiants incohérents | Lire les logs `init` ; vérifier que `MYSQL_PASSWORD` n'a pas changé **après** le 1er déploiement (le mot de passe est fixé à la création du volume `mysql_data`) |
| `web` unhealthy | Voir logs `web` ; souvent DB ou `APP_SECRET` absent | `/readyz` indique la dépendance en échec |
| Login admin refusé | Mauvais mot de passe, ou cookie `Secure` sur HTTP | S'assurer d'être en HTTPS ; sinon `COOKIE_SECURE=0` (test seulement) |
| Clients (App/borne) refusés | `APP_SECRET` différent côté client | Copier la valeur exacte depuis Environment Variables |
| Écrans/comptoirs ne se rafraîchissent pas en temps réel | Relais RabbitMQ inactif | Activer « Démarrer le serveur avec RabbitMQ » dans l'admin, redémarrer |
| « Database schema is not initialized » | `web` démarré avant la fin d'`init` | Ne devrait pas arriver (depends_on) ; vérifier les logs `init` |

## 6. Installation avec base MySQL externe (avancé)

Le compose est autonome par défaut. Pour utiliser un MySQL ou un RabbitMQ
déjà provisionné dans Coolify :

1. Renseigner `DATABASE_URL` (et `DATABASE_URL_SCHEDULER` si besoin) au
   format `mysql+pymysql://user:pass@host:3306/base`, et/ou `RABBITMQ_URL`.
2. Retirer du compose le service correspondant **et** ses entrées
   `depends_on` dans `init`, `web` et `scheduler`.
3. La base cible doit exister (vide) : l'application crée les tables, pas la
   base.

## 7. Option B — Render (PaaS)

1. Créer un deploy **Blueprint** pointant sur `render.yaml`.
2. Renseigner les variables marquées `sync: false`.
3. Provisionner un MySQL externe (Render n'en fournit pas).
4. Les migrations s'exécutent via le `Procfile` ; vérifier `GET /healthz`.

---

# Partie II — Checklist agent IA

Cette section permet à un agent de codage de préparer, modifier ou vérifier
le déploiement sans exploration préalable.

## Fichiers qui font autorité

| Fichier | Rôle |
|---|---|
| `docker-compose.coolify.yaml` | Stack Coolify tout-en-un (seul fichier déployé) |
| `Dockerfile` + `docker-entrypoint.sh` | Image ; l'entrypoint resseed les médias par défaut dans les volumes vides (idempotent, jamais d'écrasement) |
| `manage.py` (`migrate`) | Bootstrap base vide (`create_all` + `stamp head`) ou `alembic upgrade` ; verrou MySQL `GET_LOCK` contre les migrations concurrentes |
| `app.py` (`APP_ROLE`) | Rôles `init` / `web` / `scheduler` ; les hooks de bootstrap ne tournent que dans `scheduler`/`init` |
| `routes/admin_security.py` (`create_default_user`) | Premier admin via `ADMIN_USERNAME`/`ADMIN_INITIAL_PASSWORD` ; mot de passe aléatoire + log si absent ; `RuntimeError` si le mot de passe fourni est invalide |
| `.env.example` | Catalogue des variables et valeurs par défaut |
| `docs/SECURITY.md`, `docs/PROTOCOLE.md` | Invariants CSRF / tokens / namespaces Socket.IO |

## Séquence d'installation (état attendu)

```
mysql healthy ─┐
               ├─> init (migrate + bootstrap + admin) exit 0 ─┬─> web (APP_ROLE=web, sert HTTP)
rabbitmq healthy┘                                             └─> scheduler (APP_ROLE=scheduler)
```

Points de contrôle, dans l'ordre :

1. `docker compose -f docker-compose.coolify.yaml config` valide (interpolation
   résolue, `ADMIN_INITIAL_PASSWORD` exigé par `:?`).
2. `init` termine en `exit 0` ; ses logs montrent migrations + création admin.
3. `web` healthcheck OK → `GET /healthz` = 200, `GET /readyz` = 200.
4. `scheduler` démarre, log `Scheduler started in active mode`.
5. Premier login admin réussi, puis changement de mot de passe.
6. Un client authentifié via `APP_SECRET` obtient un jeton (`/api/get_app_token`).

## Conditions d'arrêt (ne PAS contourner)

- Base existante avec tables mais sans `alembic_version` → `manage.py migrate`
  refuse à juste titre : **arrêter**, ne pas poser `FORCE_BOOTSTRAP_DB=1` sans
  accord explicite de l'opérateur.
- `init` en échec → ne jamais forcer le démarrage de `web` pour « tester ».
- Échec de sauvegarde avant mise à jour → arrêter.
- Suppression ou réinitialisation d'un volume → **action destructive** :
  confirmation explicite requise, toujours.
- Secret obligatoire absent → arrêter, ne pas inventer de valeur.
- Ne pas affaiblir CSRF/CORS/`COOKIE_SECURE` pour réparer un problème de
  proxy ou de domaine : corriger la configuration Coolify à la place.

## Modifications fréquentes et leurs tests

| Changement | Vérification minimale |
|---|---|
| Compose / variables | `docker compose config` + relecture des fallbacks `SERVICE_*` |
| Bootstrap admin | `.venv/Scripts/python.exe -m pytest tests/test_admin_bootstrap.py -q` |
| Migrations / manage.py | `.venv/Scripts/python.exe -m pytest -q` (suite de référence) |
| Probes | `tests` couvrant `routes/api_system.py` |

Consignes complètes de vérification locale : `AGENTS.md` (pytest avec le
`.venv` Windows, exclusions MySQL/E2E par défaut).

## Invariants à préserver

- `web` seul sert le trafic ; `scheduler` toujours à **1 replica** ; `init`
  sans `restart`.
- Jamais de `ports:` publié sur `web` en production (le proxy Coolify suffit).
- Les secrets restent en variables Coolify : rien de sensible dans le compose
  ni dans l'image (cf. `.dockerignore`, qui exclut `.env`, `instance`, `*.db`).
- Le premier mot de passe admin n'est jamais fixe ni commité.

## Fichiers fournis

- `docker-compose.coolify.yaml` : stack Coolify tout-en-un.
- `docker-compose.yaml` : stack de développement local (ne pas utiliser en
  production).
- `render.yaml`, `Procfile` : chemin PaaS.
- `migrations/` : scripts Alembic.
- `tests/test_admin_bootstrap.py` : référence du contrat de bootstrap admin.
