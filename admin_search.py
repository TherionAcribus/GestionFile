"""Index de la recherche globale de l'administration (palette « Ctrl+K »).

Module **pur** : aucune dépendance Flask ni base de données. Il décrit tout ce
qu'un administrateur peut chercher et la destination de chaque résultat ; la
route ``/admin/search/index`` (``routes/admin_search.py``) y ajoute les éléments
vivants (activités, comptoirs, équipe, règles, langues, rôles, comptes) puis
filtre par permission.

Trois sources :

1. **Catalogue manuel** (``CATALOG``) : pages, onglets, sections et actions, avec
   des synonymes (« purge », « backup », « TTS »…) — c'est là que se règle la
   pertinence ;
2. **Réglages extraits des gabarits** (``extract_settings``) : chaque appel de
   macro ``macros.xxx("<clé>", …, "<libellé>")`` ou ``<label for="<clé>">``
   dont la clé figure dans ``PARAM_REGISTRY`` devient un résultat. Un réglage
   ajouté dans un gabarit est donc trouvable sans rien déclarer ici ; seul un
   gabarit **nouveau** doit être rattaché à sa page dans ``TEMPLATE_LOCATIONS``
   (un test l'exige) ;
3. **Éléments vivants** (``entity_entry``) : construits par la route.

Destination : une URL interne fixe, éventuellement suivie de ``#focus=<id>``.
``admin_search.js`` lit ce fragment sur la page d'arrivée, ouvre l'onglet, le
volet ``<details>`` ou le bloc repliable qui contient l'élément, puis le fait
défiler à l'écran et le met en évidence. Aucune URL ne vient du client.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from functools import lru_cache

from params_registry import PARAM_REGISTRY

# --- Types de résultat ------------------------------------------------------
KIND_PAGE = "page"
KIND_TAB = "tab"
KIND_SECTION = "section"
KIND_ACTION = "action"
KIND_SETTING = "setting"
KIND_ENTITY = "entity"

KIND_LABELS = {
    KIND_PAGE: "Page",
    KIND_TAB: "Onglet",
    KIND_SECTION: "Section",
    KIND_ACTION: "Action",
    KIND_SETTING: "Réglage",
    KIND_ENTITY: "Élément",
}

# Ordre d'affichage à pertinence égale (le JS l'utilise comme départage).
KIND_ORDER = (KIND_PAGE, KIND_TAB, KIND_ACTION, KIND_SECTION, KIND_SETTING, KIND_ENTITY)

# Permission « tout compte admin authentifié » (accueil, configuration guidée).
ANY = None


def _e(kind, title, url, permission, section="", keywords="", icon=""):
    """Entrée de catalogue (dict sérialisable tel quel en JSON)."""
    return {
        "kind": kind,
        "title": title,
        "url": url,
        "permission": permission,
        "section": section,
        "keywords": keywords,
        "icon": icon,
    }


# Libellés des pages, réutilisés comme fil d'Ariane des onglets et sections.
P_QUEUE = "File d'attente"
P_COUNTER = "Comptoirs"
P_STAFF = "Équipe"
P_ACTIVITY = "Activités"
P_ALGO = "Priorités"
P_SCHEDULE = "Planification"
P_TRANSLATION = "Traductions"
P_PATIENT = "Borne patient"
P_ANNOUNCE = "Écran d'annonce"
P_PHONE = "Affichage téléphone"
P_GALLERY = "Galerie média"
P_MUSIC = "Musique"
P_STATS = "Statistiques"
P_PERF = "Tests de performance"
P_APP = "Application"
P_DATA = "Données"
P_SECURITY = "Sécurité"

# Permissions « l'une ou l'autre » (même règle que la barre latérale).
_MUSIC = ("music_play", "music_options")
_STATS = ("stats", "performance")

CATALOG = (
    # --- Pages (miroir de la barre latérale, même permissions) -------------
    _e(KIND_PAGE, "Accueil — tableau de bord", "/admin", ANY, "",
       "dashboard cartes résumé synthèse alertes", "bi-house-door"),
    _e(KIND_PAGE, P_QUEUE, "/admin/queue", "queue", "Exploitation",
       "patients attente file liste tickets en cours", "bi-people"),
    _e(KIND_PAGE, P_COUNTER, "/admin/counter", "counter", "Exploitation",
       "comptoir poste guichet caisse", "bi-window-stack"),
    _e(KIND_PAGE, P_STAFF, "/admin/staff", "staff", "Exploitation",
       "personnel pharmaciens préparateurs membres initiales compétences",
       "bi-person-badge"),
    _e(KIND_PAGE, P_ACTIVITY, "/admin/activity", "activity", "Exploitation",
       "motifs actes services lettres boutons", "bi-card-list"),
    _e(KIND_PAGE, P_ALGO, "/admin/algo", "algo", "Configuration",
       "algorithme priorité règles ordre d'appel prochain patient",
       "bi-sort-numeric-down"),
    _e(KIND_PAGE, P_SCHEDULE, "/admin/database", "schedule", "Configuration",
       "tâches planifiées cron remise à zéro purge nuit horaires",
       "bi-calendar-week"),
    _e(KIND_PAGE, P_TRANSLATION, "/admin/translations", "translation",
       "Configuration", "langues traduction anglais langue drapeaux",
       "bi-translate"),
    _e(KIND_PAGE, P_PATIENT, "/admin/patient", "patient", "Affichage et contenus",
       "borne kiosque écran tactile accueil patient ticket", "bi-tablet"),
    _e(KIND_PAGE, P_ANNOUNCE, "/admin/announce", "announce", "Affichage et contenus",
       "écran salle d'attente télévision display affichage appel",
       "bi-display"),
    _e(KIND_PAGE, P_PHONE, "/admin/phone", "phone", "Affichage et contenus",
       "téléphone smartphone mobile qr code suivi", "bi-telephone"),
    _e(KIND_PAGE, P_GALLERY, "/admin/info", "gallery", "Affichage et contenus",
       "images diaporama photos médias informations", "bi-images"),
    _e(KIND_PAGE, P_MUSIC, "/admin/music", _MUSIC, "Affichage et contenus",
       "spotify playlist son ambiance", "bi-music-note-beamed"),
    _e(KIND_PAGE, P_STATS, "/admin/stats", _STATS, "Analyse",
       "graphiques temps d'attente affluence chiffres rapports", "bi-bar-chart"),
    _e(KIND_PAGE, "Historique détaillé", "/admin/stats/history", _STATS, "Analyse",
       "historique patients passés journal export csv", "bi-clock-history"),
    _e(KIND_PAGE, P_PERF, "/admin/performance", "performance", "Analyse",
       "stress charge test performance latence", "bi-speedometer2"),
    _e(KIND_PAGE, P_APP, "/admin/app", "app", "Administration",
       "paramètres généraux application comptoir réglages", "bi-gear"),
    _e(KIND_PAGE, P_DATA, "/admin/data", "options", "Administration",
       "base de données purge archives volumes taille", "bi-database"),
    _e(KIND_PAGE, P_SECURITY, "/admin/security", "security_view", "Administration",
       "utilisateurs comptes rôles mots de passe droits", "bi-shield-lock"),
    _e(KIND_PAGE, "Mon compte", "/admin/account/password", ANY, "Mon compte",
       "changer mon mot de passe adresse e-mail compte personnel identifiant", "bi-person-gear"),
    _e(KIND_PAGE, "Configuration guidée", "/admin/onboarding", ANY, "Aide",
       "aide démarrage premier lancement assistant tutoriel parcours",
       "bi-compass"),

    # --- Onglets ------------------------------------------------------------
    _e(KIND_TAB, "Demandes nominatives", "/admin/activity?tab=staff#focus=tab_staff",
       "activity", P_ACTIVITY, "demande nominative pharmacien précis personne",
       "bi-person-lines-fill"),
    _e(KIND_TAB, "Plages horaires", "/admin/activity?tab=schedule#focus=tab_horaires",
       "activity", P_ACTIVITY, "horaires ouverture fermeture créneaux jours",
       "bi-clock"),
    _e(KIND_TAB, "Langues", "/admin/translations#focus=tab_languages",
       "translation", P_TRANSLATION, "ajouter langue activer ordre drapeau",
       "bi-flag"),
    _e(KIND_TAB, "Textes à traduire", "/admin/translations#focus=tab_texts",
       "translation", P_TRANSLATION, "traductions boutons activités textes",
       "bi-chat-left-text"),
    _e(KIND_TAB, "Apparence et parcours", "/admin/patient/visual#focus=tab_visual",
       "patient", P_PATIENT, "couleurs textes accueil boutons drapeaux",
       "bi-palette"),
    _e(KIND_TAB, "Boutons d'activité", "/admin/patient/buttons#focus=tab_buttons",
       "patient", P_PATIENT, "boutons borne ordre images parents enfants",
       "bi-grid-3x3-gap"),
    _e(KIND_TAB, "Ticket", "/admin/patient/ticket#focus=tab_ticket",
       "patient", P_PATIENT, "ticket imprimante impression en-tête pied aperçu",
       "bi-receipt"),
    _e(KIND_TAB, "QR code", "/admin/patient/qrcode#focus=tab_qrcode",
       "patient", P_PATIENT, "qrcode qr code scanner téléphone", "bi-qr-code"),
    _e(KIND_TAB, "Apparence de l'écran d'annonce", "/admin/announce/visual#focus=tab_visual",
       "announce", P_ANNOUNCE, "couleurs titre textes visuel", "bi-palette"),
    _e(KIND_TAB, "Annonces sonores", "/admin/announce/audio#focus=tab_audio",
       "announce", P_ANNOUNCE, "audio voix son synthèse vocale tts annonce",
       "bi-volume-up"),
    _e(KIND_TAB, "Pendant l'attente", "/admin/phone#focus=tab_phoneMaine",
       "phone", P_PHONE, "page de confirmation lignes téléphone", "bi-hourglass"),
    _e(KIND_TAB, "Quand c'est son tour", "/admin/phone#focus=phoneYourTurn",
       "phone", P_PHONE, "notification tour appel téléphone", "bi-bell"),
    _e(KIND_TAB, "Lecture de la musique", "/admin/music/player#focus=tab_player",
       "music_play", P_MUSIC, "lecteur playlists jouer pause", "bi-play-circle"),
    _e(KIND_TAB, "Options de la musique", "/admin/music/options#focus=tab_options",
       "music_options", P_MUSIC, "volume spotify connexion", "bi-sliders"),
    _e(KIND_TAB, "Général (application)", "/admin/app?tab=general#focus=tab_general",
       "app", P_APP, "nom pharmacie numérotation messagerie réseau", "bi-sliders"),
    _e(KIND_TAB, "E-mails", "/admin/app?tab=mail#focus=tab_mail",
       "app", P_APP, "mail smtp courriel envoi serveur", "bi-envelope"),
    _e(KIND_TAB, "Connexions en direct", "/admin/app?tab=connexion#focus=tab_connexion",
       "app", P_APP, "clients connectés websocket comptoirs bornes écrans en ligne",
       "bi-broadcast"),
    _e(KIND_TAB, "Sauvegardes", "/admin/app?tab=backups#focus=tab_backups",
       "app", P_APP, "backup sauvegarde restauration export import zip",
       "bi-archive"),
    _e(KIND_TAB, "Utilisateurs", "/admin/security?tab=users#focus=tab_users",
       "security_view", P_SECURITY, "comptes utilisateurs login identifiants",
       "bi-people"),
    _e(KIND_TAB, "Rôles", "/admin/security?tab=roles#focus=tab_roles",
       "security_view", P_SECURITY, "rôles permissions droits accès", "bi-person-gear"),
    _e(KIND_TAB, "Mots de passe des pages", "/admin/security?tab=general#focus=tab_general",
       "security_view", P_SECURITY,
       "mot de passe pages protégées connexion se souvenir", "bi-key"),

    # --- Éditeurs visuels ---------------------------------------------------
    _e(KIND_ACTION, "Éditeur visuel de la borne", "/admin/page-editor/patient",
       "patient", P_PATIENT, "éditeur visuel aperçu mise en page wysiwyg",
       "bi-pencil-square"),
    _e(KIND_ACTION, "Éditeur visuel de l'écran d'annonce", "/admin/page-editor/announce",
       "announce", P_ANNOUNCE, "éditeur visuel aperçu mise en page wysiwyg",
       "bi-pencil-square"),
    _e(KIND_ACTION, "Éditeur visuel de l'affichage téléphone", "/admin/page-editor/phone",
       "phone", P_PHONE, "éditeur visuel aperçu mise en page wysiwyg",
       "bi-pencil-square"),

    # --- Actions courantes --------------------------------------------------
    _e(KIND_ACTION, "Ajouter un patient", "/admin/queue#focus=queue_add_title",
       "queue", P_QUEUE, "nouveau patient créer ticket manuel", "bi-person-plus"),
    _e(KIND_ACTION, "Vider la file d'attente", "/admin/queue#focus=div_queue_table",
       "queue", P_QUEUE, "vider effacer supprimer tous les patients reset",
       "bi-trash"),
    _e(KIND_ACTION, "Exporter ou restaurer une sauvegarde",
       "/admin/app?tab=backups#focus=tab_backups", "app", P_APP,
       "backup exporter importer restaurer zip base de données", "bi-download"),
    _e(KIND_ACTION, "Envoyer un e-mail de test", "/admin/app?tab=mail#focus=mail_adress",
       "app", P_APP, "tester mail smtp vérifier envoi", "bi-send"),
    _e(KIND_ACTION, "Tester une annonce vocale", "/admin/announce/audio#focus=audio_test_title",
       "announce", P_ANNOUNCE, "tester écouter voix son annonce", "bi-play-circle"),
    _e(KIND_ACTION, "Tester le QR code", "/admin/patient/qrcode#focus=tab_qrcode",
       "patient", P_PATIENT, "tester scanner qrcode", "bi-qr-code-scan"),
    _e(KIND_ACTION, "Aperçu du ticket", "/admin/patient/ticket#focus=ticket-preview-title",
       "patient", P_PATIENT, "aperçu ticket impression imprimer", "bi-eye"),
    _e(KIND_ACTION, "Exporter l'historique (CSV)", "/admin/stats/history",
       _STATS, "Historique détaillé", "export csv excel tableur télécharger",
       "bi-filetype-csv"),
    _e(KIND_ACTION, "Graphique personnalisé", "/admin/stats#focus=stats_custom_title",
       _STATS, P_STATS, "graphique courbe créer comparer", "bi-graph-up"),
    _e(KIND_ACTION, "Lancer un test de performance", "/admin/performance",
       "performance", P_PERF, "stress charge lancer test", "bi-play"),
    _e(KIND_ACTION, "Choisir les cartes du tableau de bord", "/admin#focus=sortable-dashboard",
       ANY, "Accueil", "cartes tableau de bord personnaliser ordre", "bi-grid"),

    # --- Sections (titres de cartes avec ancre) ----------------------------
    _e(KIND_SECTION, "État de l'algorithme", "/admin/algo#focus=algo_status_title",
       "algo", P_ALGO, "activer désactiver algorithme", "bi-toggle-on"),
    _e(KIND_SECTION, "Comment l'algorithme choisit le prochain patient",
       "/admin/algo#focus=algo_how_title", "algo", P_ALGO,
       "explication fonctionnement ordre", "bi-question-circle"),
    _e(KIND_SECTION, "Garde-fou de l'algorithme", "/admin/algo#focus=algo_guard_title",
       "algo", P_ALGO, "dépassements limite équité", "bi-shield-check"),
    _e(KIND_SECTION, "Règles de priorité", "/admin/algo#focus=algo_rules_title",
       "algo", P_ALGO, "règle ajouter priorité", "bi-list-ol"),
    _e(KIND_SECTION, "Appel automatique des comptoirs", "/admin/counter#focus=counter_auto_title",
       "counter", P_COUNTER, "auto appel automatique suivant", "bi-arrow-repeat"),
    _e(KIND_SECTION, "Remise à zéro quotidienne de la file", "/admin/database#focus=purge_title",
       "schedule", P_SCHEDULE, "purge vider chaque nuit reset minuit", "bi-arrow-counterclockwise"),
    _e(KIND_SECTION, "Toutes les tâches planifiées", "/admin/database#focus=tasks_title",
       "schedule", P_SCHEDULE, "cron jobs tâches ouvertures fermetures", "bi-list-task"),
    _e(KIND_SECTION, "Cache des annonces vocales", "/admin/database#focus=announce_cache_title",
       "schedule", P_SCHEDULE, "cache mp3 audio annonces nettoyage", "bi-hdd"),
    _e(KIND_SECTION, "Galerie d'images de l'écran d'annonce",
       "/admin/announce/visual#focus=announce-gallery-settings", "announce", P_ANNOUNCE,
       "diaporama images galerie taille durée", "bi-images"),
    _e(KIND_SECTION, "Signal avant l'annonce", "/admin/announce/audio#focus=audio_signal_title",
       "announce", P_ANNOUNCE, "ding gong sonnerie bip son", "bi-bell"),
    _e(KIND_SECTION, "Texte lu à voix haute", "/admin/announce/audio#focus=audio_text_title",
       "announce", P_ANNOUNCE, "phrase annonce voix", "bi-chat-quote"),
    _e(KIND_SECTION, "Moteur de voix (Google, gTTS)", "/admin/announce/audio#focus=audio_engine_title",
       "announce", P_ANNOUNCE, "google cloud tts gtts clé api synthèse", "bi-cpu"),
    _e(KIND_SECTION, "Voix de chaque langue", "/admin/announce/audio#focus=audio_voices_title",
       "announce", P_ANNOUNCE, "voix langue homme femme", "bi-translate"),
    _e(KIND_SECTION, "Réglages du diaporama", "/admin/info#focus=gallery_settings_title",
       "gallery", P_GALLERY, "diaporama durée transition", "bi-sliders"),
    _e(KIND_SECTION, "Nom de la pharmacie", "/admin/app?tab=general#focus=app_identity_title",
       "app", P_APP, "nom officine identité enseigne", "bi-shop"),
    _e(KIND_SECTION, "Couleurs de l'administration",
       "/admin/app?tab=general#focus=app_admin_colors_title", "app", P_APP,
       "thème couleurs admin sombre clair", "bi-palette"),
    _e(KIND_SECTION, "Numérotation des tickets", "/admin/app?tab=general#focus=app_numbering_title",
       "app", P_APP, "numéros série lettre compteur", "bi-123"),
    _e(KIND_SECTION, "Messagerie entre comptoirs", "/admin/app?tab=general#focus=app_messaging_title",
       "app", P_APP, "chat messages instantanés", "bi-chat-dots"),
    _e(KIND_SECTION, "Libération automatique de l'écran d'appel",
       "/admin/app?tab=general#focus=call_auto_validation_title", "app", P_APP,
       "validation automatique appel délai", "bi-hourglass-split"),
    _e(KIND_SECTION, "Adresse du serveur sur le réseau",
       "/admin/app?tab=general#focus=app_network_title", "app", P_APP,
       "ip url adresse port réseau lan", "bi-hdd-network"),
    _e(KIND_SECTION, "Serveur d'envoi des e-mails", "/admin/app?tab=mail#focus=mail_server_title",
       "app", P_APP, "smtp gmail outlook ovh", "bi-envelope-at"),
    _e(KIND_SECTION, "Temps réel des comptoirs en service",
       "/admin/performance#focus=realtime-health-title", "performance", P_PERF,
       "révision trous websocket santé", "bi-activity"),
)


# ---------------------------------------------------------------------------
# Réglages extraits des gabarits
# ---------------------------------------------------------------------------

#: Gabarit -> (fil d'Ariane, URL de la page, permission de la page). L'ordre
#: fait foi quand une même clé apparaît dans plusieurs gabarits.
TEMPLATE_LOCATIONS = {
    "app_general.html": ("Application › Général", "/admin/app?tab=general", "app"),
    "app_mail.html": ("Application › E-mails", "/admin/app?tab=mail", "app"),
    "algo.html": (P_ALGO, "/admin/algo", "algo"),
    "database.html": (P_SCHEDULE, "/admin/database", "schedule"),
    "database_announce_cache.html": (P_SCHEDULE, "/admin/database", "schedule"),
    "announce_visual.html": ("Écran d'annonce › Apparence (mode avancé)",
                             "/admin/announce/visual", "announce"),
    "announce_gallery.html": ("Écran d'annonce › Galerie d'images",
                              "/admin/announce/visual", "announce"),
    "announce_audio.html": ("Écran d'annonce › Annonces sonores",
                            "/admin/announce/audio", "announce"),
    "page_patient_page.html": ("Borne patient › Apparence et parcours",
                               "/admin/patient/visual", "patient"),
    "page_patient_ticket.html": ("Borne patient › Ticket", "/admin/patient/ticket", "patient"),
    "page_patient_qrcode.html": ("Borne patient › QR code", "/admin/patient/qrcode", "patient"),
    "phone_confirmation_page.html": ("Affichage téléphone › Pendant l'attente",
                                     "/admin/phone", "phone"),
    "phone_your_turn_page.html": ("Affichage téléphone › Quand c'est son tour",
                                  "/admin/phone", "phone"),
    "music_options.html": ("Musique › Options", "/admin/music/options", "music_options"),
    "security_general.html": ("Sécurité › Général", "/admin/security?tab=general",
                              "security_view"),
}

#: Libellés trop vagues dans leur gabarit (« Texte », « int »…) : on les
#: remplace par un titre explicite. Un test vérifie qu'aucun réglage indexé ne
#: garde un libellé générique.
LABEL_OVERRIDES = {
    "page_patient_title": "Titre de la page d'accueil de la borne",
    "page_patient_subtitle": "Texte en bas de la page d'accueil de la borne",
    "page_patient_interface_validate_scan": "Bouton « Valider » après le scan",
    "page_patient_interface_scan_explanation": "Texte d'explication pour scanner le QR code",
    "call_auto_validation_warning_seconds": "Libération automatique : avertissement après (s)",
    "call_auto_validation_grace_seconds": "Libération automatique : délai de grâce (s)",
    "call_auto_validation_extension_seconds": "Libération automatique : prolongation (s)",
    "stress_reference_patients": "Tests de performance : nombre de patients de référence",
    "pharmacy_name": "Nom de la pharmacie",
    "mail_password": "Mot de passe du compte e-mail (SMTP)",
    "mail_port": "Port du serveur e-mail (SMTP)",
    "mail_username": "Identifiant du compte e-mail (SMTP)",
    "mail_link_base_url": "Adresse de l'administration dans les e-mails",
    "printer_width": "Largeur d'impression du ticket",
    "ticket_header": "En-tête du ticket",
    "ticket_message": "Corps du ticket",
    "ticket_footer": "Pied du ticket",
    "page_patient_qrcode_data": "Données du QR code",
    "phone_title": "Titre de la page téléphone",
    "music_spotify": "Connexion à Spotify",
    "admin_colors": "Couleurs de l'administration (palette)",
    "announce_title": "Titre de l'écran d'annonce",
    "announce_subtitle": "Sous-titre de l'écran d'annonce",
    "page_patient_interface_printing": "Message « Impression en cours » de la borne",
    "page_patient_print_uncertain_timer": "Impression incertaine : délai laissé au patient pour répondre (s)",
    "page_patient_print_uncertain_timeout_action": "Impression incertaine : action si le patient ne répond pas",
    "page_patient_print_fail_behavior": "Échec d'impression : que faire si le ticket ne sort pas",
    "page_patient_end_timer": "Délai avant le retour à l'accueil de la borne (s)",
}

#: Libellés jugés trop vagues pour être affichés seuls.
GENERIC_LABELS = frozenset({
    "texte", "int", "text", "valeur", "titre", "taille", "couleur", "hauteur",
    "largeur", "épaisseur", "message", "bouton", "port", "mot de passe",
})

_MACRO_CALL = re.compile(
    r"macros\.(\w+)\(\s*[\"']([a-z0-9_]+)[\"']\s*,(.*?)\)\s*}}", re.S)
_STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'")
_LABEL_FOR = re.compile(
    r"<label\b[^>]*\bfor=[\"']([a-z0-9_]+)[\"'][^>]*>(.*?)</label>", re.S)
_TAG = re.compile(r"<[^>]+>|{#.*?#}|{{.*?}}|{%.*?%}", re.S)
# Valeurs techniques passées en argument de macro (pas des libellés).
_NOT_A_LABEL = frozenset({"int", "text", "bool", "hour", "check", "px", "%", "s"})


def _clean(text):
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _labels_in(source):
    """Couples (clé, libellé) déclarés dans le source d'un gabarit."""
    found = []
    for match in _MACRO_CALL.finditer(source):
        key, args = match.group(2), match.group(3)
        label = ""
        for dq, sq in _STRING.findall(args):
            candidate = _clean(dq or sq)
            if candidate and candidate.lower() not in _NOT_A_LABEL:
                label = candidate
                break
        found.append((key, label))
    for match in _LABEL_FOR.finditer(source):
        found.append((match.group(1), _clean(match.group(2))))
    return found


def setting_title(key, label):
    """Titre affiché d'un réglage : correction explicite, sinon le libellé."""
    return LABEL_OVERRIDES.get(key) or label


def is_generic_label(title):
    return not title or len(title) < 6 or title.strip().lower() in GENERIC_LABELS


def extract_settings(templates_dir, locations=None):
    """Réglages trouvables, extraits des gabarits listés dans ``locations``.

    ``templates_dir`` est le dossier ``templates/admin``. Une clé n'est retenue
    que si elle figure dans ``PARAM_REGISTRY`` (vraie option de configuration).
    Seuls le libellé et l'emplacement sont indexés, jamais la **valeur** : un
    réglage secret (mot de passe SMTP…) reste trouvable sans rien exposer. Le
    saut direct vise l'élément ``id="<clé>"`` posé par les macros.
    """
    locations = TEMPLATE_LOCATIONS if locations is None else locations
    entries, seen = [], set()
    for template, (breadcrumb, url, page_permission) in locations.items():
        path = os.path.join(templates_dir, template)
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except OSError:
            continue
        labels = {}
        for key, label in _labels_in(source):
            if key in PARAM_REGISTRY and not labels.get(key):
                labels[key] = label
        for key, label in labels.items():
            if key in seen:
                continue
            title = setting_title(key, label)
            if is_generic_label(title):
                continue
            seen.add(key)
            spec = PARAM_REGISTRY[key]
            permissions = tuple(dict.fromkeys(p for p in (page_permission, spec.permission) if p))
            entries.append({
                "kind": KIND_SETTING,
                "title": title,
                "url": f"{url}#focus={key}",
                # Toutes requises : voir la page ET pouvoir modifier le réglage.
                "permission": permissions,
                "permission_mode": "all",
                "section": breadcrumb,
                "keywords": key.replace("_", " "),
                "icon": "bi-sliders2",
            })
    return entries


@lru_cache(maxsize=4)
def static_index(templates_dir):
    """``templates_dir`` : dossier ``templates/admin``."""
    # Catalogue + réglages, calculés une fois par processus (gabarits figés).
    return tuple(list(CATALOG) + extract_settings(templates_dir))


# ---------------------------------------------------------------------------
# Filtrage par permission et éléments vivants
# ---------------------------------------------------------------------------

def is_allowed(entry, can):
    """``can(resource) -> bool`` ; permission absente = tout compte admin.

    Un tuple vaut « l'une ou l'autre », sauf ``permission_mode == "all"``.
    """
    permission = entry.get("permission")
    if permission is None:
        return True
    if isinstance(permission, str):
        return bool(can(permission))
    if entry.get("permission_mode") == "all":
        return all(can(p) for p in permission)
    return any(can(p) for p in permission)


def public_entry(entry):
    """Forme envoyée au navigateur : sans les champs de contrôle d'accès."""
    data = {k: v for k, v in entry.items() if k not in ("permission", "permission_mode")}
    data["kind_label"] = KIND_LABELS.get(entry["kind"], "")
    return data


def visible_entries(entries, can):
    return [public_entry(e) for e in entries if is_allowed(e, can)]


_SAFE_ANCHOR = re.compile(r"^[A-Za-z0-9_-]+$")


def entity_entry(title, url, anchor, section, keywords="", icon=""):
    """Résultat pour un élément vivant (activité, comptoir…).

    ``anchor`` doit être un identifiant HTML sûr (les identifiants proviennent
    de la base : on refuse tout ce qui n'est pas ``[A-Za-z0-9_-]``).
    """
    if anchor and _SAFE_ANCHOR.match(anchor):
        url = f"{url}#focus={anchor}"
    return {
        "kind": KIND_ENTITY,
        "title": str(title or "").strip() or "(sans nom)",
        "url": url,
        "section": section,
        "keywords": keywords,
        "icon": icon,
        "kind_label": KIND_LABELS[KIND_ENTITY],
    }


def normalize(text):
    """Minuscules sans accents — même règle que ``normalize`` côté JS."""
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def matches(entry, query):
    """Tous les mots de ``query`` figurent dans titre/section/mots-clés.

    Référence Python de la règle appliquée par ``admin_search.js`` (utilisée
    par les tests pour vérifier que les recherches usuelles aboutissent).
    """
    haystack = normalize(" ".join(
        str(entry.get(f, "")) for f in ("title", "section", "keywords")))
    return all(token in haystack for token in normalize(query).split())
