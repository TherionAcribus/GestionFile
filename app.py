# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger

import os

# eventlet must be monkey-patched before importing Flask/Werkzeug for the web server.
# BUT: when `flask db ...` imports this module, Flask/Werkzeug are already loaded
# by the CLI, and eventlet patching can crash due to LocalProxy objects.
# Solution: skip patching during CLI/migrations (see Dockerfile/Procfile/compose).
_skip_eventlet_patch = os.getenv("SKIP_EVENTLET_PATCH", "").strip().lower() in {"1", "true", "yes", "on"}
if not _skip_eventlet_patch:
    import eventlet
    eventlet.monkey_patch()  # thread=True, time=True
from flask import Flask, request, redirect, url_for, session, jsonify, g, make_response, has_request_context

from flask.signals import request_started
from flask_wtf.csrf import CSRFError
from datetime import datetime, timedelta
import time as tm

import json

import logging
import subprocess
import threading
import socket
import pika


from functools import partial

from flask_security import Security, current_user, SQLAlchemyUserDatastore

from dotenv import load_dotenv
from markupsafe import escape

from auth_utils import is_valid_app_secret_config, wants_json_response, verify_app_token

from models import db, Patient, Language, ConfigOption, User, Role
from extensions import csrf, mail, migrate, scheduler, socketio, configure_scheduler, init_socketio, start_scheduler
import sockets
from init_restore import init_default_buttons_db_from_json, init_default_options_db_from_json, init_default_languages_db_from_json, init_or_update_default_texts_db_from_json, init_update_default_translations_db_from_json, init_default_algo_rules_db_from_json, init_days_of_week_db_from_json, init_activity_schedules_db_from_json, clear_counter_table, init_counters_data_from_json, init_default_activities_db_from_json, restore_databases, init_default_dashboard_db_from_json, init_default_patient_css_variables_db_from_json, init_default_announce_css_variables_db_from_json, init_default_phone_css_variables_db_from_json
from backup import backup_databases
from routes.admin_backup import admin_backup_bp
from routes.api_system import api_system_bp
from routes.calling import calling_bp
from routes.admin_config import admin_config_bp
from scheduler_functions import clear_old_patients_table
from bdd import init_database
from config import Config, time_tz
from variables import MultiCssVariableManager
from css_manager import CSSManager

from app_holder import AppHolder

from routes.counter import counter_bp
from routes.admin_announce import admin_announce_bp
from routes.admin_counter import admin_counter_bp
from routes.admin_activity import admin_activity_bp
from routes.admin_algo import admin_algo_bp
from routes.admin_gallery import admin_gallery_bp
from routes.admin_phone import admin_phone_bp
from routes.admin_staff import admin_staff_bp
from routes.admin_patient import admin_patient_bp
from routes.admin_queue import admin_queue_bp
from routes.admin_translation import admin_translation_bp
from routes.admin_options import admin_options_bp
from routes.admin_schedule import admin_schedule_bp
from routes.admin_security import admin_security_bp, create_default_user, create_default_role, require_permission
from routes.admin_music import admin_music_bp
from routes.admin_dashboard import admin_dashboard_bp
from routes.admin_app import admin_app_bp
from routes.admin_data import admin_data_bp
from routes.announce import announce_bp
from routes.admin_stats import admin_stats_bp
from routes.patient import patient_bp
from routes.pyside import pyside_bp
from routes.home import home_bp
from python.engine import engine_bp
from params_registry import CONFIG_MAPPINGS
from config_loader import load_config_options
import config_sync

database = os.getenv("DATABASE_TYPE", getattr(Config, "database", "mysql"))
# A mettre dans la BDD ?
status_list = ['ongoing', 'standing', 'done', 'calling']

APP_ROLE = os.getenv("APP_ROLE", "all").strip().lower()
VALID_APP_ROLES = {"all", "web", "scheduler", "init"}
if APP_ROLE not in VALID_APP_ROLES:
    APP_ROLE = "all"
SKIP_STARTUP_HOOKS = os.getenv("SKIP_STARTUP_HOOKS", "").strip().lower() in {"1", "true", "yes", "on"}

server_port = int(os.environ.get("PORT", 5000))

_rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
parameters = pika.URLParameters(_rabbitmq_url)



# Charge des valeurs qui ne sont pas amener à changer avant redémarrage APP
def load_configuration(app):
    app.logger.info("Loading configuration from database")

    # Valeur par défaut pour le thème de couleurs
    app.config.setdefault("ADMIN_COLORS", "lumen")

    # Comportement patient en cas d'échec d'impression (paramétrable en Admin,
    # onglet Page Patient). Défauts appliqués si aucune ligne ConfigOption
    # n'existe encore (installation neuve). 'ask' : proposer Réessayer / Appeler
    # le personnel. 'keep' : garder le patient dans la file. 'cancel' : annuler.
    app.config.setdefault("PAGE_PATIENT_PRINT_FAIL_BEHAVIOR", "ask")
    app.config.setdefault("PAGE_PATIENT_PRINT_FAIL_SHOW_RETRY", True)
    app.config.setdefault("PAGE_PATIENT_PRINT_FAIL_SHOW_STAFF", True)
    # Délai (s) avant retour automatique à l'accueil sur l'écran d'échec en mode
    # 'ask' si le patient ne choisit rien (garde-fou anti-blocage borne). 0 = jamais.
    app.config.setdefault("PAGE_PATIENT_PRINT_FAIL_ABANDON_TIMER", 60)
    # Libellés patient du flux d'impression (traduisibles : FR = app.config,
    # autres langues via la table Translation avec repli FR).
    app.config.setdefault("PAGE_PATIENT_INTERFACE_PRINTING", "Impression en cours…")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_PRINT_FAILED", "Impression impossible.")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_RETRY", "Réessayer")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_CALL_STAFF", "Appeler le personnel")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_STAFF_CALLED", "Le personnel a été prévenu. Veuillez noter votre numéro :")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_NO_TICKET", "Ticket non imprimé. Veuillez noter votre numéro :")
    app.config.setdefault("PAGE_PATIENT_INTERFACE_PRINT_FAILED_STAFF", "Impression impossible. Veuillez vous adresser au personnel.")

    # Table clé -> (nom app.config, colonne ConfigOption) : dérivée du registre
    # centralisé (params_registry) afin que « clés chargées » et « clés
    # modifiables » restent strictement identiques.
    config_mappings = CONFIG_MAPPINGS

    # Point 12 (performances) : une SEULE requête pour toutes les options au lieu
    # d'une par clé (~130 allers-retours). ``load_config_options`` récupère les
    # lignes utiles d'un coup, les indexe par ``config_key`` et applique le
    # registre typé en mémoire. On ne pose que les clés réellement présentes en
    # base (les défauts déjà en place pour les clés absentes sont préservés).
    for config_name, value in load_config_options(ConfigOption, config_mappings).items():
        app.config[config_name] = value

    # Handling special case for cron_delete_patient_table_activated
    #if app.config.get('CRON_DELETE_PATIENT_TABLE_ACTIVATED'):
    #    scheduler_clear_all_patients()

    # Chargement des voix françaises
    french = Language.query.filter_by(code="fr").first()
    app.config["VOICE_MODEL"] = french.voice_model
    app.config["VOICE_GTTS_NAME"] = french.voice_gtts_name
    app.config["VOICE_GOOGLE_NAME"] = french.voice_google_name
    app.config["VOICE_GOOGLE_REGION"] = french.voice_google_region
    app.logger.debug('VOICE_MODEL %s', app.config["VOICE_MODEL"])

    # printer — état RUNTIME (historique du statut imprimante, poussé par l'App
    # Patient et accumulé en mémoire). Point 11 : ``load_configuration`` peut être
    # rappelée à chaud (rechargement après changement de config par un autre
    # processus) ; on utilise ``setdefault`` pour NE PAS écraser cet état runtime
    # à chaque rechargement (seul le tout premier chargement l'initialise).
    app.config.setdefault("PRINTER_INFOS", [])
    app.config.setdefault("PRINTER_ERROR", {
        'error': True,
        'message': "pas de connexion à l'App Patient",
        'timestamp': datetime.now(time_tz)
    })

    # TMP FIX adresse galleries
    app.config["ANNOUNCE_GALLERY_FOLDERS"]= "static/galleries"

    # stockage de la durée de conservation des cookies pour les mots de passe
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=app.config["SECURITY_REMEMBER_DURATION"])

    # NOTE: il n'y a plus de liste app.config["AUTO_CALLING"]. Ce statut vit
    # desormais uniquement dans Counter.auto_calling (base). La liste etait mutee
    # en place depuis deux modules, donc propre a un processus : elle divergeait
    # entre les conteneurs `web` et `scheduler`, et ce rechargement de
    # configuration la reconstruisait en ecrasant les mutations en vol.
    # Voir services/calling_service.counters_en_appel_automatique().

    #start_serveo_tunnel_in_thread()
    #flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port, debug=debug))
    #flask_thread.start()


def _is_app_role(*roles):
    return APP_ROLE == "all" or APP_ROLE in roles


def start_fonctions(app, *, run_bootstrap: bool, run_runtime: bool, run_startup_cleanup: bool):
    if run_bootstrap:
        init_database(database, db)

        # Création du rôle admin et de l'utilisateur admin par défaut
        create_default_role()  # Toujours créer le rôle admin s'il n'existe pas
        create_default_user()  # Crée l'utilisateur admin seulement s'il n'y a pas d'utilisateurs

        init_days_of_week_db_from_json()
        init_activity_schedules_db_from_json()
        init_default_activities_db_from_json()
        init_counters_data_from_json()  # a verifier
        #init_staff_data_from_json()  A refaire
        init_default_options_db_from_json()
        init_default_buttons_db_from_json()
        init_default_languages_db_from_json()
        init_or_update_default_texts_db_from_json()
        init_update_default_translations_db_from_json()
        init_default_algo_rules_db_from_json()
        init_default_dashboard_db_from_json()
        init_default_patient_css_variables_db_from_json()
        init_default_announce_css_variables_db_from_json()
        init_default_phone_css_variables_db_from_json()

        # Point 11 : garantir l'existence de la ligne de génération de config
        # avant tout démarrage de réplique web (évite une insertion concurrente).
        config_sync.ensure_generation_row()

    if run_runtime:
        load_configuration(app)
        # Point 11 : mémoriser la génération de configuration chargée pour que la
        # première requête ne déclenche pas un rechargement inutile, et que les
        # comparaisons ultérieures (before_request web / tâches scheduler)
        # détectent uniquement de vrais changements venus d'un autre processus.
        config_sync.mark_current_generation(app)

    if run_startup_cleanup:
        clear_old_patients_table(app)
        clear_counter_table()

    if run_runtime:
        # Pour gérer les app.config des CSS. A faire également pour mon Config général
        css_variable_manager = MultiCssVariableManager(app)
        app.css_manager = CSSManager()
        app.css_manager.init_app(app)


def create_app(config_class=Config):
    app = Flask(__name__)

    # Charger la configuration avant toute initialisation
    AppHolder.set_app(app)
    app.config.from_object(config_class)
    app.config["DATABASE_TYPE"] = os.getenv("DATABASE_TYPE", getattr(config_class, "database", "mysql"))
    app.debug = bool(app.config.get("DEBUG", False))

    # Initialiser le logging
    logging.basicConfig(level=(logging.DEBUG if app.debug else logging.INFO),
                        format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

    # Refus de démarrage si le secret applicatif n'est pas configuré.
    # Sans APP_SECRET, /api/get_app_token ne peut de toute façon plus émettre de
    # token (check_app_secret refuse un secret serveur vide), donc l'App et
    # l'imprimante ne pourraient pas s'authentifier : autant échouer tôt et
    # clairement. On saute ce contrôle pour les commandes hors-serveur
    # (migrations, CLI, tests) qui posent SKIP_STARTUP_HOOKS, et en debug on se
    # contente d'un avertissement pour ne pas gêner le développement local.
    if not is_valid_app_secret_config(app.config.get("APP_SECRET")):
        message = ("APP_SECRET n'est pas configuré (absent, vide ou placeholder). "
                   "Définissez une valeur forte et unique dans l'environnement "
                   "(variable APP_SECRET) identique au secret saisi côté clients.")
        if SKIP_STARTUP_HOOKS:
            app.logger.warning("%s [ignoré : SKIP_STARTUP_HOOKS]", message)
        elif app.debug:
            app.logger.warning("%s [toléré en debug : aucune émission de token possible]", message)
        else:
            raise RuntimeError(message)

    db.init_app(app)
    migrate.init_app(app, db)

    # --- Protection CSRF ---
    # On protège les requêtes navigateur (voir csrf_protect_browser_requests).
    # WTF_CSRF_CHECK_DEFAULT=False : pas de vérification automatique sur toutes
    # les vues ; on l'applique sélectivement pour ne pas casser les clients
    # machine (jeton applicatif) ni Socket.IO.
    # WTF_CSRF_TIME_LIMIT=None : le jeton reste valide le temps de la session
    # (évite les faux rejets sur les longues sessions d'administration).
    app.config.setdefault("WTF_CSRF_CHECK_DEFAULT", False)
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
    csrf.init_app(app)

    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    security = Security(app, user_datastore, register_blueprint=True)
    #security.init_app(app, user_datastore, register_blueprint=True, name='flask_security')

    # Initialiser le mail avec l'application (instance partagee d'extensions.py).
    # Auparavant : `app.mail = Mail(app)` creait une SECONDE instance, puis la
    # ligne `app.mail = mail` en fin de module l'ecrasait par l'instance module
    # non initialisee. Sans consequence (personne ne lit app.mail : l'envoi passe
    # par flask_mailman.EmailMessage, qui utilise l'extension enregistree sur
    # l'app), mais incoherent.
    mail.init_app(app)


    # Appeler explicitement des fonctions de démarrage dans le contexte de l'application
    with app.app_context():
        if not SKIP_STARTUP_HOOKS:
            start_fonctions(
                app,
                run_bootstrap=_is_app_role("scheduler", "init"),
                run_runtime=_is_app_role("web", "scheduler", "init"),
                run_startup_cleanup=_is_app_role("scheduler", "init"),
            )

    # Enregistrement des blueprints
    app.register_blueprint(home_bp, url_prefix='')
    app.register_blueprint(admin_announce_bp, url_prefix='')
    app.register_blueprint(admin_counter_bp, url_prefix='')
    app.register_blueprint(admin_activity_bp, url_prefix='')
    app.register_blueprint(admin_algo_bp, url_prefix='')
    app.register_blueprint(admin_gallery_bp, url_prefix='')
    app.register_blueprint(admin_phone_bp, url_prefix='')
    app.register_blueprint(admin_staff_bp, url_prefix='')
    app.register_blueprint(admin_patient_bp, url_prefix='')
    app.register_blueprint(admin_queue_bp, url_prefix='')
    app.register_blueprint(admin_translation_bp, url_prefix='')
    app.register_blueprint(admin_options_bp, url_prefix='')
    app.register_blueprint(counter_bp, url_prefix='')
    app.register_blueprint(admin_schedule_bp, url_prefix='')
    app.register_blueprint(admin_security_bp, url_prefix='')
    app.register_blueprint(announce_bp, url_prefix='')
    app.register_blueprint(patient_bp, url_prefix='')
    app.register_blueprint(pyside_bp, url_prefix='')
    app.register_blueprint(admin_music_bp, url_prefix='')
    app.register_blueprint(admin_dashboard_bp, url_prefix='')
    app.register_blueprint(admin_stats_bp, url_prefix='')
    app.register_blueprint(admin_app_bp, url_prefix='')
    app.register_blueprint(admin_data_bp, url_prefix='')
    app.register_blueprint(engine_bp, url_prefix='')
    app.register_blueprint(admin_backup_bp, url_prefix='')
    app.register_blueprint(api_system_bp, url_prefix='')
    app.register_blueprint(calling_bp, url_prefix='')
    app.register_blueprint(admin_config_bp, url_prefix='')

    # Temps reel et ordonnanceur : crees a vide dans extensions.py, lies ici.
    # Auparavant ils etaient instancies au niveau module APRES create_app(), ce
    # qui obligeait tout le reste du code a passer par des attributs greffes sur
    # l'objet app (app.socketio, app.scheduler).
    init_socketio(app)
    configure_scheduler(app)

    return app

load_dotenv()
app = create_app(config_class=Config)

# Etat des connexions temps reel : vit desormais dans sockets.py. Reexpose sur
# l'objet app car les pages d'administration le consultent via current_app.
app.active_connections = sockets.active_connections
app.connected_clients_info = sockets.connected_clients_info


@app.errorhandler(404)
def page_not_found(e):
    if has_request_context():
        app.logger.info("404 Not Found: %s %s", request.method, request.path)
        safe_path = escape(request.path)
        safe_method = escape(request.method)
        details = f"<p>Request: {safe_method} {safe_path}</p>"
    else:
        details = ""

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>404 Not Found</title>
</head>
<body>
  <h1>404 Not Found</h1>
  <p>The requested URL was not found on the server.</p>
  {details}
</body>
</html>"""

    response = make_response(html, 404)
    response.headers["Cache-Control"] = "no-store"
    return response


# Configuration de la base de données avec session scoped
"""engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                        autoflush=False,
                                        bind=engine))
"""

# ---------------------------------------------------------------------------
# Endpoints de santé (Health checks)
# ---------------------------------------------------------------------------
#
# Ces endpoints sont destinés aux orchestrateurs (Kubernetes, Coolify, Render,
# Docker Compose, etc.) pour piloter le cycle de vie du conteneur.
#
# /healthz  – Liveness probe
#   Répond 200 tant que le processus Python/Flask est vivant.
#   Si cet endpoint ne répond plus, l'orchestrateur doit redémarrer le
#   conteneur.  Aucune dépendance externe n'est vérifiée ici volontairement :
#   un problème de base de données ne doit pas provoquer un redémarrage en
#   boucle.
#
# /readyz   – Readiness probe
#   Répond 200 uniquement si l'application est prête à traiter du trafic :
#     • La base de données est joignable (SELECT 1).
#     • RabbitMQ est joignable (si activé dans la configuration).
#   Tant que /readyz renvoie 503, l'orchestrateur ne route pas de trafic
#   vers cette instance, ce qui évite les erreurs utilisateur pendant un
#   démarrage lent ou une panne transitoire d'un service amont.
#
# Configuration Coolify / Docker Compose :
#   healthcheck:
#     test: ["CMD", "curl", "-f", "http://localhost:${PORT:-5000}/healthz"]
#     interval: 10s
#     timeout: 5s
#     retries: 3
#     start_period: 30s
# ---------------------------------------------------------------------------

# ROUTES 

# Sauvegardes / Restaurations (base de données brute uniquement, le reste est dans admin_backup_bp)
#
# Point 1 (audit Admin) : ces deux routes ne portaient AUCUNE garde de permission
# au-delà de l'authentification globale (/admin). Un utilisateur simplement
# connecté — sans le rôle ni la permission 'app' — pouvait télécharger l'intégralité
# de la base (backup) ou l'écraser (restore). Elles sont désormais protégées par
# require_permission('app'), la même permission que le nouveau système de
# sauvegardes (admin_backup_bp), pour une politique cohérente.

app.add_url_rule('/admin/database/backup', 'backup_databases',
                require_permission('app')(partial(backup_databases, database)),
                methods=['GET'])

app.add_url_rule('/admin/database/restore', 'restore_databases',
                require_permission('app')(partial(restore_databases, request, database)),
                methods=['GET', 'POST'])



#user_datastore = SQLAlchemyUserDatastore(db, User, None)
#security = Security(app, user_datastore, login_form=ExtendedLoginForm)


def get_locale():
    return session.get('lang', request.accept_languages.best_match(['en', 'fr']))
#babel.init_app(app, locale_selector=get_locale)


@app.before_request
def set_locale():
    from flask import request
    user_language = request.cookies.get('lang', 'fr')  # Exemple: lire la langue depuis un cookie
    #request.babel_locale = user_language


# permet d'avoir le contexte de l'App pour le Scheduler. A utiliser comme décorateur


# -------------- SECURITé ---------------------

def _deny_unauthenticated_access():
    """Refus d'accès faute de session authentifiée, sous la forme adaptée au
    client : **401 JSON** pour un appel AJAX/HTMX (que le JS peut traiter),
    **redirection** vers la page de connexion pour une navigation navigateur."""
    if wants_json_response(request):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for('admin_security.login', next=request.url))


@app.before_request
def require_login_for_admin():
    """Applique les règles d'authentification par zone de l'application.

    L'administration (``/admin``) exige **toujours** une session authentifiée
    (point 1.2). Le paramètre ``SECURITY_LOGIN_ADMIN`` est **déprécié** : il n'est
    plus consulté ici et ne peut donc plus rendre l'administration anonyme. Les
    autres zones (comptoir, écran, patient, app) restent gouvernées par leurs
    paramètres respectifs, inchangés.
    """

    app_token = request.headers.get('X-App-Token')
    is_valid_app_request = app_token and verify_app_token(app_token)

    if request.path.startswith('/admin'):
        # Authentification OBLIGATOIRE et inconditionnelle pour toute l'admin.
        if not current_user.is_authenticated:
            return _deny_unauthenticated_access()
    elif request.path.startswith('/spotify'):
        # Point 1.4 : les routes /spotify/* (lecteur + OAuth) sont hors du préfixe
        # /admin. Elles exigent chacune une permission via @require_permission,
        # mais on ne veut PAS que leur sécurité repose sur la seule présence de ce
        # décorateur. On impose donc ici, structurellement, une session
        # authentifiée pour tout le préfixe /spotify (défense en profondeur : une
        # future route /spotify oubliée ne sera jamais anonyme). La permission
        # fine — music_play pour les commandes de lecture, music_options pour
        # l'OAuth/config — reste appliquée par le décorateur de chaque route.
        if not current_user.is_authenticated:
            return _deny_unauthenticated_access()
    elif request.path.startswith('/counter'):
        if app.config["SECURITY_LOGIN_COUNTER"] and not current_user.is_authenticated:
            return redirect(url_for('admin_security.login', next=request.url))
    elif request.path.startswith('/display'):
        if app.config["SECURITY_LOGIN_SCREEN"] and not current_user.is_authenticated:
            return redirect(url_for('admin_security.login', next=request.url))
    # on mets en code sur les pages patients, mais pas patient/phone
    elif request.path.startswith('/patient') and not request.path.startswith('/patient/phone'):
        if app.config["SECURITY_LOGIN_PATIENT"] and not current_user.is_authenticated:
            return redirect(url_for('admin_security.login', next=request.url))
    elif request.path.startswith('/app'):
        if app.config["SECURITY_LOGIN_COUNTER"] and not (current_user.is_authenticated or is_valid_app_request):
            return jsonify({"error": "Unauthorized"}), 401


# Préfixes de chemins exemptés de CSRF : transport Socket.IO et endpoints
# machine/kiosque (App_Comptoir, borne, imprimante). Ces clients ne sont pas des
# navigateurs porteurs de session : ils s'authentifient par jeton applicatif
# (X-App-Token) ou sont des bornes publiques. Documenté dans docs/SECURITY.md.
_CSRF_EXEMPT_PREFIXES = (
    "/socket.io",   # transport Socket.IO (polling POST)
    "/api/",        # API machine (jeton) : get_app_token, counter, printer...
    "/app/",        # routes App_Comptoir (jeton)
    "/patient",     # borne/kiosque patient + /patients_submit + /patient/phone
)

# Point 2.2 : routes « à double usage » (appelées à la fois par le navigateur ET
# par un client machine) situées HORS des préfixes ci-dessus. Seules ces routes
# peuvent être exemptées par un jeton applicatif VALIDE. L'administration
# (/admin) et Spotify (/spotify) n'en font JAMAIS partie : un simple en-tête ne
# doit jamais suffire à contourner le CSRF sur une route sensible.
# Aujourd'hui la seule route concernée est /validate_and_call_next/<counter_id>,
# partagée par le comptoir navigateur et App_Comptoir.
_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES = (
    "/validate_and_call_next",
)


def _csrf_is_exempt():
    """Vrai si la requête courante ne doit PAS être soumise au contrôle CSRF."""
    path = request.path
    if path.startswith(_CSRF_EXEMPT_PREFIXES):
        return True
    # Requêtes des applications clientes (App_Comptoir) sur une route à double
    # usage. L'exemption exige les TROIS conditions (point 2.2) :
    #   1. un jeton applicatif présent ;
    #   2. un jeton réellement VALIDE (vérifié ici, plus seulement présent) ;
    #   3. une route explicitement prévue pour les clients machine (allowlist).
    # Ainsi, un formulaire cross-site avec un en-tête bidon — ou pointant sur
    # /admin — n'échappe jamais au contrôle CSRF.
    token = request.headers.get("X-App-Token")
    if token and verify_app_token(token) and path.startswith(_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES):
        return True
    return False


@app.before_request
def csrf_protect_browser_requests():
    """Applique la vérification CSRF aux seules requêtes navigateur mutatrices.

    Les requêtes GET/HEAD/OPTIONS et les endpoints machine/kiosque exemptés ne
    sont pas contrôlés ; tout le reste (formulaires HTMX et fetch d'admin, pages
    comptoir navigateur) doit présenter un jeton CSRF valide, sous peine de 400.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if _csrf_is_exempt():
        return
    csrf.protect()


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    """Refus explicite d'une requête sans jeton CSRF valide (400)."""
    app.logger.warning("CSRF refusé sur %s %s : %s", request.method, request.path, error.description)
    return jsonify({"error": "CSRF validation failed", "reason": error.description}), 400



    #return validate_and_transform_text_for_before_validation(value)

    #return validate_and_transform_text_for_after_validation(value)


# --------  ADMIN -> DataBase  ---------

# ---------------- PAGE PATIENT FRONT ----------------

# liste des patients en attente : Nécessaire pour être transmis à Pyside
def list_patients_standing():
    patients_standing = Patient.query.filter_by(status='standing').all()
    patients_data = [patient.to_dict() for patient in patients_standing]
    return patients_data


# ---------------- FIN  PAGE PATIENTS FRONT ----------------


# ---------------- PAGE COUNTER FRONT ----------------

# si le comptoir n'existe pas -> page avec liste des comptoirs
# ---------------- FIN  PAGE COUNTER FRONT ----------------


# ---------------- FONCTIONS Généralistes / COmmunication ---------------- 

# NOTE: ~100 lignes de plomberie SSE ont ete retirees ici (point 9.5d) : neuf
# listes de clients au niveau module, add_client/remove_client, event_stream,
# event_stream_dict, et neuf routes /events/* enfermees dans une chaine de
# caracteres (donc JAMAIS enregistrees). Le temps reel passe entierement par
# Socket.IO. Les trois EventSource clients qui pointaient encore vers ces routes
# inexistantes -- et bouclaient donc sur des 404 -- ont ete retires de
# static/js/counter.js et static/js/announce.js.


@app.after_request
def _attach_admin_feedback(response):
    """Point 7.5 — Acquittement HTTP des actions admin.

    Si un ``display_toast`` a été émis pendant la requête, on ajoute son
    résultat (succès + message) dans l'en-tête ``HX-Trigger`` de la réponse.
    Le client (AdminFeedback) écoute l'évènement ``adminFeedback`` et l'annonce
    dans une zone ``aria-live``. La sauvegarde est ainsi confirmée par la
    réponse HTTP, indépendamment de la diffusion WebSocket.
    """
    feedback = g.pop('_admin_feedback', None) if has_request_context() else None
    if feedback is None:
        return response

    trigger = {'adminFeedback': feedback}
    existing = response.headers.get('HX-Trigger')
    if existing:
        # Fusionne sans écraser un HX-Trigger déjà présent.
        try:
            merged = json.loads(existing)
            if not isinstance(merged, dict):
                raise ValueError
        except (ValueError, TypeError):
            merged = {existing: {}}
        merged.update(trigger)
        trigger = merged
    response.headers['HX-Trigger'] = json.dumps(trigger)
    return response


# ---------------- FONCTIONS Généralistes > Communication avec Pyside ---------------- 

# ---------------- FONCTIONS Généralistes > Affichage page sur téléphone ---------------- 


def start_serveo():
    port = 80
    if is_port_open('localhost', port):
        app.logger.info(f"Port {port} is open. Trying with port 8080.")
        port = 8080
    
    command = ["ssh", "-i", os.path.expanduser("~/.ssh/id_rsa"), "-R", f"pharmaciesainteagathe:{port}:localhost:{server_port}", "serveo.net"]
    subprocess.run(command)
    app.logger.info(f"Serveo tunnel started on port {port}")

def is_port_open(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((host, port))
        s.shutdown(socket.SHUT_RDWR)
        return True
    except:
        return False
    finally:
        s.close()

# Démarrer LocalTunnel lorsque Flask démarre
def start_serveo_tunnel_in_thread():
    app.logger.info("start serveo tunnel in thread")
    serveo_thread = threading.Thread(target=start_serveo)
    serveo_thread.start()

"""
@app.before_request
def log_request_info():
    app.logger.debug('Headers: %s', request.headers)
    app.logger.debug('Body: %s', request.get_data())
    app.logger.debug('Full Path: %s', request.full_path)
    app.logger.debug('URL: %s', request.url)
"""
# ---------------- FIN FONCTIONS Généralistes ---------------- 

# Définir un filtre pour Jinja2
@app.template_filter('format_time')
def format_time(value):
    return value.strftime('%H:%M') if value else ''


# Chargement des couleurs pour pouvoir les passer dans la session pour être envoyé à base.html
def load_colors(sender, **extra):
    # Cette fonction sera appelée avant chaque requête
    if 'admin_colors' not in session or session['admin_colors'] != app.config['ADMIN_COLORS']:
        try:
            session['admin_colors'] = app.config['ADMIN_COLORS']
        except KeyError:
            session['admin_colors'] = "lumen"
# Connecter le signal request_started à la fonction load_configuration
request_started.connect(load_colors, app)


@app.before_request
def _sync_configuration_across_processes():
    """Point 11 — convergence des paramètres entre répliques web.

    Vérification *throttlée* (au plus une lecture mono-ligne de la génération de
    configuration toutes les ``CONFIG_SYNC_MIN_INTERVAL`` secondes) : si un autre
    processus (autre réplique web, page d'admin) a modifié la configuration, on
    recharge ``app.config`` depuis la base avant de traiter la requête. En cas
    d'erreur base, la requête n'échoue pas : la configuration en mémoire est
    conservée (cf. config_sync.maybe_reload_configuration).
    """
    config_sync.maybe_reload_configuration(app)


# Fonctions attachées à app afin de pouvoir les appeler depuis un autre fichier via current_app
# NOTE: seule `load_configuration` reste greffee sur l'objet application. Elle
# est appelee depuis init_restore.py et routes/admin_backup.py -- deux modules
# qu'app.py importe lui-meme : un import direct creerait un cycle. Les autres
# greffes ont disparu (socketio et scheduler viennent d'extensions.py,
# active_connections/connected_clients_info de sockets.py, display_toast et
# allowed_image_file de ui_feedback.py, l'appel patient de services/).
app.load_configuration = load_configuration

if __name__ == "__main__":

    app.logger.info(f"Starting with APP_ROLE={APP_ROLE}")

    if APP_ROLE == "init":
        app.logger.info("Initialization role completed. Exiting process.")
    else:
        if APP_ROLE == "scheduler":
            start_scheduler(active=True)
            app.logger.info("Scheduler started in active mode (APP_ROLE=scheduler)")
            # Ce process ne sert aucune connexion WebSocket (pas de socketio.run()),
            # mais s'il partage un message_queue (START_RABBITMQ) avec les process
            # "web", les communikation()/socketio.emit() appelés depuis les tâches
            # planifiées (ex: scheduler_functions.py) sont bien relayés à leurs
            # clients. Sans message_queue configuré, ces appels sont des no-op ici,
            # ce qui reste acceptable : le scheduler ne fait que des tâches de fond.
            try:
                while True:
                    tm.sleep(60)
            except KeyboardInterrupt:
                app.logger.info("Scheduler process interrupted, shutting down.")
        else:
            if APP_ROLE == "web":
                start_scheduler(active=False)
                app.logger.info("Scheduler started in paused mode (APP_ROLE=web)")
            else:
                start_scheduler(active=True)
                app.logger.info("Scheduler started in active mode (APP_ROLE=all)")

            #eventlet.wsgi.server(eventlet.listen(('0.0.0.0', server_port)), app)
            socketio.run(app, host='0.0.0.0', port=server_port, debug=app.debug)

# Contexte processeur pour rendre current_user disponible dans tous les templates (menu de page base.html)
@app.context_processor
def inject_user():
    return dict(current_user=current_user)

app.logger.debug("Starting Flask...")
app.logger.info(f"Starting Flask on port {server_port} with debug={app.debug}")

#app.run(host='0.0.0.0', port=server_port, debug=app.debug, threaded=True)

app.logger.info("Starting Flask app...")
