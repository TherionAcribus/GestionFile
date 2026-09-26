# Protocole temps réel (Socket.IO)

Contrat des échanges serveur → clients. La **base de données reste la source de
vérité** : Socket.IO est un canal de *notification*, pas de transport d'état —
chaque client doit pouvoir reconstruire son affichage via un endpoint
autoritatif (`/api/counter/<id>/state`, `/announce/state`, fragments HTMX).

## Enveloppe

Tous les messages émis via `communication_websocket()` ont la forme :

```json
{"flag": <libre|id(s) comptoir>, "data": <charge utile>, "revision": <entier|null>}
```

- `flag` : ciblage grossier côté client (notifications adressées à un ou
  plusieurs comptoirs ; `null` = tout le monde). Rarement utilisé.
- `data` : **valeur JSON native** — objet, liste, chaîne ou `null` selon
  l'évènement (schéma ci-dessous). Ce n'est *jamais* une chaîne contenant du
  JSON sérialisé : le client n'a pas à faire `json.loads` sur `data`.
  (Historiquement `notification` envoyait `json.dumps(...)` — corrigé, les
  clients restent tolérants à l'ancien format.)
- `revision` : entier monotone de `queue_revision`, présent uniquement sur les
  évènements de mutation de la file (`update`, `update_patient_list`). Sert à
  écarter les doublons et à détecter un trou → resynchronisation.

## Namespaces

| Namespace | Accès | Consommateurs |
|---|---|---|
| `/socket_update_patient` | **public** (choix documenté) | écran (`announce.js`), comptoir web (`counter.js`), admin (`admin.js`) |
| `/socket_update_screen` | `SECURITY_LOGIN_SCREEN` | écran d'affichage (`announce.js`) |
| `/socket_admin` | session admin + permission | toutes les pages admin (`admin.js`) |
| `/socket_patient` | `SECURITY_LOGIN_PATIENT` | page borne (`patients.js`) |
| `/socket_app_counter` | `SECURITY_LOGIN_COUNTER` | App comptoir PySide |
| `/socket_app_screen` | `SECURITY_LOGIN_SCREEN` | *réservé — aucune App écran n'existe dans le dépôt* |
| `/socket_counter` | `SECURITY_LOGIN_COUNTER` | comptoir web (`counter.js`) |
| `/socket_phone` | cookies signés (`patient_token`) | page téléphone (`phone.js`) |

## Évènements par namespace

### `/socket_update_patient`

| Évènement | `data` | Effet |
|---|---|---|
| `update` | `null` (+ `revision`) | Déclencheur de rafraîchissement (HTMX / resync bannières). La liste complète n'y est plus diffusée (point 8). |

### `/socket_update_screen`

| Évènement | `data` | Effet |
|---|---|---|
| `add_calling` | `{id, counter_id, text}` | Ajoute une bannière d'appel (animée) |
| `remove_calling` | `{id}` | Retire une bannière d'appel |
| `refresh` | `null` | Rechargement complet de la page (changement de config) |
| `audio` | `string` — URL du fichier son | Joue l'annonce vocale (lecteur web) |
| `spotify_status` | `bool` | État de lecture Spotify (affichage/ducking) |

### `/socket_app_counter`

| Évènement | `data` | `flag` | Effet |
|---|---|---|---|
| `update_patient_list` | liste des patients (+ `revision`) | — | Snapshot complet de la file |
| `notification` | `{origin, message, timestamp, for_counter}` | id ou liste d'ids comptoir | Notification (toast PySide) |
| `paper` | `{add_paper: int}` | — | Mise à jour du niveau de papier |
| `disconnect_user` | `{counter_id, staff}` | — | Déconnexion forcée du comptoir |
| `change_auto_calling` | `{counter_id, autocalling: bool}` | — | Bascule de l'appel automatique |
| `update_auto_calling` | `{counter_id, patient: {...}}` | — | Patient servi en appel automatique |
| `refresh_after_clear_patient_list` | `null` | — | Resync après purge de la file |

#### Messagerie App Comptoir

`messaging_changed`, `messaging_presence_changed` et
`messaging_config_changed` ne transportent jamais le texte privé. Ils demandent
à l'App de relire l'état autoritatif par l'API REST.

### `/socket_counter` (comptoir web)

| Évènement | `data` | Effet |
|---|---|---|
| `update buttons` | `null` | Recharge les boutons d'action |
| `paper` | `null` | Recharge l'indicateur papier |
| `refresh_auto_calling` | `{auto_calling: bool}` | Recharge l'interrupteur d'appel auto |

### `/socket_patient` (borne)

| Évènement | `data` | Effet |
|---|---|---|
| `refresh` | `null` | Recharge la page borne |
| `refresh_title` | `null` | Recharge le titre |
| `refresh_buttons` | `null` | Recharge les boutons d'activité |
| `update_scan_phone` | `{call_number, patient_id}` — salle `scan_<journey>` | Signale le scan du QR du parcours affiché par cette borne |
| `print_ticket` | `string` — ESC/POS **base64** ; `flag` = `job_id` optionnel | Impression (ticket de test admin). Quand `flag` est présent, la borne acquitte via `print_test_result` |
| `print_test_result` (borne → serveur) | `{job_id, success, code, message, borne_id}` | Acquittement du tirage de test, relayé tel quel sur `/socket_admin` |

Chaque parcours QR est identifié par un UUID (`journey`) généré à l'affichage
de la page de validation, encodé dans l'URL du QR (`?journey=<uuid>`). La
borne émet `join_scan_journey {journey}` pour rejoindre la salle
`scan_<journey>` quand le fragment QR apparaît, `leave_scan_journey` quand il
disparaît ; `/patient/phone/ping` n'émet `update_scan_phone` que dans cette
salle — avec plusieurs bornes, une confirmation ne peut plus arriver sur le
mauvais écran. Sans `journey` (QR antérieur), aucune diffusion n'est faite.

Le QR lui-même n'est **pas** un fichier : `qr_code_data_uri` génère le PNG en
mémoire et la page l'embarque en `data:image/png;base64,...`. Un
`call_number` étant réutilisé d'un jour à l'autre, un fichier statique
`qr_patient-<numéro>.png` pouvait être servi périmé depuis le cache — plus
rien à cacher ni à nettoyer. La page de conclusion regénère le QR pour le
patient réellement inscrit (`patient_id`), y compris en impression directe
où aucun QR de validation n'existait.

#### Acquittement d'impression (`POST /patient/confirm_print`)

Après une impression physique, la borne DOIT obtenir une réponse du serveur
— sinon le patient repart avec un ticket absent de la file. L'acquittement
est donc persisté en `localStorage` **avant** le premier essai, puis retenté
jusqu'à réponse définitive (2xx/4xx ; les 5xx et erreurs réseau restent en
file). L'endpoint est idempotent : un appel répété renvoie l'état métier
(`activated` / `activated_no_ticket` / `cancelled` / `ask` / `expired`), sans
ré-exécuter — le statut interne brut (`standing`, `print_failed`) n'est pas
exposé. La file est vidée au `connect` Socket.IO, périodiquement, et au
chargement de la page.

### `/socket_admin`

Tous ces évènements déclenchent un rafraîchissement HTMX ciblé ; `data` est
`null` sauf mention contraire :

`update` (toast : `{toast, success, message}`), `refresh_colors`,
`refresh_activity_table`, `refresh_button_order`, `refresh_counter_order`,
`refresh_languages_order`, `refresh_dashboard_select`, `refresh_gallery_list`,
`display_new_gallery` (`string` — nom), `refresh_sound`,
`audio_test` (`string` — URL), `refresh_printer_dashboard`,
`refresh_counter_dashboard`, `refresh_schedule_tasks_list`,
`refresh_announce_cache`,
`print_test_result` (`{job_id, success, code, message, borne_id}` — acquittement
d'un tirage de test, relais de l'évènement émis par la borne).

### `/socket_phone`

Rooms `call_<numéro d'appel>`, jointes uniquement avec le cookie signé
`patient_token` (point 10). Émission **directe** (`socketio.emit`, hors
enveloppe `communikation`) :

| Évènement | Corps | Effet |
|---|---|---|
| `your_turn` | `{call_number}` | Écran « c'est votre tour » (vibration) |

### `/socket_app_screen`

Réservé à une future App écran. Le produit emploie actuellement toujours le
lecteur web : une ancienne configuration `ANNOUNCE_PLAYER=app` est ramenée au
lecteur web afin de ne jamais produire d'annonce silencieuse.

## Invariants

1. Émettre **après** le commit de la mutation (jamais avant — un rollback
   laisserait les clients afficher un état inexistant).
2. `bump_queue_revision` / `record_printer_status` utilisent une connexion
   dédiée : ils ne committent jamais la session de l'appelant.
3. Le namespace public `/socket_update_patient` ne transporte **pas** la
   liste des patients : enveloppe seule + révision.
4. Aucun secret dans les messages : le jeton OAuth Spotify reste côté
   serveur, `patient_token` ne circule que dans les cookies.
5. `data` est une valeur JSON native — jamais `json.dumps`.
