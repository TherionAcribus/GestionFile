# Sécurité — Serveur GestionFile

## Protection CSRF

Le serveur active une protection **CSRF** (Cross-Site Request Forgery) via
Flask-WTF (`CSRFProtect`). L'objectif : empêcher qu'un site tiers ne déclenche,
depuis le navigateur d'un administrateur connecté, une requête mutatrice
(modification de paramètres, gestion des utilisateurs, etc.) à son insu.

### Principe

- Toute requête **navigateur** mutatrice (`POST`, `PUT`, `PATCH`, `DELETE`)
  doit présenter un **jeton CSRF** valide, sinon elle est refusée avec un
  **HTTP 400**.
- Le contrôle est piloté explicitement dans `app.py`
  (`csrf_protect_browser_requests` + `_csrf_is_exempt`) plutôt que par la
  vérification automatique globale de Flask-WTF
  (`WTF_CSRF_CHECK_DEFAULT = False`). Cela permet d'exempter proprement les
  clients qui ne sont pas des navigateurs.
- Le jeton reste valide le temps de la session (`WTF_CSRF_TIME_LIMIT = None`),
  ce qui évite les faux rejets sur de longues sessions d'administration.

### Diffusion du jeton au navigateur

Les gabarits des pages navigateur exposent le jeton dans une balise meta :

```html
<meta name="csrf-token" content="{{ csrf_token() }}">
```

Le script partagé [`static/js/csrf.js`](../static/js/csrf.js) le lit et
l'injecte automatiquement dans **toutes** les requêtes mutatrices de même
origine, quel que soit le mécanisme :

- **HTMX** — via l'événement `htmx:configRequest` (en-tête `X-CSRFToken`) ;
- **fetch** — via un wrapper de `window.fetch` ;
- **jQuery** — via `$.ajaxSetup`.

Les requêtes vers une autre origine (ex. `api.spotify.com`) ne reçoivent
jamais le jeton.

Gabarits concernés (socle navigateur) :

- `templates/admin/base.html` — toutes les pages d'administration ;
- `templates/counter/counter.html`, `templates/counter/countert.html` — pages
  comptoir affichées dans un navigateur.

Le formulaire de connexion (`templates/security/login.html`) inclut déjà le
jeton via `{{ form.hidden_tag() }}` (Flask-WTF).

### Routes exemptées (et pourquoi)

Ces requêtes ne proviennent pas d'un navigateur porteur de session ; les
soumettre au CSRF les casserait sans bénéfice de sécurité. L'exemption est
définie par `_csrf_is_exempt()` dans `app.py`.

| Exemption | Motif |
|-----------|-------|
| Préfixe `/socket.io` | Transport Socket.IO (POLLING POST) géré hors formulaire. |
| Préfixe `/api/` | API machine authentifiées par **jeton applicatif** (`/api/get_app_token`, `/api/counter/*`, `/api/printer/status`, …). |
| Préfixe `/app/` | Routes d'App_Comptoir (`/app/counter/*`), authentifiées par jeton. |
| Préfixe `/patient` | Borne/kiosque patient (`/patients_submit`, `/patient/print_*`, `/patient/phone/*`). Endpoints publics sans session privilégiée. |
| En-tête `X-App-Token` présent | Toute requête d'une application cliente (App_Comptoir, borne, imprimante). Un formulaire cross-site **ne peut pas** positionner d'en-tête personnalisé (protection intrinsèque), et le jeton est de toute façon revérifié par la route (`@require_app_token_or_login`). Couvre notamment les routes à double usage appelées à la fois par le navigateur comptoir et par App_Comptoir (`/validate_and_call_next`, `/validate_patient`, `/pause_patient`, `/counter/*`). |

### Limite connue (à traiter séparément)

Certaines actions destructrices sont encore exposées en **GET**
(ex. `/admin/security/delete_user/<id>`, `/admin/activity/delete/<id>`, …).
Le CSRF ne protège pas les requêtes GET. La correction (passage de ces routes
en POST, avec jeton) fait l'objet d'un point ultérieur ; elle n'est pas incluse
dans l'activation du CSRF pour ne pas modifier le comportement des liens et
modales de confirmation existants.

## Intégration Spotify (musique d'ambiance)

L'intégration Spotify pilote un **unique** compte Spotify (le lecteur physique
du point de vente). Sa sécurisation repose sur quatre principes.

### 1. Aucun secret client codé en dur

L'identifiant (`client_id`) et le secret (`client_secret`) de l'application
Spotify sont résolus par `spotify_support.resolve_spotify_credentials`, dans cet
ordre :

1. variables d'environnement `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
   (gestionnaire de secrets — **recommandé**) ;
2. configuration de l'officine en base (`music_spotify_user` /
   `music_spotify_key`, cette dernière marquée secrète : jamais rendue dans un
   gabarit, exclue des sauvegardes).

Si aucune source ne fournit les deux valeurs, Spotify est traité comme « non
configuré » et aucune connexion n'est tentée. **Aucune valeur de repli n'est
codée en dur dans le code.**

> ⚠️ **Rotation obligatoire** — Une version antérieure contenait un
> `client_secret` Spotify en clair dans le code source. Ce secret est
> considéré comme **compromis** : il doit être **révoqué puis régénéré** dans le
> [dashboard développeur Spotify](https://developer.spotify.com/dashboard) et la
> nouvelle valeur fournie via `SPOTIFY_CLIENT_SECRET` (ou l'écran d'options).

### 2. Jetons OAuth conservés côté serveur

Les jetons d'accès et de rafraîchissement OAuth ne sont **plus** stockés dans la
session Flask (cookie signé côté client). Ils vivent en base, dans la table
`spotify_token` (unique ligne `id = 1`), via `SpotifyDBCacheHandler`. Ce jeton
est un secret : il n'est jamais journalisé ni exporté dans les sauvegardes.

### 3. Toutes les routes Spotify exigent une permission admin

Les routes `/spotify/*` (connexion, déconnexion, callback, lecture,
pause/reprise, volume, playlists, pistes enregistrées) sont protégées par
`@require_permission('music_play')` ou `@require_permission('music_options')`.
Elles ne sont plus accessibles sans session administrateur autorisée.

Le **ducking** (baisser/couper la musique pendant les annonces vocales) était
auparavant déclenché par l'écran d'annonce **public** via des routes Spotify
ouvertes. Il est désormais piloté **côté serveur** (`duck_for_announcement`,
lancé en tâche de fond à l'émission de l'audio) : l'écran d'annonce n'appelle
plus aucune route Spotify, ce qui permet de toutes les protéger.

### 4. Pas de journalisation de secret

Le secret client et les jetons ne sont jamais imprimés ni journalisés (l'ancien
`print` de `MUSIC_SPOTIFY_KEY` a été supprimé).

## Connexion durcie (point 3.4)

La route de connexion (`routes/admin_security.py :: login`) applique cinq
mesures complémentaires. Toute la décision est déportée dans trois modules
**purs** (testables sans Flask ni MySQL) : `login_guard.py`,
`password_policy.py`, `login_audit.py`.

### 1. Message générique — anti-énumération

Un identifiant inconnu et un mot de passe erroné renvoient **exactement** le
même message (`« Identifiants incorrects »`) et le même traitement. On ne
révèle plus lequel des deux est faux, ce qui empêche de découvrir quels comptes
existent. Pour un utilisateur inconnu, une **vérification de hash factice** est
tout de même exécutée afin d'égaliser le temps de réponse (défense anti-timing).

### 2. Limitation des tentatives — par IP **et** par identité

`login_guard.LoginThrottle` compte les échecs récents séparément par adresse IP
et par nom d'utilisateur. La route interroge les deux clés et applique le délai
**le plus contraignant**. Ainsi une IP qui balaye plusieurs comptes est freinée,
et un compte visé depuis plusieurs adresses aussi.

### 3. Délai progressif / verrouillage temporaire

Chaque échec au-delà du premier repousse la prochaine tentative autorisée d'un
délai qui **double** (2 s, 4 s, 8 s…) plafonné à 300 s : c'est à la fois le délai
progressif et, après quelques échecs, un verrouillage temporaire de fait. Une
connexion réussie **réinitialise** les compteurs ; les échecs plus vieux que la
fenêtre glissante (15 min) sont oubliés (récupération automatique).

> **Périmètre.** L'état de limitation est **en mémoire, par process**. Derrière
> plusieurs workers, chaque worker a son propre compteur. Suffisant pour une
> administration interne ; un stockage partagé (table/redis) reste possible sans
> changer l'interface du module.

### 4. Politique minimale de mot de passe

`password_policy.validate_password` est appliquée à la **création** d'un
utilisateur et au **changement** de mot de passe (pas à la connexion, qui
vérifie seulement le hash existant) : longueur ≥ 10 caractères, refus des mots
de passe notoirement faibles / par défaut (`admin`, `password`, `gestionfile`…),
et interdiction d'un mot de passe identique au nom d'utilisateur.

### 5. Journal d'audit — sans secret

`login_audit.build_login_audit` produit une ligne normalisée pour **chaque**
tentative — `success`, `failure`, `blocked` — avec l'issue, l'identifiant
revendiqué, l'IP, une empreinte tronquée du User-Agent et le délai imposé. La
fonction n'accepte **aucun** champ de mot de passe ; les retours à la ligne sont
retirés (anti-injection de log) et les valeurs bornées. Les refus/blocages sont
journalisés en `WARNING`, les succès en `INFO`.

L'IP retenue est `request.remote_addr`. `X-Forwarded-For` n'est **pas** cru par
défaut (un client pourrait le forger pour contourner la limitation) : un reverse
proxy de confiance doit réécrire `remote_addr` (ProxyFix) en amont.
