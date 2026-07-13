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
