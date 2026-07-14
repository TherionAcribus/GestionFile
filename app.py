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
from flask import Flask, render_template, request, redirect, url_for, session, current_app, jsonify, send_from_directory, Response, g, make_response, request, has_request_context, flash, session

from sqlalchemy.orm import sessionmaker, relationship, backref, session as orm_session, exc as sqlalchemy_exceptions, joinedload
from sqlalchemy import func, CheckConstraint, and_, Boolean, DateTime, Column, Integer, String, ForeignKey
from flask_migrate import Migrate
from flask.signals import request_started
from flask_mailman import Mail
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_socketio import SocketIO, join_room, leave_room
from datetime import datetime, time, timedelta
import time as tm

from flask_apscheduler import APScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

import json

from queue import Queue, Empty
import logging
import subprocess
import threading
import socket
import pika
import threading
import pytz

from urllib.parse import urlparse, urljoin
import random

from functools import partial

from flask_debugtoolbar import DebugToolbarExtension
from flask_security import Security, current_user, auth_required, hash_password, \
    SQLAlchemySessionUserDatastore, permissions_accepted, UserMixin, RoleMixin, AsaList, SQLAlchemyUserDatastore, login_required, lookup_identity, uia_username_mapper, verify_and_update_password, login_user
from sqlalchemy.ext.declarative import declarative_base
from flask_security.forms import LoginForm, BooleanField
from wtforms import StringField, PasswordField, HiddenField
from wtforms.validators import DataRequired

import jwt
from dotenv import load_dotenv
from markupsafe import escape

from auth_utils import require_app_token_or_login, check_app_secret, is_valid_app_secret_config, is_socket_connection_authorized, is_authenticated_request, wants_json_response
from idempotency import idempotent

from models import db, Patient, Counter, Pharmacist, Activity, Button, Language, Text, AlgoRule, ActivitySchedule, ConfigOption, ConfigVersion, User, Role, Weekday, TextTranslation, activity_schedule_link, Translation, JobExecutionLog, DashboardCard
from init_restore import init_default_buttons_db_from_json, init_default_options_db_from_json, init_default_languages_db_from_json, init_or_update_default_texts_db_from_json, init_update_default_translations_db_from_json, init_default_algo_rules_db_from_json, init_days_of_week_db_from_json, init_activity_schedules_db_from_json, clear_counter_table, init_staff_data_from_json, init_counters_data_from_json, init_default_activities_db_from_json, restore_databases, init_default_dashboard_db_from_json, init_default_patient_css_variables_db_from_json, init_default_announce_css_variables_db_from_json, init_default_phone_css_variables_db_from_json
from python.engine import call_next, counter_become_inactive, counter_become_active, trigger_async_audio_calling
from utils import validate_and_transform_text, parse_time, convert_markdown_to_escpos, replace_balise_announces, replace_balise_phone, get_buttons_translation, choose_text_translation, get_text_translation
from backup import backup_databases
from routes.admin_backup import admin_backup_bp
from scheduler_functions import enable_buttons_for_activity, disable_buttons_for_activity, add_scheduler_clear_all_patients, clear_old_patients_table, remove_scheduler_clear_all_patients, remove_scheduler_clear_announce_calls, scheduler_clear_announce_calls
from bdd import init_database
from config import Config, time_tz
from communication import send_app_notification, communikation
from variables import MultiCssVariableManager
from css_manager import CSSManager

from app_holder import AppHolder

from routes.counter import counter_bp, update_switch_auto_calling
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
from routes.admin_security import admin_security_bp, ExtendedLoginForm, create_default_user, create_default_role
from routes.admin_music import admin_music_bp, is_spotipy_connected
from routes.admin_dashboard import admin_dashboard_bp
from routes.admin_app import admin_app_bp
from routes.admin_data import admin_data_bp
from routes.announce import announce_bp
from routes.admin_stats import admin_stats_bp
from routes.patient import patient_bp
from routes.pyside import pyside_bp, create_patients_list_for_pyside
from routes.home import home_bp
from python.engine import engine_bp
from routes.admin_security import require_permission, require_permission_dashboard, user_has_permission
from params_registry import CONFIG_MAPPINGS, BALISE_LETTERS, get_spec
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

mail = Mail()
migrate = Migrate()
# Protection CSRF (Flask-WTF). Initialisée dans create_app. On désactive la
# vérification automatique globale (WTF_CSRF_CHECK_DEFAULT=False) et on décide
# nous-mêmes, requête par requête, ce qui doit être protégé : les requêtes
# navigateur (session + cookie) le sont, les requêtes machine (App_Comptoir,
# borne, imprimante — authentifiées par jeton applicatif) et le transport
# Socket.IO en sont exemptés. Voir csrf_protect_browser_requests() et
# _csrf_is_exempt().
csrf = CSRFProtect()


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
    print("VOICE_MODEL", app.config["VOICE_MODEL"])

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

    # auto_calling 
    auto_calling = []
    for counter in Counter.query.all():
        if counter.auto_calling:
            auto_calling.append(counter.id)
    app.config["AUTO_CALLING"] = auto_calling

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

    # Initialiser le mail avec l'application
    app.mail = Mail(app)


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

    return app

load_dotenv()
app = create_app(config_class=Config)

_socketio_kwargs = {"async_mode": "eventlet"}
if app.config.get("SOCKETIO_CORS_ALLOWED_ORIGINS") is not None:
    _socketio_kwargs["cors_allowed_origins"] = app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"]

# Message queue optionnel (RabbitMQ). Sans lui, chaque processus ne diffuse
# qu'à ses propres clients connectés -- ce qui est le comportement historique
# et reste parfaitement valide pour un déploiement mono-processus (aucune
# infra supplémentaire requise). Avec lui, SocketIO relaie automatiquement les
# messages entre tous les processus qui partagent le même message_queue,
# y compris depuis un processus qui ne sert aucune connexion lui-même
# (ex: le conteneur APP_ROLE=scheduler, cf. scheduler_functions.py).
# Activé via le switch admin "Démarrer le serveur avec RabbitMQ" (nécessite
# un redémarrage du process pour prendre effet).
if app.config.get("START_RABBITMQ") and app.config.get("RABBITMQ_URL"):
    _socketio_kwargs["message_queue"] = app.config["RABBITMQ_URL"]

socketio = SocketIO(app, **_socketio_kwargs)

# Définir le jobstore avec votre base de données
jobstores = {
    'default': SQLAlchemyJobStore(url=app.config['SQLALCHEMY_DATABASE_URI_SCHEDULER'])
}
scheduler = BackgroundScheduler(jobstores=jobstores)
#scheduler.init_app(app)


def start_scheduler(active: bool):
    if scheduler.running:
        return
    scheduler.start(paused=not active)

# Dictionnaire pour suivre les connexions actives
active_connections = {
    '/socket_update_patient': set(),
    '/socket_update_screen': set(),
    '/socket_admin': set(),
    '/socket_patient': set(),
    '/socket_app_counter': set(),
    '/socket_app_screen': set(),
    '/socket_counter': set(),
    '/socket_phone': set(),
}

app.active_connections = active_connections

app.connected_clients_info = {}

def get_and_register_socketio_username(request):
    """ Les usernames sont transmis via le header dans Pyside et via la querystring dans JS. 
    Ceci pour des raisons de simplicité côté client.
    """

    # Essayer d'abord de récupérer le username des headers (cas PySide)
    username = request.headers.get('username')

    # Si le username n'est pas dans les headers, chercher dans la query string (cas JavaScript)
    if not username:
        username = request.args.get('username', 'Unknown')

    # Enregistrer le client avec son SID dans le dictionnaire
    app.connected_clients_info[request.sid] = {
        'username': username
    }

    return username


def _socket_require(flag_name: str, namespace: str) -> bool:
    allowed = is_socket_connection_authorized(app.config.get(flag_name, False))
    if not allowed:
        app.logger.warning("Unauthorized Socket.IO connect to %s (missing login/token).", namespace)
    return allowed


@socketio.on('connect', namespace='/socket_update_patient')
def connect_general():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to update patient namespace")

@socketio.on('disconnect', namespace='/socket_update_patient')
def disconnect_general():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from general namespace")

@socketio.on('connect', namespace='/socket_update_screen')
def connect_screen():
    if not _socket_require("SECURITY_LOGIN_SCREEN", "/socket_update_screen"):
        return False
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_update_screen')
def disconnect_screen():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_admin', )
def connect_admin():
    # Admin : authentification TOUJOURS requise (point 1.2). SECURITY_LOGIN_ADMIN
    # est déprécié et n'est plus consulté pour le namespace d'administration :
    # il ne peut plus rendre l'admin anonyme.
    if not is_authenticated_request():
        app.logger.warning("Unauthorized Socket.IO connect to /socket_admin (missing login).")
        return False
    username = get_and_register_socketio_username(request)
    logging.info(f"Client connected to admin namespace with SID {request.sid} and username {username}")
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_admin')
def disconnect_admin():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_patient')
def connect_patient():
    if not _socket_require("SECURITY_LOGIN_PATIENT", "/socket_patient"):
        return False
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to update patient namespace")

@socketio.on('disconnect', namespace='/socket_patient')
def disconnect_patient():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from patient namespace")

@socketio.on('connect', namespace='/socket_app_counter')
def connect_app_counter():
    if not _socket_require("SECURITY_LOGIN_COUNTER", "/socket_app_counter"):
        return False
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to app counter namespace")

@socketio.on('disconnect', namespace='/socket_app_counter')
def disconnect_app_counter():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from app counter namespace")

@socketio.on('connect', namespace='/socket_app_screen')
def connect_app_screen():
    if not _socket_require("SECURITY_LOGIN_SCREEN", "/socket_app_screen"):
        return False
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to test namespace")

@socketio.on('disconnect', namespace='/socket_app_screen')
def disconnect_app_screen():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from test namespace")

@socketio.on('connect', namespace='/socket_counter')
def connect_counter():
    if not _socket_require("SECURITY_LOGIN_COUNTER", "/socket_counter"):
        return False
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to counter namespace")

@socketio.on('disconnect', namespace='/socket_counter')
def disconnect_counter():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from counter namespace")

@socketio.on('connect', namespace='/socket_phone')
def connect_phone():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to phone namespace")
    # Récupérer le patient_id depuis le cookie
    patient_id = request.cookies.get('patient_id')
    call_number = request.cookies.get('patient_call_number')
    
    if patient_id and call_number:
        # Créer une salle unique pour ce patient
        call_room = f"call_{call_number}"
        
        # Rejoindre les deux salles
        join_room(call_room)
        
        app.logger.debug(f"Patient {patient_id} (call number {call_number}) connected and joined rooms")


@socketio.on('disconnect', namespace='/socket_phone')
def disconnect_phone():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from phone namespace")
    patient_id = request.cookies.get('patient_id')
    call_number = request.cookies.get('patient_call_number')
    
    if patient_id and call_number:
        leave_room(f"call_{call_number}")

@app.route('/send_message', methods=['POST'])
@require_app_token_or_login
def send_message():
    message = request.json.get('message', 'Hello from server')
    try:
        socketio.emit('new_message', {'data': message})
        return "Message sent!"
    except Exception as e:
        return f"Failed to send message: {e}", 500


#socketio.run(app, host='0.0.0.0', port=5001)

#socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60000, ping_interval=30000)

#app.config['DEBUG_TB_PROFILER_ENABLED'] = True  # Activer le profiler
#toolbar = DebugToolbarExtension(app)

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


@app.route('/send')
@require_app_token_or_login
def send_message_old():
    url = app.config.get('RABBITMQ_URL') or os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
    params = pika.URLParameters(url)
    
    app.logger.info(f"Connecting to RabbitMQ at {url}")
    
    # Ajoutez une boucle pour réessayer la connexion à RabbitMQ
    for attempt in range(5):  # Réessayez 5 fois 
        try:
            app.logger.info(f"Attempt {attempt + 1} to connect to RabbitMQ")
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue='hello')
            channel.basic_publish(exchange='', routing_key='hello', body='Hello World!')
            connection.close()
            app.logger.info("Message sent to RabbitMQ")
            return jsonify({"message": "Message sent to RabbitMQ!"})
        except pika.exceptions.AMQPConnectionError as e:
            app.logger.error(f"Connection failed, retrying in 5 seconds... {e}")
            time.sleep(5)  # Attendez 5 secondes avant de réessayer

    app.logger.error("Failed to connect to RabbitMQ after 5 attempts")
    return jsonify({"message": "Failed to connect to RabbitMQ"}), 500


@app.route('/test')
@require_app_token_or_login
def rabbitmq_status():
    url = app.config.get('RABBITMQ_URL') or os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
    params = pika.URLParameters(url)
    
    try:
        connection = pika.BlockingConnection(params)
        connection.close()
        return jsonify({"status": "RabbitMQ is running"})
    except Exception as e:
        return jsonify({"status": "RabbitMQ is not running", "error": str(e)}), 500


# Configuration de la base de données avec session scoped
"""engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                        autoflush=False,
                                        bind=engine))
"""

@app.route('/test_local')
@require_app_token_or_login
def rabbitmq_status_local():
    rabbitmq_url = app.config.get('RABBITMQ_URL') or os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
    params = pika.URLParameters(rabbitmq_url)

    try:
        connection = pika.BlockingConnection(params)
        print("Successfully connected to RabbitMQ (local)")
        connection.close()
    except Exception as e:
        print(f"Failed to connect to RabbitMQ (local): {e}")
    return "", 204


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

@app.route('/healthz')
def healthz():
    """Liveness probe – indique que le processus Flask est en vie.

    Renvoie toujours HTTP 200 avec ``{"status": "alive"}``.
    Utilisé par les orchestrateurs (Kubernetes, Coolify, Docker…) pour
    détecter un processus bloqué et le redémarrer automatiquement.

    Aucune dépendance externe (DB, RabbitMQ) n'est testée ici afin
    d'éviter les redémarrages en cascade lors d'une panne transitoire
    d'un service amont.

    Returns:
        tuple: (JSON body, HTTP 200)
    """
    return jsonify({"status": "alive"}), 200


@app.route('/readyz')
def readyz():
    """Readiness probe – indique que l'application est prête à recevoir du trafic.

    Vérifie les dépendances critiques avant de répondre 200 :
      1. **Base de données** : exécute un ``SELECT 1`` pour confirmer que la
         connexion SQL est opérationnelle.
      2. **RabbitMQ** *(optionnel)* : si ``START_RABBITMQ`` est activé, ouvre
         puis ferme une connexion AMQP pour valider la joignabilité du broker.

    Si l'une des vérifications échoue, l'endpoint renvoie HTTP 503 avec le
    détail des checks en erreur.  L'orchestrateur cessera alors de router
    du trafic vers cette instance jusqu'à ce qu'elle redevienne saine.

    Returns:
        tuple: (JSON body, HTTP 200 | 503)

    Exemple de réponse OK (200)::

        {
            "status": "ready",
            "checks": {
                "database": "ok",
                "rabbitmq": "ok"
            }
        }

    Exemple de réponse KO (503)::

        {
            "status": "not_ready",
            "checks": {
                "database": "ok",
                "rabbitmq": "Connection refused"
            }
        }
    """
    checks = {}
    ready = True

    # --- Check base de données ---
    try:
        db.session.execute(db.text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = str(e)
        ready = False

    # --- Check RabbitMQ (seulement si activé) ---
    if app.config.get("START_RABBITMQ"):
        try:
            rabbitmq_url = app.config.get('RABBITMQ_URL') or os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
            connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
            connection.close()
            checks["rabbitmq"] = "ok"
        except Exception as e:
            checks["rabbitmq"] = str(e)
            ready = False

    status_code = 200 if ready else 503
    return jsonify({
        "status": "ready" if ready else "not_ready",
        "checks": checks
    }), status_code


# ROUTES 

# Sauvegardes / Restaurations (base de données brute uniquement, le reste est dans admin_backup_bp)

app.add_url_rule('/admin/database/backup', 'backup_databases', 
                partial(backup_databases, database), 
                methods=['GET'])

app.add_url_rule('/admin/database/restore', 'restore_databases', 
                partial(restore_databases, request, database), 
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
def with_app_context(func):
    def wrapper(*args, **kwargs):
        with app.app_context():
            return func(*args, **kwargs)
    return wrapper




# -------------- SECURITé ---------------------

def generate_app_token():
    expiration = datetime.utcnow() + timedelta(days=1)  # Le token expire après 1 jour
    return jwt.encode({"exp": expiration}, app.config["SECRET_KEY"], algorithm="HS256")

def verify_app_token(token):
    try:
        jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False

@app.route('/api/get_app_token', methods=['POST'])
def get_app_token():
    # check_app_secret compare en temps constant le secret fourni au secret
    # configuré (app.config["APP_SECRET"]) et refuse TOUJOURS si le secret serveur
    # n'est pas réellement configuré (absent/vide/placeholder). Cela ferme la
    # faille où un APP_SECRET absent (chaîne vide) acceptait un secret vide.
    if check_app_secret(request.form.get('app_secret'), app.config.get("APP_SECRET")):
        token = generate_app_token()
        return jsonify({"token": token})
    else:
        return jsonify({"error": "Unauthorized"}), 401

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


def _csrf_is_exempt():
    """Vrai si la requête courante ne doit PAS être soumise au contrôle CSRF."""
    path = request.path
    if path.startswith(_CSRF_EXEMPT_PREFIXES):
        return True
    # Requêtes des applications clientes (App_Comptoir, borne, imprimante) :
    # présence d'un en-tête personnalisé X-App-Token. Un formulaire cross-site ne
    # peut pas positionner d'en-tête personnalisé (protection intrinsèque), et le
    # jeton est de toute façon revérifié par la route (@require_app_token_or_login).
    if request.headers.get("X-App-Token"):
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



def authorize_config_change(key, expected_value_type=None):
    """Contrôle d'accès commun aux routes de modification des paramètres.

    Retourne ``(spec, None)`` si la modification est autorisée, sinon
    ``(None, (réponse, statut))`` à renvoyer tel quel :

    - **401** si l'utilisateur n'est pas authentifié ;
    - **400** si la clé est absente du registre (``PARAM_REGISTRY``) ou d'un
      type incompatible avec la route appelée ;
    - **403** si l'utilisateur n'a pas la permission associée à la clé.

    Aucune confiance n'est accordée aux données du navigateur : la permission
    et le type proviennent exclusivement du registre serveur.
    """
    if not current_user.is_authenticated:
        app.logger.warning("Modification de paramètre refusée (non authentifié) : %r", key)
        return None, (jsonify({"error": "Unauthorized"}), 401)

    spec = get_spec(key)
    if spec is None:
        app.logger.warning("Modification de paramètre refusée (clé inconnue) : %r", key)
        return None, (jsonify({"error": "Unknown parameter"}), 400)

    if expected_value_type is not None and spec.value_type != expected_value_type:
        app.logger.warning(
            "Modification de paramètre refusée (type %s attendu pour %s, registre=%s)",
            expected_value_type, key, spec.value_type)
        return None, (jsonify({"error": "Invalid parameter type"}), 400)

    if not user_has_permission(current_user, spec.permission):
        app.logger.warning(
            "Modification de '%s' refusée à %s (permission '%s' requise)",
            key, getattr(current_user, "username", "?"), spec.permission)
        return None, (jsonify({"error": "Forbidden"}), 403)

    return spec, None


@app.route('/admin/update_switch', methods=['POST'])
def update_switch():
    """ Mise à jour des switches d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key, expected_value_type="value_bool")
    if error:
        return error

    bool_value = value == "true"
    try:
        # Mutation en base dans une seule transaction. On ne touche PAS à
        # app.config ici : la mémoire ne doit refléter le changement qu'APRÈS un
        # commit réussi (point 10), pour ne pas diverger de la base si le commit
        # échoue.
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        if config_option:
            config_option.value_bool = bool_value
        else:
            config_option = ConfigOption(config_key=key, value_bool=bool_value)
            db.session.add(config_option)

        # Point 11 : incrémenter la génération de configuration DANS la même
        # transaction, pour que les autres processus (répliques web, scheduler)
        # rechargent app.config. On ne le fait PAS pour les paramètres nécessitant
        # un redémarrage : ils ne s'appliquent qu'à l'initialisation du processus.
        if not spec.restart_required:
            config_sync.bump_generation()

        db.session.commit()
    except Exception as e:
        # Toute exception annule la transaction (rollback) : la base reste dans
        # son état précédent et app.config n'a pas été modifié.
        db.session.rollback()
        app.logger.error("Échec de mise à jour du switch %r : %s", key, e)
        return display_toast(success=False, message=str(e))

    # Paramètre nécessitant un redémarrage : la valeur est persistée mais n'est
    # PAS appliquée à chaud (ni ici ni sur les autres processus). On ne mute donc
    # pas app.config et on l'annonce clairement plutôt que de prétendre l'inverse.
    if spec.restart_required:
        return display_toast(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    # Commit réussi : refléter en mémoire, puis déclencher les effets de bord.
    app.config[spec.config_name] = bool_value
    call_function_with_switch(key, value)
    return display_toast(success=True, message="Option mise à jour.")
    

@app.route('/admin/update_css_variable_old', methods=['POST'])
def update_css_variable_old():
    print(request.form)
    try:
        # Récupération des données
        source = request.form.get('source')
        variable = request.form.get('variable')
        value = request.form.get('value')

        # Validation des données
        if not all([source, variable, value]):
            return jsonify({
                'status': 'error',
                'message': 'Données manquantes'
            }), 400

        if source not in ['patient', 'announce']:
            return jsonify({
                'status': 'error',
                'message': 'Source invalide'
            }), 400

        # Mise à jour via le gestionnaire
        app.css_variable_manager.update_variable(source, variable, value)

        return jsonify({
            'status': 'success',
            'message': f'Variable {variable} mise à jour pour {source}',
            'data': {
                'source': source,
                'variable': variable,
                'value': value
            }
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/admin/update_css_variable', methods=['POST'])
def update_css_variable():
    print("UPDATE!!!")

    source_name = request.form.get('source')
    variable_name = request.form.get('variable')
    value = request.form.get('value')
    dependencies = json.loads(request.form.get('dependencies', '[]'))
    
    # Met à jour la variable dans la base de données
    app.css_variable_manager.update_variable(source_name, variable_name, value)
    
    # Met à jour toutes les variables dépendantes
    for dep_variable in dependencies:
        app.css_variable_manager.update_variable(
            source_name, 
            dep_variable,  # Maintenant dep_variable est directement le nom de la variable
            value  # On utilise la même valeur que la variable parente
        )

    # Récupère toutes les variables pour générer le CSS
    variables = app.css_variable_manager.get_all_variables(source_name)
    
    # Génère le nouveau CSS
    new_css_url = app.css_manager.generate_css(variables, mode=source_name)
    
    return jsonify({
        'status': 'success',
        'css_url': new_css_url
    })


@app.route('/admin/copy_colors', methods=['POST'])
def copy_colors():
    """Copie les couleurs parentes d'une page source vers une page cible"""
    try:
        data = request.get_json()
        source_page = data.get('source_page')
        target_page = data.get('target_page')
        mappings = data.get('mappings', [])

        if not all([source_page, target_page, mappings]):
            return jsonify({'status': 'error', 'message': 'Données manquantes'}), 400

        # Pour chaque mapping, lire la valeur source et l'écrire dans la cible
        for mapping in mappings:
            source_var = mapping.get('source_var')
            target_var = mapping.get('target_var')
            source_source = mapping.get('source_source')  # ex: 'patient', 'announce', 'phone'
            target_source = mapping.get('target_source')

            value = app.css_variable_manager.get_variable(source_source, source_var)
            if value:
                # Met à jour la variable parente cible
                app.css_variable_manager.update_variable(target_source, target_var, value)

                # Met à jour aussi les variables dépendantes via colorMappings (côté client)
                dep_variables = mapping.get('dependencies', [])
                for dep_var in dep_variables:
                    app.css_variable_manager.update_variable(target_source, dep_var, value)

        # Régénère le CSS pour la/les source(s) cible(s)
        target_sources = set(m.get('target_source') for m in mappings)
        for ts in target_sources:
            variables = app.css_variable_manager.get_all_variables(ts)
            app.css_manager.generate_css(variables, mode=ts)

        return jsonify({'status': 'success', 'message': 'Couleurs copiées avec succès'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/admin/update_input', methods=['POST'])
def update_input():
    """ Mise à jour des input d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key)
    if error:
        return error

    # Le type de validation vient du registre serveur, jamais du paramètre
    # ``check`` envoyé par le navigateur.
    validator = spec.validator
    if value is None:
        value = ""

    # Secrets (mot de passe SMTP, clé Spotify...) : le formulaire n'affiche
    # jamais la valeur courante et envoie un champ vide par défaut. Un envoi vide
    # signifie donc « conserver la valeur actuelle » — on n'efface pas un secret
    # au seul motif que le champ était vide. On ne journalise jamais la valeur.
    if spec.secret and value.strip() == "":
        return config_change_response(success=True, message="Secret inchangé (valeur actuelle conservée).")

    # --- Validation de TOUTES les valeurs AVANT toute mutation (point 10) ---
    if validator == "int":
        if value.isdigit():
            value = int(value)
        else:
            return config_change_response(success=False, message="L'entrée doit être un nombre.")
    elif validator in BALISE_LETTERS:
        text_check = validate_and_transform_text(value, BALISE_LETTERS[validator])
        if text_check["success"]:
            value = text_check["value"]
        else:
            return config_change_response(success=False, message=text_check["value"])

    # Cas particulier des tickets : la version ESC/POS est enregistrée dans la
    # MÊME transaction que l'option principale (plus de commit intermédiaire pour
    # une seule opération logique) et n'est reflétée dans app.config qu'après le
    # commit final.
    is_ticket = key.startswith("ticket_")
    escpos_text = convert_markdown_to_escpos(value) if is_ticket else None
    key_printer = (key + "_printer") if is_ticket else None

    is_int = spec.value_type == "value_int"
    try:
        # MAJ BDD — option principale. La colonne cible vient du registre serveur.
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        if config_option:
            if is_int:
                config_option.value_int = value
            else:
                config_option.value_str = value
        else:
            if is_int:
                config_option = ConfigOption(config_key=key, value_int=value)
            else:
                config_option = ConfigOption(config_key=key, value_str=value)
            db.session.add(config_option)

        # MAJ BDD — version imprimante du ticket (même transaction, un seul commit).
        if is_ticket:
            printer_option = ConfigOption.query.filter_by(config_key=key_printer).first()
            if printer_option:
                printer_option.value_str = escpos_text
            else:
                printer_option = ConfigOption(config_key=key_printer, value_str=escpos_text)
                db.session.add(printer_option)

        # Point 11 : génération incrémentée dans la même transaction pour la
        # convergence inter-processus (sauf paramètre nécessitant un redémarrage).
        if not spec.restart_required:
            config_sync.bump_generation()

        db.session.commit()
    except Exception as e:
        # Toute exception annule l'ensemble de la transaction : ni l'option ni la
        # version imprimante ne sont modifiées, et app.config reste intact.
        db.session.rollback()
        # Pour une clé secrète, ne jamais renvoyer/journaliser le détail technique
        # (il pourrait, selon le backend, contenir la valeur).
        if spec.secret:
            app.logger.error("Échec de mise à jour du paramètre secret %r", key)
            return config_change_response(success=False, message="La mise à jour du secret a échoué.")
        app.logger.error("Échec de mise à jour de l'option %r : %s", key, e)
        return config_change_response(success=False, message="La mise à jour a échoué.")

    # Paramètre nécessitant un redémarrage : persisté mais non appliqué à chaud.
    if spec.restart_required:
        return config_change_response(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    # Commit réussi : refléter en mémoire (app.config) puis effets de bord.
    app.config[spec.config_name] = value
    if is_ticket:
        app.config[key_printer.upper()] = escpos_text
    special_functions_with_input(key)
    # Réponse directe à l'auteur de la requête (pas de diffusion WebSocket à
    # tous les administrateurs pour une sauvegarde de champ individuelle).
    return config_change_response(success=True, message="Option mise à jour.")


def special_functions_with_input(key):
    if key == "cron_delete_patient_table_hour":
        remove_scheduler_clear_all_patients()
        add_scheduler_clear_all_patients()
        communikation("admin", event="refresh_schedule_tasks_list")
    if key == "cron_delete_announce_calls_hour":
        remove_scheduler_clear_announce_calls()
        scheduler_clear_announce_calls()
        communikation("admin", event="refresh_schedule_tasks_list")


@app.route('/admin/update_select', methods=['POST'])
def update_select():
    """ Mise à jour des selects d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key, expected_value_type="value_str")
    if error:
        return error

    # Validation de l'existence de l'option AVANT toute mutation.
    config_option = ConfigOption.query.filter_by(config_key=key).first()
    if not config_option:
        return display_toast(success=False, message="Option non trouvée.")

    try:
        # Mutation en base dans une transaction ; app.config n'est mis à jour
        # qu'APRÈS un commit réussi (point 10).
        config_option.value_str = value
        # Point 11 : génération incrémentée dans la même transaction (convergence
        # inter-processus), sauf paramètre nécessitant un redémarrage.
        if not spec.restart_required:
            config_sync.bump_generation()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error("Échec de mise à jour du select %r : %s", key, e)
        return display_toast(success=False, message=str(e))

    # Paramètre nécessitant un redémarrage : persisté mais non appliqué à chaud.
    if spec.restart_required:
        return display_toast(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    app.config[spec.config_name] = value
    call_function_with_select(key, value)
    return display_toast(success=True)


def call_function_with_select(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un select en plus de la sauvegarde"""
    # pour les couleurs, on met la page à jour. Pas possible en js direct, car rechargement trop rapide et on garde donc l'ancienne couleur sur la page
    if key == "admin_colors":
        communikation("admin", event="refresh_colors")

def call_function_with_switch(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un switch en plus de la sauvegarde"""
    if key == "cron_delete_patient_table_activated":
        if value == "true":
            add_scheduler_clear_all_patients()
        else:
            remove_scheduler_clear_all_patients()
    elif key == "cron_delete_announce_calls_activated":
        if value == "true":
            scheduler_clear_announce_calls()
        else:
            remove_scheduler_clear_announce_calls()


def check_balises_before_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    print("call_function_with_input", value)

    #return validate_and_transform_text_for_before_validation(value)

def check_balises_after_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    print("call_function_with_input", value)

    #return validate_and_transform_text_for_after_validation(value)


# --------  ADMIN -> DataBase  ---------

@app.route('/admin/database')
@require_permission('schedule')
def admin_database():
    return render_template('/admin/database.html',
                        cron_delete_patient_table_activated = app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"],
                        cron_transfer_patient_to_history = app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"],
                        cron_delete_patient_table_hour=app.config["CRON_DELETE_PATIENT_TABLE_HOUR"],
                        cron_delete_announce_calls_activated=app.config["CRON_DELETE_ANNOUNCE_CALLS_ACTIVATED"],
                        cron_delete_announce_calls_hour=app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"])



@app.route("/admin/database/schedule_tasks_list")
def display_schedule_tasks_list():
    jobs = scheduler.get_jobs()
    main_jobs = []
    other_jobs = []
    
    MAIN_JOBS = ['Clear Patient Table', 'Clear Announce Calls']
    
    for job in jobs:
        # Préparer les informations du job
        job_info = {
            'id': job.id,
            'next_run_time': str(job.next_run_time),
            'function_name': job.func.__name__,
            'trigger': str(job.trigger),
            'misfire_grace_time': job.misfire_grace_time,
            'coalesce': job.coalesce,
            'max_instances': job.max_instances,
            'cron_details': {
                'hour': job.trigger.fields[5] if hasattr(job.trigger, 'fields') else None,
                'minute': job.trigger.fields[4] if hasattr(job.trigger, 'fields') else None,
            }
        }
        
        # Récupérer les 5 dernières exécutions
        last_executions = JobExecutionLog.query.filter_by(
            job_id=job.id
        ).order_by(
            JobExecutionLog.execution_time.desc()
        ).limit(5).all()
        
        job_info['last_executions'] = [{
            'time': log.execution_time,
            'status': log.status,
            'error': log.error_message
        } for log in last_executions]
        
        # Séparer les jobs en deux groupes
        if job.id in MAIN_JOBS:
            main_jobs.append(job_info)
        else:
            other_jobs.append(job_info)
    
    return render_template('/admin/database_schedule_tasks_list.html',
                        main_jobs=main_jobs,
                        other_jobs=other_jobs)


@app.route('/admin/appschedule/dashboard')
@require_permission_dashboard('schedule')
def dashboard_counter():
    jobs = scheduler.get_jobs()
    main_jobs_info = []
    other_jobs_info = []
    MAIN_JOBS = ['Clear Patient Table', 'Clear Announce Calls']
    
    for job in jobs:
        # Récupérer la dernière exécution
        last_execution = JobExecutionLog.query.filter_by(
            job_id=job.id
        ).order_by(
            JobExecutionLog.execution_time.desc()
        ).first()
        
        job_info = {
            'id': job.id,
            'next_run_time': job.next_run_time,
            'last_execution': {
                'time': last_execution.execution_time if last_execution else None,
                'status': last_execution.status if last_execution else None,
                'error': last_execution.error_message if last_execution else None
            } if last_execution else None
        }
        
        if job.id in MAIN_JOBS:
            main_jobs_info.append(job_info)
        else:
            other_jobs_info.append(job_info)

    dashboardcard = DashboardCard.query.filter_by(name="appschedule").first()
    
    return render_template('/admin/dashboard_appschedule.html',
                            dashboardcard=dashboardcard,
                            main_jobs=main_jobs_info,
                            other_jobs=other_jobs_info)


def disable_buttons_for_activity_task(activity_id):
    with app.app_context():
        disable_buttons_for_activity(activity_id)

def enable_buttons_for_activity_task(activity_id):
    with app.app_context():
        enable_buttons_for_activity(app, activity_id)


@app.route('/patient_right_page_default')
def patient_right_page_default():
    print("default")
    return render_template('htmx/patient_right_page_default.html')


def call_specific_patient_action(counter_id, patient_id):
    validate_current_patient(counter_id)

    next_patient = Patient.query.get(patient_id)
    if not next_patient:
        return False, {"error": "not_found"}, 404

    # Réclamation atomique (même logique que call_next) : on ne passe le patient
    # à 'calling' que si il est TOUJOURS 'standing' en base. Sans ça, deux
    # comptoirs cliquant le même patient pouvaient tous deux le « décrocher ».
    claimed = (
        db.session.query(Patient)
        .filter(Patient.id == patient_id, Patient.status == "standing")
        .update({"status": "calling", "counter_id": counter_id}, synchronize_session=False)
    )
    if claimed:
        db.session.query(Counter).filter(Counter.id == counter_id).update(
            {"is_active": True}, synchronize_session=False
        )
    db.session.commit()

    if not claimed:
        app.logger.info("Already called")
        send_app_notification(origin="patient_taken", data={"counter_id": counter_id, "patient": next_patient})
        return False, {"error": "already_called"}, 423

    communikation("update_patient")

    text = replace_balise_announces(app.config["ANNOUNCE_CALL_TEXT"], next_patient)
    communikation("update_screen", event="add_calling", data={"id": next_patient.id, "counter_id": counter_id, "text": text})

    language_code = next_patient.language.code
    trigger_async_audio_calling(counter_id, next_patient.id, language_code)

    return True, next_patient.to_dict(), 200


@app.route('/call_specific_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def call_specific_patient(counter_id, patient_id):
    ok, payload, status_code = call_specific_patient_action(counter_id, patient_id)
    return jsonify(payload), status_code


@app.route('/validate_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def validate_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("validation", patient_id)

    if patient_id:
        current_patient = Patient.query.get(patient_id)
        if current_patient:
            current_patient.status = 'ongoing'
            current_patient.timestamp_counter = datetime.now(time_tz)
            db.session.commit()
    else:
        current_patient = None

    communikation("update_patient")
    communikation("update_screen", event="remove_calling", data={"id": patient_id})

    current_patient_pyside = current_patient.to_dict() if isinstance(current_patient, Patient) else {"id": None, "counter_id": counter_id}

    #return redirect(url_for('counter', counter_number=counter_number, current_patient_id=current_patient.id))
    return jsonify(current_patient_pyside), 200  


@app.route('/update_patient_status')
def update_patient_status():
    # Mettez à jour la base de données comme nécessaire
    return 'Status Updated'


# Route pour la page patients qui accepte une langue via l'URL
@app.route('/patients/<lang>')
def patients_langue(lang):
    session['lang'] = lang
    print(session['lang'])
    return render_template('patients.html', cache=False)


# ---------------- PAGE PATIENT FRONT ----------------

@with_app_context
def auto_calling():
    # si il y a des comptoirs en appel automatique on lance l'appel automatique
    print(app.config["AUTO_CALLING"])
    if len(app.config["AUTO_CALLING"]) > 0:
        counters = db.session.query(Counter).filter(
            Counter.id.in_(current_app.config["AUTO_CALLING"]),
            Counter.is_active.is_(False),
            Counter.staff_id != None
        ).all()

        print("auto counters", counters)

        if app.config["COUNTER_ORDER"] == "order":
            counters = sorted(counters, key=lambda x: x.sort_order)
        elif app.config["COUNTER_ORDER"] == "random":
            random.shuffle(counters)

        for counter in counters:
            if not counter.is_active:
                print("auto calling counter libre", counter.id)
                is_patient, patient = call_next(int(counter.id))
                # mise à jour écran ... bizarremment l'audio est dans le call next....
                text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], patient)
                communikation("update_screen", event="add_calling", data={"id": patient.id, "counter_id": counter.id, "text": text})
                counter_become_active(counter.id)
                # mise à jour de Pyside, car lui est mis à jour normalement via les retours du serveur et non via websocket contrairement au site (pour l'instant)
                communikation("app_counter", event="update_auto_calling", data={"counter_id": counter.id, "patient": patient.to_dict()})
                break


# liste des patients en attente : Nécessaire pour être transmis à Pyside
def list_patients_standing():
    patients_standing = Patient.query.filter_by(status='standing').all()
    patients_data = [patient.to_dict() for patient in patients_standing]
    return patients_data


# ---------------- FIN  PAGE PATIENTS FRONT ----------------


# ---------------- PAGE COUNTER FRONT ----------------

@app.route('/countert/<int:counter_id>')
def counter_test(counter_id):

    print("counter_number", counter_id)
    counter = Counter.query.get(counter_id)
    activities = Activity.query.all()
    # si l'id du comptoir n'existe pas -> page avec liste des comptoirs

    if not counter:
        return wrong_counter(counter_id)
    return render_template('counter/countert.html', 
                            counter=counter,
                            activities=activities)

#  On utilise l'ID dans l'URL pour éviter les erreurs (espace dans le nom...)
@app.route('/counter/<int:counter_id>')
def counter(counter_id):

    print("counter_number", counter_id)
    counter = Counter.query.get(counter_id)
    activities = Activity.query.all()
    # si l'id du comptoir n'existe pas -> page avec liste des comptoirs

    if not counter:
        return wrong_counter(counter_id)
    return render_template('counter/counter.html', 
                            counter=counter,
                            activities=activities)


# si le comptoir n'existe pas -> page avec liste des comptoirs
def wrong_counter(counter_id):
    return render_template('counter/wrong_counter.html', 
                    counters=Counter.query.all(), 
                    counter_id=counter_id)


@app.route('/current_patient_for_counter/<int:counter_id>')
def current_patient_for_counter(counter_id):
    """ Affiche le patient en cours de traitement pour un comptoir """
    print('counter_number ??', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id,
        Patient.status != 'done'
    ).first()
    print("CURRENT", patient)
    return render_template('counter/current_patient_for_counter.html', patient=patient)


@app.route('/counter/buttons_for_counter/<int:counter_id>')
def current_patient_for_counter_test(counter_id):
    """ Affiche le patient en cours de traitement pour un comptoir """
    print('counter_number', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id, 
        Patient.status != "done"
    ).first()
    if not patient:
        patient_id = None
        patient_status = None
    else :
        patient_id = patient.id
        patient_status = patient.status
    return render_template('counter/buttons_for_counter.html', 
                            patient=patient, 
                            patient_id=patient_id, 
                            counter_id=counter_id, 
                            status = patient_status,
                            current_staff=Counter.query.get(counter_id).staff  # TODO Utiliser une classe pour stocker ces infos
                            )


@app.route('/counter/switch_auto_calling/<int:counter_id>')
def switch_auto_calling(counter_id):
    counter = Counter.query.get(counter_id)
    return render_template('counter/switch_auto_calling.html',
                            counter=counter,
                            auto_calling=counter.auto_calling)




# A SUPPRIMER, NE FONCTIONNE PLUS AVEC HTTPS
@app.route('/counter_buttons/<int:counter_id>/')
def counter_refresh_buttons(counter_id):
    print('BUTTONS', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id, 
        Patient.status != "done"
    ).first()
    print("Patient", patient)
    if not patient:
        patient_id = None
        patient_status = None
    else :
        patient_id = patient.id
        patient_status = patient.status

    return render_template('/counter/display_buttons.html', counter_id=counter_id, patient_id=patient_id, status=patient_status)


@app.route('/validate_and_call_next/<int:counter_id>', methods=['POST'])
@require_app_token_or_login
@idempotent
def validate_and_call_next(counter_id):
    print('validate_and_call_next', counter_id)

    current_patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if current_patient:
        communikation("update_screen", event="remove_calling", data={"id": current_patient.id})

    validate_current_patient(counter_id)

    is_patient, next_patient = call_next(counter_id)

    
    if is_patient:
        counter_become_active(counter_id)   
        communikation("update_patient")
        
        text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], next_patient)
        communikation("update_screen", event="add_calling", data={"id": next_patient.id, "counter_id": counter_id, "text": text})
        
        return jsonify(next_patient.to_dict()), 200  

    # si pas de patient suivant, le comptoir devient inactif
    else:
        counter_become_inactive(counter_id)
        return '', 204


def validate_current_patient(counter_id):
    # On ne traite QUE les patients réellement en cours à ce comptoir
    # (calling / ongoing). Filtrer sur le statut évite de recharger et de
    # réécrire tous les patients déjà "done" de la journée : sinon le coût
    # devient quadratique au fil des appels et les anciens timestamp_end sont
    # écrasés par l'heure du dernier appel (statistiques de durée faussées).
    # On garde volontairement counter_id sur les patients terminés : les stats
    # du jour regroupent les patients "done" par comptoir (routes/admin_stats).
    active_patients = Patient.query.filter(
        Patient.counter_id == counter_id,
        Patient.status.in_(("calling", "ongoing")),
    ).all()

    if not active_patients:
        print("pas de patient")
        return

    now = datetime.now(time_tz)
    for patient in active_patients:
        if patient.status == "calling":
            communikation("update_screen", event="remove_calling", data={"id": patient.id})
        patient.status = 'done'
        patient.timestamp_end = now
    db.session.commit()


@app.route('/pause_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def pause_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("pause_patient")
    print("p", patient_id, "c",counter_id)
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'done'
        current_patient.timestamp_end = datetime.now(time_tz)
        db.session.commit()

    # le comptoir devient inactif
    counter_become_inactive(counter_id)
    
    communikation("update_patient")
    print("counterauto", Counter.query.get(counter_id).auto_calling)
    # si l'autocalling est activé. On le vire quand on se met en pause
    if Counter.query.get(counter_id).auto_calling:
        call_update_switch_auto_calling(counter_id)  

    return jsonify({"id": None, "counter_id": counter_id}), 200  

def call_update_switch_auto_calling(counter_id):
    with current_app.test_request_context():
        
        # Simuler les données de la requête
        request.values = {
            'counter_id': counter_id,
            'value': "false"
        }        
        # Appel direct de la fonction
        result = update_switch_auto_calling()

        # mise à jour du web. Pas nécessaire pour l'App
        communikation("counter", event="refresh_auto_calling", data={"auto_calling": False})
                
        print("switch_auto_calling")
        print(f"Résultat : {result}")
        




# ---------------- FIN  PAGE COUNTER FRONT ----------------


# ---------------- FONCTIONS Généralistes / COmmunication ---------------- 

# liste des flux SSE
update_patients = []
update_page_patient = []
update_admin = []
play_sound_streams = []
counter_streams = {}
update_announce = []
update_patient_pyside = []
update_patient_app = []
update_screen_app = []

def add_client(clients_list):
    client = Queue()
    clients_list.append(client)
    app.logger.debug(f"Added new client. Total clients: {len(clients_list)}")
    return client

def remove_client(clients_list, client):
    clients_list.remove(client)
    app.logger.debug(f"Removed client. Total clients: {len(clients_list)}")

def event_stream(clients):
    return None
    client = add_client(clients)
    print("client", client)
    try:
        while True:
            message = client.get(timeout=30)  # Use timeout to avoid blocking indefinitely
            app.logger.debug(f"Sending message: {message}")
            yield f'data: {message}\n\n'
    except Empty:
        yield 'data: no-message\n\n'
    except GeneratorExit:
        app.logger.debug("Client disconnected, removing client.")
        remove_client(clients, client)

def event_stream_dict(client_id):
    app.logger.debug("start event event_stream_dict")
    client_queue = counter_streams[client_id]
    try:
        while True:
            try:
                message = client_queue.get(timeout=30)
                app.logger.debug(f"message test: {message}")
                yield f'data: {message}\n\n'
            except Empty:
                yield 'data: no-message\n\n'
                app.logger.debug("No message for client.")
    except GeneratorExit:
        app.logger.debug("Generator exit, client disconnected")
    finally:
        counter_streams.pop(client_id, None)
        app.logger.debug(f"Stream closed for client {client_id}")

"""@app.route('/events/update_patients')
def events_update_patients():
    return Response(event_stream(update_patients), content_type='text/event-stream')

@app.route('/events/update_patient_app')
def events_update_patients_app():
    return Response(event_stream(update_patient_app), content_type='text/event-stream')

@app.route('/events/update_screen_app')
def events_update_screen_app():
    return Response(event_stream(update_screen_app), content_type='text/event-stream')

@app.route('/events/sound_calling')
def events_update_sound_calling():
    return Response(event_stream(play_sound_streams), content_type='text/event-stream')

@app.route('/events/update_counter/<int:client_id>')
def events_update_counter(client_id):
    if client_id not in counter_streams:
        counter_streams[client_id] = Queue()  # Crée une nouvelle Queue si elle n'existe pas
    return Response(event_stream_dict(client_id), content_type='text/event-stream')

@app.route('/events/update_admin')
def events_update_admin():
    return Response(event_stream(update_admin), content_type='text/event-stream')

@app.route('/events/update_announce')
def events_update_announce():
    return Response(event_stream(update_announce), content_type='text/event-stream')

@app.route('/events/update_page_patient')
def events_update_page_patients():
    return Response(event_stream(update_page_patient), content_type='text/event-stream')

@app.route('/events/update_patient_pyside')
def events_update_patient_pyside():
    return Response(event_stream(update_patient_pyside), content_type='text/event-stream')
"""






def display_toast(success=True, message=None):
    """ Affiche le toast dans la page Admin.
    Pour validation réussie, on peut simplement appeler la fonction sans argument """
    if message is None:
        message = "Enregistrement effectué"
        
    data = {"toast": True, 'success': success, 'message': message}
    communikation("admin", data)
    return "", 204
    #return f'<script>display_toast({data})</script>'


def config_change_response(success=True, message=None):
    """Réponse renvoyée DIRECTEMENT à l'auteur d'une modification de paramètre.

    Contrairement à :func:`display_toast`, qui diffuse le résultat par WebSocket
    à TOUS les administrateurs connectés (chaque admin voit alors un toast pour
    une action qu'il n'a pas faite), cette fonction ne répond qu'au client qui a
    soumis la requête :

    - le **statut HTTP** distingue succès (200) et échec (400), ce qui permet au
      JavaScript (``handleAfterRequestConfig``) de tester ``event.detail.successful``
      et de ne mettre à jour la valeur initiale du champ qu'en cas de succès ;
    - le **corps** contient le message à afficher près du champ concerné.

    Aucune diffusion WebSocket n'est effectuée ici.
    """
    if message is None:
        message = "Enregistré." if success else "Échec de l'enregistrement."
    status = 200 if success else 400
    return message, status, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------- FONCTIONS Généralistes > Communication avec Pyside ---------------- 

@app.route('/api/counters', methods=['GET'])
@require_app_token_or_login
def get_counters():
    counters = Counter.query.all()
    counters_list = [{'id': counter.id, 'name': counter.name} for counter in counters]
    return jsonify(counters_list)


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

# A PRIORI NE SERT PLUS A RIEN
@app.route('/current_patients')
def current_patients():
    # Supposons que vous vouliez afficher les patients dont le statut est "au comptoir"
    patients = Patient.query.filter_by(status='ongoing').all()
    print(patients)
    return render_template('htmx/update_patients.html', patients=patients)


@app.route('/add_counter', methods=['POST'])
@require_permission('counter')
def add_counter():
    if request.method == 'POST':
        name = request.form['name']
        new_counter = Counter(name=name)
        db.session.add(new_counter)
        db.session.commit()
        return redirect('/admin')
    return "Erreur dans la soumission du formulaire"


@app.route('/patients_queue')
def patients_queue():
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp, Patient.id).all()
    return render_template('htmx/patients_queue.html', patients=patients)


@app.route('/pharmacists')
@require_permission('staff')
def pharmacists():
    all_pharmacists = Pharmacist.query.all()
    print("ALL", all_pharmacists)
    return render_template('pharmacists.html', pharmacists=all_pharmacists)


@app.route('/update_pharmacist/<int:pharmacist_id>', methods=['POST'])
@require_permission('staff')
def update_pharmacist(pharmacist_id):
    pharmacist = Pharmacist.query.get(pharmacist_id)
    if pharmacist:
        pharmacist.name = request.form.get('name', pharmacist.name)
        pharmacist.initials = request.form.get('initials', pharmacist.initials)
        pharmacist.language = request.form.get('language', pharmacist.language)
        pharmacist.is_active = 'is_active' in request.form
        pharmacist.activity = request.form.get('activity', pharmacist.activity)
        db.session.commit()
    return redirect(url_for('pharmacists'))


@app.route('/add_pharmacist', methods=['POST'])
@require_permission('staff')
def add_pharmacist():
    name = request.form.get('name')
    initials = request.form.get('initials')
    language = request.form.get('language')
    is_active = request.form.get('is_active') == 'on'
    activity = request.form.get('activity')
    new_pharmacist = Pharmacist(name=name, initials=initials, language=language, is_active=is_active, activity=activity)
    db.session.add(new_pharmacist)
    db.session.commit()
    return render_template('htmx/menu_admin_pharmacist_row.html', pharmacist=new_pharmacist)


@app.route('/new_pharmacist_form')
@require_permission('staff')
def new_pharmacist_form():
    print("new_pharmacist_form")
    return render_template('htmx/menu_admin_new_pharmacist_form.html')


# Définir un filtre pour Jinja2
@app.template_filter('format_time')
def format_time(value):
    return value.strftime('%H:%M') if value else ''


def allowed_image_file(filename):
    """Vérifie si le fichier a une extension autorisée."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_IMAGE_EXTENSIONS"]


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
app.load_configuration = load_configuration
app.display_toast = display_toast
app.call_specific_patient = call_specific_patient_action
app.allowed_image_file = allowed_image_file
app.mail = mail
app.auto_calling = auto_calling
app.socketio = socketio
app.database = database
app.scheduler = scheduler

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

# creation BDD si besoin et initialise certaines tables (Activités)
def initialize_data():
    pass
            
initialize_data()

print("Starting Flask...")
app.logger.info(f"Starting Flask on port {server_port} with debug={app.debug}")

#app.run(host='0.0.0.0', port=server_port, debug=app.debug, threaded=True)

app.logger.info("Starting Flask app...")
