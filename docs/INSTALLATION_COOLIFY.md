---
id: installation-coolify
title: Installation sur Coolify
sidebar_label: Installation Coolify
sidebar_position: 1
description: Guide pas à pas pour installer GestionFile Serveur sur une instance Coolify, sans connaissance de Docker.
keywords: [coolify, installation, déploiement, docker, vps, gestionfile]
---

# Installation de GestionFile Serveur sur Coolify

Ce guide vous accompagne **pas à pas** pour installer GestionFile Serveur sur un
serveur géré par [Coolify](https://coolify.io). Aucune connaissance de Docker ou
de ligne de commande n'est nécessaire : tout se fait dans l'interface Coolify.

Durée estimée : **15 à 20 minutes**.

:::tip Ce que vous obtenez à la fin
Un serveur GestionFile complet et sécurisé, accessible en HTTPS sur votre nom de
domaine, avec sa base de données, ses tâches planifiées et ses fichiers
persistants — prêt à connecter vos comptoirs, bornes et écrans.
:::

---

## 1. Ce que Coolify va installer

Le fichier `docker-compose.coolify.yaml` du dépôt décrit une **stack complète et
autonome**. Coolify lance 5 conteneurs sur votre serveur :

| Service | Rôle | Visible sur Internet ? |
|---|---|---|
| `init` | Prépare la base (migrations, données par défaut, premier administrateur) puis s'arrête | Non |
| `web` | L'application : pages web, API, temps réel (WebSocket) | **Oui**, via le proxy HTTPS de Coolify |
| `scheduler` | Tâches planifiées (nettoyage, tâches cron métier) | Non |
| `mysql` | Base de données MySQL 8 | Non (réseau privé interne) |
| `rabbitmq` | Relais de messages temps réel entre `web` et `scheduler` | Non (réseau privé interne) |

```text
        Internet                    Serveur Coolify
┌──────────────────────┐    ┌──────────────────────────────────┐
│  Comptoirs / Bornes  │    │                                  │
│  Écrans / Navigateurs│───▶│  Proxy HTTPS (Traefik)           │
└──────────────────────┘    │         │                        │
                            │         ▼                        │
                            │       [web]                      │
                            │      /   \                       │
                            │  [mysql] [rabbitmq]──[scheduler] │
                            │  réseau privé (invisible)        │
                            └──────────────────────────────────┘
```

:::info Persistance des données
Toutes les données (base, configuration, médias, uploads) vivent dans des
**volumes Docker**. Redéployer, redémarrer ou mettre à jour l'application ne
perd aucune donnée.
:::

---

## 2. Prérequis

Avant de commencer, vérifiez que vous disposez de :

- [ ] **Une instance Coolify fonctionnelle** avec un serveur connecté
      (par exemple un VPS chez votre hébergeur, déjà ajouté dans Coolify) ;
- [ ] **Le dépôt Git** de GestionFile Serveur — public, ou privé avec une
      Git App / clé de déploiement configurée dans Coolify ;
- [ ] **Un nom de domaine** dont le DNS pointe vers l'IP du serveur
      (enregistrement de type **A**). Exemple : `files.mapharmacie.fr` ;
- [ ] **Un mot de passe fort** pour le premier compte administrateur :
      minimum 10 caractères, pas un mot courant, différent de `admin`.

:::warning DNS d'abord
Le certificat HTTPS (Let's Encrypt) ne peut être délivré que si le domaine
pointe déjà vers le serveur. Configurez le DNS **avant** de déployer et
laissez-lui quelques minutes pour se propager.
:::

---

## 3. Étape 1 — Créer l'application dans Coolify

1. Connectez-vous à votre instance Coolify.
2. Ouvrez votre **Project** (ou créez-en un, ex. `GestionFile`), puis
   l'environnement **`production`**.
3. Cliquez sur **+ Add Resource**.
4. Choisissez le type de source :
   - **Public Repository** (cas courant) : collez l'URL HTTPS du dépôt
     (ex. `https://github.com/TherionAcribus/GestionFile.git`) puis
     validez — les autres types de dépôt (Git App, deploy key) ne servent que
     pour un dépôt privé ;
   - sélectionnez ensuite le **serveur de destination** et confirmez.
5. La page **General** de l'application s'ouvre. Le **Build Pack** est sur
   `Nixpacks` par défaut : cliquez sur **`Docker Compose`**.

   :::info
   Le formulaire change à la sélection : les champs Nixpacks (Install/Build/
   Start Command, Publish Directory) disparaissent et les champs propres à
   Docker Compose apparaissent.
   :::
6. Réglez ensuite :
   - **Base Directory** : `/`
   - **Docker Compose Location** : `docker-compose.coolify.yaml`
     (chemin **relatif**, sans `/` initial)
7. Ouvrez **Configuration > Git Source** et réglez **Branch** sur
   **`master`** (Coolify propose `main` par défaut — ce dépôt utilise
   `master` ; sinon le clonage échoue avec
   `fatal: Remote branch main not found`).
8. Cliquez **Save**.
9. Ouvrez l'onglet **Docker Compose Content** : vous devez y voir les 5
   services `init`, `web`, `scheduler`, `mysql`, `rabbitmq`.

:::note
Si le dépôt est un monorepo, adaptez **Base Directory** au dossier contenant le
fichier compose. Pour le dépôt serveur seul, `/` convient.
:::

---

## 4. Étape 2 — Configurer le domaine

1. Dans la configuration de l'application, repérez le service **`web`**.
2. Dans son champ **Domains**, saisissez votre domaine **avec le port
   interne** :

   ```text
   https://files.mapharmacie.fr:5000
   ```

   - `https://` : Coolify délivrera un certificat TLS automatiquement ;
   - `:5000` : port **interne** du conteneur, nécessaire pour que le proxy
     sache où router — vos utilisateurs n'auront jamais à le taper ;
   - remplacez `files.mapharmacie.fr` par votre vrai domaine.
3. Ne configurez **aucun domaine** sur les autres services (`mysql`,
   `rabbitmq`, `scheduler`, `init` restent invisibles de l'extérieur).

### Tester sans nom de domaine : l'URL temporaire Coolify (sslip.io)

Coolify peut générer un domaine gratuit qui pointe automatiquement vers
l'IP de votre serveur — pratique pour tester avant d'acheter ou de configurer
un vrai domaine :

1. Dans le champ **Domains** du service `web`, cliquez sur le bouton
   **Generate Domain** (icône à côté du champ) → Coolify produit une URL du
   type `http://xxxx.<ip-serveur>.sslip.io` ;
2. **Remplacez `http://` par `https://`** dans le champ (les ports 80 et 443
   du serveur doivent être ouverts : le 80 sert à valider le certificat),
   puis **Save** ;
3. Attendez ~30 s le temps que Let's Encrypt émette le certificat, puis ouvrez
   `https://xxxx.<ip>.sslip.io/healthz`.

:::warning HTTPS obligatoire
La connexion échoue en HTTP : le cookie de session est marqué `Secure`
(`COOKIE_SECURE=1` par défaut) et le navigateur le refuse hors HTTPS —
l'erreur typique est `CSRF validation failed` au login. Ne passez
`COOKIE_SECURE=0` que pour un test local, jamais en production exposée.
:::

:::tip Après le test
Quand vous passez au vrai domaine, remplacez simplement l'URL sslip.io par
`https://votre-domaine.fr:5000` dans le même champ. Pensez aussi à mettre la
variable `SITE` à jour si elle était renseignée.
:::

---

## 5. Étape 3 — Variables d'environnement

Ouvrez la section **Environment Variables** de la ressource.

### Variable obligatoire

| Variable | Pourquoi |
|---|---|
| `ADMIN_INITIAL_PASSWORD` | Mot de passe du **premier compte administrateur**. **Le déploiement est bloqué tant qu'elle est vide.** Doit respecter la politique : ≥ 10 caractères, non trivial, différent du nom d'utilisateur. |

### Variables optionnelles

| Variable | Défaut | Quand la modifier |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Choisir un autre identifiant pour le premier admin |
| `APP_SECRET` | auto-généré | Secret partagé avec les clients (comptoir, borne, imprimante). Laissez Coolify le générer, puis **notez sa valeur** : vous la saisirez dans chaque client |
| `COOKIE_SECURE` | `1` | Ne mettre `0` que pour un test local sans HTTPS |
| `PAGE_EDITOR_ENABLED_PAGES` | `announce` | Activer d'autres pages de l'éditeur visuel |
| `PORT` | `5000` | Ne pas modifier sauf besoin spécifique |

### Secrets générés automatiquement — ne rien faire

Coolify génère et conserve lui-même les secrets techniques, grâce aux
variables `SERVICE_*` déclarées dans le compose :

`SECRET_KEY`, `SECURITY_PASSWORD_SALT`, `BASE32_KEY` (clé de chiffrement),
`MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `RABBITMQ_PASSWORD`.

:::tip Retrouver un secret généré
Dans **Environment Variables**, la valeur des variables générées (dont
`APP_SECRET`, nécessaire côté clients) est affichée en clair aux personnes
autorisées. Notez `APP_SECRET` dans votre coffre de mots de passe.
:::

:::danger Jamais dans le code
Ne collez aucun secret dans le dépôt Git. Toutes les valeurs sensibles vivent
dans les variables Coolify.
:::

---

## 6. Étape 4 — Déployer

1. Cliquez sur **Deploy**.
2. Suivez le déploiement dans l'onglet **Deployments**. L'ordre normal est :

   ```text
   mysql + rabbitmq  deviennent "healthy"
        │
        ▼
   init  exécute les migrations + crée les données + le premier admin
        │  (se termine avec exit 0 : c'est NORMAL qu'il s'arrête)
        ▼
   web + scheduler   démarrent
   ```

3. Attendez le statut final. Un premier déploiement peut prendre plusieurs
   minutes (construction de l'image Docker).

:::info Le conteneur `init` s'arrête tout seul
`init` est un conteneur de déploiement : il prépare la base puis quitte. Son
statut « exited (0) » signifie **succès**, pas un crash.
:::

### Vérifications après déploiement

| Vérification | Où | Résultat attendu |
|---|---|---|
| Processus vivant | `https://votre-domaine.fr/healthz` | `{"status":"alive"}` |
| Prêt à servir | `https://votre-domaine.fr/readyz` | HTTP `200` (base joignable ; RabbitMQ aussi si activé) |
| Page de login | `https://votre-domaine.fr/admin_security/login` | Formulaire affiché en HTTPS (cadenas) |
| Bootstrap réussi | Logs du service `init` | Migrations + `Admin user created` |
| Web démarré | Logs du service `web` | `Starting with APP_ROLE=web` |

---

## 7. Étape 5 — Première connexion et configuration

1. Ouvrez `https://votre-domaine.fr/admin_security/login`.
2. Connectez-vous avec `ADMIN_USERNAME` (défaut `admin`) et le mot de passe
   `ADMIN_INITIAL_PASSWORD` saisi à l'étape 3.
3. **Changez immédiatement le mot de passe** dans l'administration
   (onglet Sécurité).
4. Configurez : nom de la pharmacie, comptoirs, activités, horaires.

---

## 8. Étape 6 — Connecter les clients

Pour chaque client (application comptoir, borne, écran, imprimante) :

1. Dans Coolify → **Environment Variables**, copiez la valeur de
   **`APP_SECRET`** (auto-générée).
2. Dans le client, renseignez :
   - **URL du serveur** : `https://votre-domaine.fr`
   - **Secret applicatif** : la valeur d'`APP_SECRET`
3. Vérifiez la connexion (le client obtient un jeton via `/api/get_app_token`).

### Temps réel (Socket.IO / RabbitMQ)

Le relais temps réel entre `web` et `scheduler` passe par RabbitMQ. Pour
l'activer : dans l'administration, activez l'option **« Démarrer le serveur
avec RabbitMQ »**, puis redémarrez l'application dans Coolify
(**Restart**).

### Test métier complet

Réalisez le parcours de bout en bout :

1. Un patient prend un ticket à la borne (ou via l'interface de test) ;
2. Le ticket apparaît dans la file (comptoir + écran d'appel) ;
3. Un comptoir appelle le patient ;
4. L'écran affiche l'appel (+ annonce vocale et impression si configurées).

:::tip
`/healthz` et `/readyz` prouvent que le serveur tourne — seul ce parcours
complet valide que l'installation fonctionne réellement.
:::

---

## 9. Maintenance courante

### Mettre à jour l'application

1. Pousser les modifications sur la branche suivie (`master`) ;
2. Dans Coolify : **Redeploy** (ou laisser le webhook Git le faire
   automatiquement) ;
3. Le conteneur `init` rejoue les migrations automatiquement à chaque
   déploiement — rien à faire.

### Modifier une variable d'environnement

1. **Environment Variables** → modifier → **Save** ;
2. **Restart** suffit pour une variable de fonctionnement ; **Redeploy** si la
   variable affecte la construction.

### Consulter les journaux

Onglet **Logs** de chaque service. Pour un problème de démarrage, regardez
dans l'ordre : `init` (migrations/bootstrap), puis `web`, puis `scheduler`.

### Sauvegardes

Deux niveaux complémentaires :

- **Export applicatif** : fonction d'export intégrée dans l'administration
  (configuration, données métier) ;
- **Volumes Docker** : `mysql_data` (la base), `instance_data` (secrets
  persistés, exports), `galleries_data`, `buttons_data`, `flags_data`,
  `annonces_data`, `signals_data`, `tts_data` (médias). Sauvegardez-les
  régulièrement (snapshot VPS, `mysqldump`, ou outil de backup Coolify).

:::danger Avant chaque mise à jour
Faites une sauvegarde de la base (`mysqldump`) et des volumes. Un retour à une
version précédente du code ne ramène **pas** la base à son schéma précédent.
:::

---

## 10. Dépannage

### Erreur `build-time.env: Invalid template` après une mise à jour

Coolify peut conserver les variables découvertes dans une ancienne version du
Compose. Une valeur incomplète comme `${SERVICE_PASSWORD_GFROOT:-change-me`
suffit à faire échouer la lecture du fichier d'environnement, même si le nouveau
Compose ne référence plus cette variable.

Pour la stack tout-en-un corrigée (commit `d539d73`), procéder ainsi :

1. Vérifier que le Compose chargé utilise directement les variables `SERVICE_*`,
   par exemple `MYSQL_ROOT_PASSWORD: ${SERVICE_PASSWORD_GFROOT}`.
2. Dans **Production Environment Variables**, supprimer uniquement les anciennes
   entrées suivantes, devenues inutilisées comme entrées du Compose :
   `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `RABBITMQ_USER`,
   `RABBITMQ_PASSWORD`, `SECRET_KEY`, `SECURITY_PASSWORD_SALT`, `APP_SECRET`
   et `BASE32_KEY`. Les variables portant ces noms dans les conteneurs restent
   fournies par le Compose à partir des valeurs `SERVICE_*`.
3. Pour le broker RabbitMQ inclus, vider la valeur de `RABBITMQ_URL`, puis cliquer
   sur **Update**. Garder `RABBITMQ_HOST=rabbitmq`. Une ancienne URL non vide
   prendrait la priorité sur les nouveaux identifiants générés. Ne pas appliquer
   cette étape à une URL de broker externe volontairement configurée.
4. Conserver toutes les variables `SERVICE_*`, `ADMIN_INITIAL_PASSWORD` et les
   autres paramètres utilisés. Ne pas remplacer les expressions cassées par
   `change-me` et ne pas les masquer avec **Is Literal**.
5. Appliquer le même nettoyage aux **Preview Deployments Environment Variables**
   avant toute utilisation des previews, sans copier les secrets de production.
6. Lancer un nouveau déploiement et ouvrir sa nouvelle entrée dans **Deployments**.
   Vérifier dans la ligne `Importing` le commit réellement déployé : `d539d73`
   ou une version ultérieure contenant le correctif, et non `639d2e5`.

Dans cette version, le secret client `APP_SECRET` est fourni par la variable
Coolify **`SERVICE_BASE64_64_GFAPPSECRET`** : c'est cette valeur qu'il faut
récupérer pour configurer les clients, et non l'ancienne entrée `APP_SECRET`.

:::warning Secrets et données existantes
Ne partager que les noms des variables et les messages d'erreur, jamais les
valeurs des secrets. Un secret partagé en clair doit être remplacé. Si MySQL
est déjà initialisé, modifier sa variable d'environnement ne change pas le mot
de passe dans la base : prévoir une rotation coordonnée plutôt que supprimer
le volume. Le nettoyage des anciennes entrées Coolify ne nécessite aucune
suppression de volume.
:::

| Symptôme | Cause probable | Solution |
|---|---|---|
| `fatal: Remote branch main not found` au clonage | Coolify clone `main` par défaut ; ce dépôt utilise `master` | **Configuration > Git Source** → Branch = `master` |
| Le bouton Deploy est bloqué / variable requise | `ADMIN_INITIAL_PASSWORD` vide | Renseignez-la dans Environment Variables |
| `init` échoue : `ADMIN_INITIAL_PASSWORD invalide` | Mot de passe trop faible ou égal à l'identifiant | Choisissez un mot de passe conforme (≥ 10 car.) puis **Redeploy** |
| `init` échoue sur les migrations | MySQL pas prêt, ou `MYSQL_PASSWORD` modifié **après** le premier déploiement | Ne jamais changer `MYSQL_PASSWORD` sur une base existante ; lire les logs `init` |
| `web` en échec / unhealthy | Dépendance injoignable ou `APP_SECRET` absent | Consulter `https://domaine/readyz` puis les logs `web` |
| Login impossible, cookies refusés | Accès en HTTP alors que `COOKIE_SECURE=1` | Utilisez HTTPS ; `COOKIE_SECURE=0` seulement pour un test local |
| `CSRF validation failed` au login | Domaine sslip.io généré en `http://` : le cookie `Secure` n'est pas stocké | Préfixer le domaine par `https://` dans **Domains** (ports 80/443 ouverts), attendre le certificat, recharger |
| « Database schema is not initialized » | `web` a démarré sans `init` terminé | Vérifier les logs `init`, Redeploy |
| Clients refusés (401) | `APP_SECRET` différent côté client | Copier la valeur exacte depuis Environment Variables |
| Écrans/comptoirs ne se rafraîchissent plus | Relais RabbitMQ inactif | Activer l'option RabbitMQ dans l'admin + Restart |
| `E: Failed to fetch … deb11u16 … 404 Not Found` pendant le build | Image de base `python:3.10.4` (Debian bullseye) obsolète : paquets retirés des miroirs | Corrigé dans le Dockerfile (`python:3.12-slim-bookworm`) ; utiliser un commit à jour puis Redeploy |
| Certificat TLS absent | DNS non propagé ou domaine incorrect | Vérifier l'enregistrement A, le champ Domains (`https://...:5000`), attendre la propagation |

:::warning En cas de blocage
Relevez : le service concerné, le message exact dans **Logs**, et l'étape du
guide où vous êtes. Ne supprimez jamais de volume Docker pour « repartir à
zéro » sans avoir sauvegardé.
:::

---

## 11. Variante avancée — services externes

Le compose est **tout-en-un** par défaut. Pour utiliser un MySQL ou un
RabbitMQ déjà provisionné dans Coolify :

1. Renseignez `DATABASE_URL` (format `mysql+pymysql://user:pass@host:3306/base`)
   et/ou `RABBITMQ_URL` dans les variables ;
2. Supprimez du compose le service correspondant **et** ses lignes
   `depends_on` dans `init`, `web` et `scheduler` ;
3. La base cible doit **exister** (vide) : l'application crée les tables,
   pas la base.

---

## 12. Récapitulatif — ce dont vous avez besoin

```text
✔ Un serveur dans Coolify
✔ Le dépôt Git du serveur
✔ Un domaine → IP du serveur (DNS type A)
✔ Un mot de passe fort pour le premier admin
✔ 15-20 minutes
```

Et c'est tout : la base, le broker, les secrets et les volumes sont gérés par
la stack.

## Pour aller plus loin

- Référence technique complète : `docs/DEPLOYMENT.md` (dans le dépôt) ;
- Sécurité (CSRF, jetons applicatifs, sessions) : `docs/SECURITY.md` ;
- Protocole temps réel clients/serveur : `docs/PROTOCOLE.md`.
