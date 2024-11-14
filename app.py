# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger

# deux lignes a appeler avant tout le reste (pour server Render)
import eventlet
eventlet.monkey_patch() #thread=True, time=True
from flask import Flask, render_template, request, redirect, url_for, session, current_app, jsonify, send_from_directory, Response, g, make_response, request, has_request_context, flash, session

from sqlalchemy.orm import sessionmaker, relationship, backref, session as orm_session, exc as sqlalchemy_exceptions, joinedload
from sqlalchemy import func, CheckConstraint, and_, Boolean, DateTime, Column, Integer, String, ForeignKey
from flask_migrate import Migrate
from flask.signals import request_started
from flask_mailman import Mail
from flask_socketio import SocketIO
from datetime import datetime, time, timedelta
import time as tm

from flask_apscheduler import APScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

import json

import os
from queue import Queue, Empty
import logging
import subprocess
import threading
import socket
import pika
import threading

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

from models import db, Patient, Counter, Pharmacist, Activity, Button, Language, Text, AlgoRule, ActivitySchedule, ConfigOption, ConfigVersion, User, Role, Weekday, TextTranslation, activity_schedule_link, Translation
from init_restore import init_default_buttons_db_from_json, init_default_options_db_from_json, init_default_languages_db_from_json, init_or_update_default_texts_db_from_json, init_update_default_translations_db_from_json, init_default_algo_rules_db_from_json, init_days_of_week_db_from_json, init_activity_schedules_db_from_json, clear_counter_table, restore_config_table_from_json, init_staff_data_from_json, restore_staff, restore_counters, init_counters_data_from_json, restore_schedules, restore_algorules, restore_activities, init_default_activities_db_from_json, restore_buttons, restore_databases, init_default_dashboard_db_from_json, init_default_patient_css_variables_db_from_json, init_default_announce_css_variables_db_from_json
from python.engine import generate_audio_calling, call_next, counter_become_inactive, counter_become_active
from utils import validate_and_transform_text, parse_time, convert_markdown_to_escpos, replace_balise_announces, replace_balise_phone, get_buttons_translation, choose_text_translation, get_text_translation
from backup import backup_config_all, backup_staff, backup_counters, backup_schedules, backup_algorules, backup_activities, backup_buttons, backup_databases
from scheduler_functions import enable_buttons_for_activity, disable_buttons_for_activity, add_scheduler_clear_all_patients, clear_old_patients_table, remove_scheduler_clear_all_patients, remove_scheduler_clear_announce_calls, scheduler_clear_announce_calls
from bdd import init_database
from config import Config, time_tz
from communication import send_app_notification, start_rabbitmq_consumer, communikation
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
from routes.admin_security import admin_security_bp, ExtendedLoginForm
from routes.admin_music import admin_music_bp, is_spotipy_connected
from routes.admin_dashboard import admin_dashboard_bp
from routes.admin_app import admin_app_bp
from routes.announce import announce_bp
from routes.admin_stats import admin_stats_bp
from routes.patient import patient_bp
from routes.pyside import pyside_bp, create_patients_list_for_pyside
from python.engine import engine_bp

# adresse production
rabbitMQ_url = 'amqp://rabbitmq:ojp5seyp@rabbitmq-7yig:5672'
# adresse developement
rabbitMQ_url = 'amqp://guest:guest@localhost:5672/%2F'

site = "production"
communication_mode = "websocket"  # websocket, sse or rabbitmq

database = "mysql"
# A mettre dans la BDD ?
status_list = ['ongoing', 'standing', 'done', 'calling']

if site == "production":
    credentials = pika.PlainCredentials('rabbitmq', 'ojp5seyp')
    parameters = pika.ConnectionParameters('rabbitmq-7yig',
                                    5672,
                                    '/',
                                    credentials)
else:
    parameters = pika.URLParameters('amqp://guest:guest@localhost:5672/%2F')

mail = Mail()
migrate = Migrate()

"""def communikation(stream, data=None, flag=None, event="update", client_id=None):

    print(f"communikation called with stream={stream}, data={data}, flag={flag}, event={event}, client_id={client_id}")
    print("communikation", communication_mode, data, event)
    if communication_mode == "websocket":
        #communication_websocket(stream=f"socket_{stream}", data=data)
        if stream == "update_patient":
            patients = create_patients_list_for_pyside()
            print("PYSODE", patients)
            #data = json.dumps({"flag": "patient", "data": patients})
            communication_websocket(stream="socket_app_counter", data=patients, flag="update_patient_list")
            communication_websocket(stream="socket_app_counter", data=patients, flag="my_patient")
            communication_websocket(stream="socket_update_patient", data=patients)
        elif stream == "update_audio":
            if event == "spotify":
                print("spotify!")
                communication_websocket(stream="socket_update_screen", data=data, flag=flag, event=event)
            
            else:
                if app.config["ANNOUNCE_ALERT"]:
                    signal_file = app.config["ANNOUNCE_ALERT_FILENAME"]
                    audio_path = url_for('static', filename=f'audio/signals/{signal_file}', _external=True)
                    if app.config["ANNOUNCE_PLAYER"] == "web":
                        communication_websocket(stream="socket_update_screen", event="audio", data=audio_path)
                    else:
                        communication_websocket(stream="socket_app_screen", data=audio_path, flag="sound")
                if app.config["ANNOUNCE_PLAYER"] == "web":
                    communication_websocket(stream="socket_update_screen", data=data, event="audio")
                else:
                    communication_websocket(stream="socket_app_screen", data=data, flag="sound")
        else:
            communication_websocket(stream=f"socket_{stream}", data=data, flag=flag, event=event)
    # REFAIRE !!!! 
    elif communication_mode == "rabbitmq":
        communication_rabbitmq(queue=f"socket_{stream}", data=data)
        if stream == "update_patient":
            patients = create_patients_list_for_pyside()
            data = json.dumps({"type": "patient", "list": patients})
            communication_rabbitmq(queue="socket_app_counter", data=data)
        else:
            communication_rabbitmq(stream=stream, data=data)


def communication_websocket(stream, data=None, flag=None, client_id=None, event="update"):
    print('communication_websocket')
    print("streamm", stream)
    print("data", data)
    print("event", event)

    # Utiliser request.args.get uniquement si dans le contexte de la requête
    if has_request_context():
        stream = request.args.get('stream', stream)
        message = request.args.get('message', data)
    else:
        message = data

    try:
        namespace = f'/{stream}'
        socketio.emit(event, {"flag": flag, 'data': message}, namespace=namespace)
        print("message:", message)
        print("namespace:", namespace)
        return "Message sent!"
    except Exception as e:
        print("message failed:", message)
        return f"Failed to send message: {e}", 500
    

def communication_rabbitmq(queue, data=None, client_id=None):

    message = data
    try:
        channel.basic_publish(exchange='',
                            routing_key=queue,
                            body=message)
        print("message:", message)
        print("queue:", queue)
        return "Message sent to RabbitMQ!"
    except Exception as e:
        print("message failed:", message)
        return f"Failed to send message: {e}", 500"""


# Charge des valeurs qui ne sont pas amener à changer avant redémarrage APP
def load_configuration(app):
    app.logger.info("Loading configuration from database")

    config_mappings = {
        "pharmacy_name": ("PHARMACY_NAME", "value_str"),
        "network_adress": ("NETWORK_ADRESS", "value_str"),
        "numbering_by_activity": ("NUMBERING_BY_ACTIVITY", "value_bool"),
        "start_rabbitmq": ("START_RABBITMQ", "value_bool"),
        "use_rabbitmq": ("USE_RABBITMQ", "value_bool"),
        "algo_activate": ("ALGO_IS_ACTIVATED", "value_bool"),
        "algo_overtaken_limit": ("ALGO_OVERTAKEN_LIMIT", "value_int"),
        "printer": ("PRINTER", "value_bool"),
        "printer_width": ("PRINTER_WIDTH", "value_int"),
        "add_paper": ("ADD_PAPER", "value_bool"),
        "admin_colors": ("ADMIN_COLORS", "value_str"),
        "announce_title": ("ANNOUNCE_TITLE", "value_str"),
        "announce_title_size": ("ANNOUNCE_TITLE_SIZE", "value_int"),
        "announce_subtitle": ("ANNOUNCE_SUBTITLE", "value_str"),
        "announce_text_up_patients": ("ANNOUNCE_TEXT_UP_PATIENTS", "value_str"),
        "announce_text_up_patients_display": ("ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY", "value_str"),
        "announce_text_up_patients_size": ("ANNOUNCE_TEXT_UP_PATIENTS_SIZE", "value_int"),
        "announce_text_down_patients": ("ANNOUNCE_TEXT_DOWN_PATIENTS", "value_str"),
        "announce_text_down_patients_display": ("ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY", "value_str"),
        "announce_text_down_patients_size": ("ANNOUNCE_TEXT_DOWN_PATIENTS_SIZE", "value_int"),
        "announce_sound": ("ANNOUNCE_SOUND", "value_bool"),
        "announce_alert": ("ANNOUNCE_ALERT", "value_bool"),
        "announce_alert_filename": ("ANNOUNCE_ALERT_FILENAME", "value_str"),
        "announce_style": ("ANNOUNCE_STYLE", "value_str"),
        "announce_player": ("ANNOUNCE_PLAYER", "value_str"),
        "announce_infos_display": ("ANNOUNCE_INFOS_DISPLAY", "value_bool"),
        "announce_infos_display_time": ("ANNOUNCE_INFOS_DISPLAY_TIME", "value_int"),
        "announce_infos_transition": ("ANNOUNCE_INFOS_TRANSITION", "value_str"),
        "announce_infos_gallery": ("ANNOUNCE_INFOS_GALLERY", "value_str"),
        "announce_gallery_folders": ("ANNOUNCE_GALLERY_FOLDERS", "value_str"),
        "announce_infos_mix_folders": ("ANNOUNCE_INFOS_MIX_FOLDERS", "value_bool"),
        "announce_infos_width": ("ANNOUNCE_INFOS_WIDTH", "value_int"),
        "announce_infos_height": ("ANNOUNCE_INFOS_HEIGHT", "value_int"),
        "announce_call_text": ("ANNOUNCE_CALL_TEXT", "value_str"),
        "announce_call_text_size": ("ANNOUNCE_CALL_TEXT_SIZE", "value_int"),
        "announce_call_text_transition": ("ANNOUNCE_CALL_TEXT_TRANSITION", "value_str"),
        "announce_ongoing_display": ("ANNOUNCE_ONGOING_DISPLAY", "value_bool"),
        "announce_ongoing_text": ("ANNOUNCE_ONGOING_TEXT", "value_str"),
        "announce_call_sound": ("ANNOUNCE_CALL_SOUND", "value_str"),
        "announce_call_translation": ("ANNOUNCE_CALL_TRANSLATION", "value_str"),
        "counter_order": ("COUNTER_ORDER", "value_str"),
        "music_volume": ("MUSIC_VOLUME", "value_int"),
        "music_announce_volume": ("MUSIC_ANNOUNCE_VOLUME", "value_int"),
        "music_announce_action": ("MUSIC_ANNOUNCE_ACTION", "value_str"),
        "music_spotify": ("MUSIC_SPOTIFY", "value_bool"),        
        "music_spotify_user": ("MUSIC_SPOTIFY_USER", "value_str"),
        "music_spotify_key": ("MUSIC_SPOTIFY_KEY", "value_str"),
        "page_patient_structure" : ("PAGE_PATIENT_STRUCTURE", "value_str"),
        "page_patient_disable_button": ("PAGE_PATIENT_DISABLE_BUTTON", "value_bool"),
        "page_patient_disable_default_message": ("PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE", "value_str"),
        "page_patient_title": ("PAGE_PATIENT_TITLE", "value_str"),
        "page_patient_subtitle": ("PAGE_PATIENT_SUBTITLE", "value_str"),
        "page_patient_validation_message": ("PAGE_PATIENT_VALIDATION_MESSAGE", "value_str"),
        "page_patient_confirmation_message": ("PAGE_PATIENT_CONFIRMATION_MESSAGE", "value_str"),
        "page_patient_qrcode_display": ("PAGE_PATIENT_QRCODE_DISPLAY", "value_bool"),
        "page_patient_display_button_scan" : ("PAGE_PATIENT_DISPLAY_BUTTON_SCAN", "value_bool"),
        "page_patient_display_scan_explanation": ("PAGE_PATIENT_DISPLAY_SCAN_EXPLANATION", "value_bool"),
        "page_patient_qrcode_web_page": ("PAGE_PATIENT_QRCODE_WEB_PAGE", "value_bool"),
        "page_patient_qrcode_data": ("PAGE_PATIENT_QRCODE_DATA", "value_str"),
        "page_patient_qrcode_display_specific_message": ("PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
        "page_patient_print_ticket_display": ("PAGE_PATIENT_PRINT_TICKET_DISPLAY", "value_bool"),
        "page_patient_end_timer": ("PAGE_PATIENT_END_TIMER", "value_int"),
        "page_patient_display_specific_message": ("PAGE_PATIENT_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
        "page_patient_display_translations": ("PAGE_PATIENT_DISPLAY_TRANSLATIONS", "value_bool"),
        "page_patient_interface_validate_print": ("PAGE_PATIENT_INTERFACE_VALIDATE_PRINT", "value_str"),
        "page_patient_interface_validate_scan": ("PAGE_PATIENT_INTERFACE_VALIDATE_SCAN", "value_str"),
        "page_patient_interface_scan_explanation": ("PAGE_PATIENT_INTERFACE_SCAN_EXPLANATION", "value_str"),
        "page_patient_interface_validate_cancel": ("PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL", "value_str"),
        "page_patient_interface_done_print": ("PAGE_PATIENT_INTERFACE_DONE_PRINT", "value_str"),
        "page_patient_interface_done_extend": ("PAGE_PATIENT_INTERFACE_DONE_EXTEND", "value_str"),
        "page_patient_interface_done_back": ("PAGE_PATIENT_INTERFACE_DONE_BACK", "value_str"),
        "page_patient_print_after_scan": ("PAGE_PATIENT_PRINT_AFTER_SCAN", "value_bool"),
        "page_patient_print_after_print": ("PAGE_PATIENT_PRINT_AFTER_PRINT", "value_bool"),
        "page_patient_timer_activity_inactive": ("PAGE_PATIENT_TIMER_ACTIVITY_INACTIVE", "value_int"),
        "ticket_header": ("TICKET_HEADER", "value_str"),
        "ticket_header_printer": ("TICKET_HEADER_PRINTER", "value_str"),
        "ticket_message": ("TICKET_MESSAGE", "value_str"),
        "ticket_message_printer": ("TICKET_MESSAGE_PRINTER", "value_str"),
        "ticket_footer": ("TICKET_FOOTER", "value_str"),
        "ticket_footer_printer": ("TICKET_FOOTER_PRINTER", "value_str"),
        "ticket_display_specific_message": ("TICKET_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
        "mail_server": ("MAIL_SERVER", "value_str"),
        "mail_port": ("MAIL_PORT", "value_int"),
        "mail_username": ("MAIL_USERNAME", "value_str"),
        "mail_password": ("MAIL_PASSWORD", "value_str"),
        "mail_default_sender": ("MAIL_DEFAULT_SENDER", "value_str"),
        "mail_use_tls": ("MAIL_USE_TLS", "value_bool"),
        "mail_use_ssl": ("MAIL_USE_SSL", "value_bool"),
        "phone_center": ("PHONE_CENTER", "value_bool"),
        "phone_title": ("PHONE_TITLE", "value_str"),
        "phone_line1": ("PHONE_LINE1", "value_str"),
        "phone_line2": ("PHONE_LINE2", "value_str"),
        "phone_line3": ("PHONE_LINE3", "value_str"),
        "phone_line4": ("PHONE_LINE4", "value_str"),
        "phone_line5": ("PHONE_LINE5", "value_str"),
        "phone_line6": ("PHONE_LINE6", "value_str"),
        "phone_display_specific_message": ("PHONE_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
        "cron_delete_patient_table_activated": ("CRON_DELETE_PATIENT_TABLE_ACTIVATED", "value_bool"),
        "cron_transfer_patient_to_history": ("CRON_TRANSFER_PATIENT_TO_HISTORY", "value_bool"),
        "cron_delete_patient_table_hour": ("CRON_DELETE_PATIENT_TABLE_HOUR", "value_str"),
        "cron_delete_announce_calls_activated": ("CRON_DELETE_ANNOUNCE_CALLS_ACTIVATED", "value_bool"),
        "cron_delete_announce_calls_hour": ("CRON_DELETE_ANNOUNCE_CALLS_HOUR", "value_str"),
        "security_login_admin": ("SECURITY_LOGIN_ADMIN", "value_bool"),
        "security_login_counter": ("SECURITY_LOGIN_COUNTER", "value_bool"),
        "security_login_screen": ("SECURITY_LOGIN_SCREEN", "value_bool"),
        "security_login_patient": ("SECURITY_LOGIN_PATIENT", "value_bool"),
        "security_remember_duration": ("SECURITY_REMEMBER_DURATION", "value_int")
    }

    for key, (config_name, value_type) in config_mappings.items():
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        if config_option:
            app.config[config_name] = getattr(config_option, value_type)

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

    # printer
    app.config["PRINTER_INFOS"] = []
    app.config["PRINTER_ERROR"] = {
        'error': True,
        'message': "pas de connexion à l'App Patient",
        'timestamp': datetime.now(time_tz)
    }

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


def start_fonctions(app):

    init_database(database, db)

    # Pour gérer les app.config des CSS. A faire également pour mon Config général
    css_variable_manager = MultiCssVariableManager(app)

    app.css_manager = CSSManager()
    app.css_manager.init_app(app)


    # Check if the user table is empty and create an admin user if it is

    if User.query.count() == 0:
        app.logger.info("Creating admin user...")
        #admin_role = Role.query.filter_by(name='admin').first()
        #if not admin_role:
            #admin_role = Role(name='admin', description='Administrator')
            #db.session.add(admin_role)
            #db.session.commit()

        admin_user = User(email='admin', username='admin', password=hash_password('admin'), active=True, confirmed_at=datetime.now())
        #admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.commit()

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
    load_configuration(app)
    clear_old_patients_table(app)
    css_variable_manager.reload_all()
    clear_counter_table()

    # désactiver la possibilité d'utiliser rabbitMQ s'il n'est pas lancé
    if not app.config["START_RABBITMQ"]:
        app.config["USE_RABBITMQ"] = False


def create_app(config_class=Config):
    app = Flask(__name__)

    print("CREATE_APP_HOST:", os.getenv('MYSQL_HOST'))

    # Charger la configuration avant toute initialisation
    AppHolder.set_app(app)
    app.config.from_object(config_class)
    app.debug = True

    # Initialiser le logging
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

    db.init_app(app)
    migrate.init_app(app, db)

    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    security = Security(app, user_datastore, register_blueprint=True)
    #security.init_app(app, user_datastore, register_blueprint=True, name='flask_security')

    # Initialiser le mail avec l'application
    app.mail = Mail(app)

    print("CONFIG_CLASS", config_class.SECURITY_PASSWORD_HASH)

    # Appeler explicitement des fonctions de démarrage dans le contexte de l'application
    with app.app_context():
        start_fonctions(app)

    # Enregistrement des blueprints
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
    app.register_blueprint(engine_bp, url_prefix='')

    return app

load_dotenv()
print("MYSQL_USER:", os.getenv('MYSQL_USER'))
print("MYSQL_PASSWORD:", os.getenv('MYSQL_PASSWORD'))
print("MYSQL_HOST:", os.getenv('MYSQL_HOST'))
app = create_app(config_class=Config)
print("App configuration:", app.config)

socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")
#start_rabbitmq_consumer(app)

# Définir le jobstore avec votre base de données
jobstores = {
    'default': SQLAlchemyJobStore(url=app.config['SQLALCHEMY_DATABASE_URI_SCHEDULER'])
}
scheduler = BackgroundScheduler(jobstores=jobstores)
#scheduler.init_app(app)
scheduler.start()

def callback_update_patient(ch, method, properties, body):
    logging.debug(f"Received general message: {body}")
    if communication_mode == "websocket":
        socketio.emit('general_message', {'data': body.decode()}, namespace='/socket_update_patient')
    ch.basic_ack(delivery_tag=method.delivery_tag)

def callback_sound(ch, method, properties, body):
    logging.debug(f"Received screen message: {body}")
    if communication_mode == "websocket":
        socketio.emit('update', {'data': body.decode()}, namespace='/socket_update_screen')
    ch.basic_ack(delivery_tag=method.delivery_tag)

def callback_admin(ch, method, properties, body):
    logging.debug(f"Received screen message: {body}")
    if communication_mode == "websocket":
        socketio.emit('update', {'data': body.decode()}, namespace='/socket_admin')
    ch.basic_ack(delivery_tag=method.delivery_tag)

def callback_app_counter(ch, method, properties, body):
    logging.debug(f"Received screen message: {body}")
    if communication_mode == "websocket":
        socketio.emit('update', {'data': body.decode()}, namespace='/socket_app_counter')
    ch.basic_ack(delivery_tag=method.delivery_tag)

def callback_counter(ch, method, properties, body):
    logging.debug(f"Received counter message: {body}")
    if communication_mode == "websocket":
        socketio.emit('update', {'data': body.decode()}, namespace='/socket_counter')
    ch.basic_ack(delivery_tag=method.delivery_tag)

# continuer les connexioNs Rabbit

def consume_rabbitmq(connection, channel, queue_name, callback):
    channel.queue_declare(queue=queue_name)
    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
    logging.info(f"Starting RabbitMQ consumption on {queue_name}")
    while True:
        connection.process_data_events(time_limit=None)


# Dictionnaire pour suivre les connexions actives
active_connections = {
    '/socket_update_patient': set(),
    '/socket_update_screen': set(),
    '/socket_admin': set(),
    '/socket_patient': set(),
    '/socket_app_counter': set(),
    '/socket_app_patient': set(),
    '/socket_app_screen': set(),
    '/socket_counter': set()
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
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_update_screen')
def disconnect_screen():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_admin', )
def connect_admin():
    username = get_and_register_socketio_username(request)
    logging.info(f"Client connected to admin namespace with SID {request.sid} and username {username}")
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_admin')
def disconnect_admin():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_patient')
def connect_patient():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to update patient namespace")

@socketio.on('disconnect', namespace='/socket_patient')
def disconnect_patient():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from patient namespace")

@socketio.on('connect', namespace='/socket_app_counter')
def connect_app_counter():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to app counter namespace")

@socketio.on('disconnect', namespace='/socket_app_counter')
def disconnect_app_counter():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from app counter namespace")

@socketio.on('connect', namespace='/socket_app_patient')
def connect_app_patient():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to test namespace")

@socketio.on('disconnect', namespace='/socket_app_patient')
def disconnect_app_patient():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from test namespace")

@socketio.on('connect', namespace='/socket_app_screen')
def connect_app_screen():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to test namespace")

@socketio.on('disconnect', namespace='/socket_app_screen')
def disconnect_app_screen():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from test namespace")

@socketio.on('connect', namespace='/socket_counter')
def connect_counter():
    username = get_and_register_socketio_username(request)
    logging.info("Client connected to counter namespace")

@socketio.on('disconnect', namespace='/socket_counter')
def disconnect_counter():
    app.connected_clients_info.pop(request.sid, None)
    logging.info("Client disconnected from counter namespace")


@app.route('/send_message', methods=['POST'])
def send_message():
    message = request.json.get('message', 'Hello from server')
    try:
        socketio.emit('new_message', {'data': message})
        return "Message sent!"
    except Exception as e:
        return f"Failed to send message: {e}", 500


#threading.Thread(target=consume_rabbitmq).start()
#socketio.run(app, host='0.0.0.0', port=5001)

#socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60000, ping_interval=30000)

#app.config['DEBUG_TB_PROFILER_ENABLED'] = True  # Activer le profiler
#toolbar = DebugToolbarExtension(app)

@app.errorhandler(404)
def page_not_found(e):
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>404 Not Found</title>
    </head>
    <body>
        <h1>404 Not Found</h1>
        <p>The requested URL was not found on the server.</p>
        <p>Error: {e}</p>
        <p>Request Path: {request.path}</p>
        <p>Request Method: {request.method}</p>
        <p>Request Headers: {request.headers}</p>
    </body>
    </html>
    """, 404


@app.route('/send')
def send_message_old():
    url = rabbitMQ_url
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
def rabbitmq_status():
    url = rabbitMQ_url
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
def rabbitmq_status_local():
    rabbitmq_url = 'amqp://guest:guest@127.0.0.1:5672'
    params = pika.URLParameters(rabbitmq_url)

    try:
        connection = pika.BlockingConnection(params)
        print("Successfully connected to RabbitMQ (local)")
        connection.close()
    except Exception as e:
        print(f"Failed to connect to RabbitMQ (local): {e}")
    return "", 204



"""with app.app_context():
    app.add_url_rule('/admin/backup/config', 'backup_config_all', backup_config_all(ConfigOption, ConfigVersion))
"""

# ROUTES 

# Sauvegardes / Restaurations

app.add_url_rule('/admin/database/backup', 'backup_databases', 
                partial(backup_databases, database), 
                methods=['GET'])

app.add_url_rule('/admin/database/restore', 'restore_databases', 
                partial(restore_databases, request, database), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/backup/config', 'backup_config_all', 
                partial(backup_config_all, ConfigOption, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/restore/config', 'restore_config_all', 
                partial(restore_config_table_from_json, db, ConfigVersion, ConfigOption, request), 
                methods=['POST'])

app.add_url_rule('/admin/staff/backup', 'backup_staff', 
                partial(backup_staff, Pharmacist, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/staff/restore', 'restore_staff', 
                partial(restore_staff, db, ConfigVersion, Pharmacist, Activity, request), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/counter/backup', 'backup_counter', 
                partial(backup_counters, Counter, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/counter/restore', 'restore_counter', 
                partial(restore_counters, db, ConfigVersion, Counter, Activity, request), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/schedules/backup', 'backup_schedules', 
                partial(backup_schedules, ActivitySchedule, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/schedules/restore', 'restore_schedules', 
                partial(restore_schedules, db, ConfigVersion, ActivitySchedule, Activity, Weekday, request), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/activities/backup', 'backup_activities', 
                partial(backup_activities, Activity, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/activities/restore', 'restore_activities', 
                partial(restore_activities, db, ConfigVersion, Activity, ActivitySchedule, request), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/algorules/backup', 'backup_algorules', 
                partial(backup_algorules, AlgoRule, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/algorules/restore', 'restore_algorules', 
                partial(restore_algorules, db, ConfigVersion, AlgoRule, request), 
                methods=['GET', 'POST'])

app.add_url_rule('/admin/buttons/backup', 'backup_buttons', 
                partial(backup_buttons, Button, ConfigVersion), 
                methods=['GET'])

app.add_url_rule('/admin/buttons/restore', 'restore_buttons', 
                partial(restore_buttons, db, ConfigVersion, Button, Activity, request), 
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
    # Ici, vous devriez implémenter une vérification des credentials de l'application
    # Par exemple, vérifier un secret partagé ou des identifiants spécifiques à l'application
    if request.form.get('app_secret') == 'votre_secret_app':
        token = generate_app_token()
        return jsonify({"token": token})
    else:
        return jsonify({"error": "Unauthorized"}), 401

@app.before_request
def require_login_for_admin():
    """ Défini les règles de login """

    app_token = request.headers.get('X-App-Token')
    is_valid_app_request = app_token and verify_app_token(app_token)

    if request.path.startswith('/admin'):
        if app.config["SECURITY_LOGIN_ADMIN"] and not current_user.is_authenticated:
            print("SECURITY_LOGIN_ADMIN", request.url)
            return redirect(url_for('admin_security.login', next=request.url))
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
            if is_valid_app_request:
                return jsonify({"error": "Unauthorized"}), 401
            else:
                return redirect(url_for('admin_security.login', next=request.url))
        


@app.route('/admin/update_switch', methods=['POST'])
def update_switch():
    """ Mise à jour des switches d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')
    print("key, value", key, value)
    try:
        # MAJ BDD
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        # MAJ Config 
        app.config[key.upper()] = True if value == "true" else False
        if config_option:
            config_option.value_bool = True if value == "true" else False
            db.session.commit()
            call_function_with_switch(key, value)
            return display_toast(success=True, message="Option mise à jour.")
        else:
            return display_toast(success=False, message="Option non trouvée.")
    except Exception as e:
            print(e)
            return display_toast(success=False, message=str(e))
    

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

    print("DEP", request.form)
    
    # Met à jour la variable dans la base de données
    app.css_variable_manager.update_variable(source_name, variable_name, value)
    
    # Met à jour toutes les variables dépendantes
    for dep_variable in dependencies:
        print("DEPVAR", dep_variable)
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


@app.route('/admin/update_input', methods=['POST'])
def update_input():
    """ Mise à jour des input d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')
    check = request.values.get('check')
    print("ZINPUT", key, value, check)

    if check:
        if check == "int":
            if value.isdigit():
                value = int(value)
            else:
                return display_toast(success=False, message="L'entrée doit être un nombre.")
        else:
            authorized_letters = ""
            if check == "welcome":
                authorized_letters = "PDH"
            elif check == "before_call":
                authorized_letters = "PDHAN"
            elif check == "after_call":
                authorized_letters = "PDHANMC"
            text_check = validate_and_transform_text(value, authorized_letters)
            
            if text_check["success"]:
                value = text_check["value"]
            else:
                return display_toast(success=False, message=text_check["value"])

    if key.startswith("ticket_"):
        escpos_text = convert_markdown_to_escpos(value)
        print("escpos_text", escpos_text)
        key_printer = key + "_printer"
        app.config[key_printer.upper()] = escpos_text
        config_option = ConfigOption.query.filter_by(config_key=key_printer).first()
        if config_option:
            print("escpos_text2", escpos_text)
            config_option.value_str = escpos_text
            db.session.commit()

    try:
        # MAJ Config
        app.config[key.upper()] = value

        # MAJ BDD
        config_option = ConfigOption.query.filter_by(config_key=key).first()        

        if config_option:
            if check == "int":
                config_option.value_int = value
            else:
                config_option.value_str = value
            db.session.commit()

            # pour les actions à faire après un changement
            special_functions_with_input(key)

            display_toast(success=True, message="Option mise à jour.")

            return "", 204
        else:
            display_toast(success=False, message="Option non trouvée.")
            return "", 204

    except Exception as e:
            app.logger.error(e)
            return display_toast(success=False, message=str(e))


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
    print(request.form)
    key = request.values.get('key')
    value = request.values.get('value')
    try:
        # MAJ BDD
        config_option = ConfigOption.query.filter_by(config_key=key).first()        
        # MAJ Config
        app.config[key.upper()] = value
        if config_option:
            config_option.value_str = value
            db.session.commit()
            call_function_with_select(key, value)         
            return display_toast(success=True)
        else:
            return display_toast(success=False, message="Option non trouvée.")
    except Exception as e:
            print(e)
            return display_toast(success=False, message=str(e))


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
    jobs_info = [{
        'id': job.id,
        'next_run_time': str(job.next_run_time),
        'function_name': job.func.__name__
    } for job in jobs]
    return render_template('/admin/database_schedule_tasks_list.html',
                        jobs_info=jobs_info)


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


@app.route('/call_specific_patient/<int:counter_id>/<int:patient_id>')
def call_specific_patient(counter_id, patient_id):

    validate_current_patient(counter_id)

    # Récupération du patient spécifique
    next_patient = Patient.query.get(patient_id)

    # Cas d'un patient appelé en même temps par un autre comptoir. On empêche double selection et retourne une info
    if next_patient.status != "standing":
        app.logger.info("Already called")
        send_app_notification(origin="patient_taken", data={"counter_id": counter_id, "patient": next_patient})
        return "Already called", 423
    
    if next_patient:
        print("Appel du patient :", patient_id, "au comptoir", counter_id)
        # Mise à jour du statut du patient
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()

        # mise à jour du comptoir:
        counter_become_active(counter_id)

        # Notifier tous les clients et mettre à jour le comptoir
        communikation("update_patient")

        text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], next_patient)
        communikation("update_screen", event="add_calling", data={"id": next_patient.id, "text": text})

        language_code = next_patient.language.code
        print("language_code_pour_audio", language_code)
        audio_url = generate_audio_calling(counter_id, next_patient, language_code)
        communikation("update_audio", event="audio", data=audio_url)

        return jsonify(next_patient.to_dict()), 200  
    else:
        print("Aucun patient trouvé avec l'ID :", patient_id)
    
    # Redirection vers la page du comptoir ou une autre page appropriée
    return "", 200


@app.route('/validate_patient/<int:counter_id>/<int:patient_id>', methods=['POST', 'GET'])
def validate_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("validation", patient_id)
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'ongoing'
        current_patient.timestamp_counter = datetime.now(time_tz)
        db.session.commit()

    communikation("update_patient")
    communikation("update_screen", event="remove_calling", data={"id": patient_id})

    if isinstance(current_patient, Patient):
        current_patient_pyside = current_patient.to_dict()

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
                communikation("update_screen", event="add_calling", data={"id": patient.id, "text": text})
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


@app.route('/validate_and_call_next/<int:counter_id>', methods=['POST', 'GET'])
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
        communikation("update_screen", event="add_calling", data={"id": next_patient.id, "text": text})
        
        return jsonify(next_patient.to_dict()), 200  

    # si pas de patient suivant, le comptoir devient inactif
    else:
        counter_become_inactive(counter_id)
        return '', 204


def validate_current_patient(counter_id):
    current_patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if current_patient:
        communikation("update_screen", event="remove_calling", data={"id": current_patient.id})
    
    # si patient actuel
    patients_at_counter = Patient.query.filter_by(counter_id=counter_id).all()
    if patients_at_counter:
        print("patient dans le comptoir")
        # Mise à jour du statut et du timestamp_end pour tous les patients au comptoir
        for patient in patients_at_counter:
            patient.status = 'done'
            patient.timestamp_end = datetime.now(time_tz)        
        db.session.commit()
    else:
        print("pas de patient")


@app.route('/pause_patient/<int:counter_id>/<int:patient_id>', methods=['POST', 'GET'])
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


# ---------------- FONCTIONS Généralistes > Communication avec Pyside ---------------- 

@app.route('/api/counters', methods=['GET'])
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
    
    command = ["ssh", "-i", os.path.expanduser("~/.ssh/id_rsa"), "-R", f"pharmaciesainteagathe:{port}:localhost:5000", "serveo.net"]
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
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('htmx/patients_queue.html', patients=patients)


@app.route('/pharmacists')
def pharmacists():
    all_pharmacists = Pharmacist.query.all()
    print("ALL", all_pharmacists)
    return render_template('pharmacists.html', pharmacists=all_pharmacists)


@app.route('/update_pharmacist/<int:pharmacist_id>', methods=['POST'])
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
            session['admin_colors'] = "flatly"
# Connecter le signal request_started à la fonction load_configuration
request_started.connect(load_colors, app)


# Fonctions attachées à app afin de pouvoir les appeler depuis un autre fichier via current_app
app.load_configuration = load_configuration
app.display_toast = display_toast
app.call_specific_patient = call_specific_patient
app.allowed_image_file = allowed_image_file
app.mail = mail
app.auto_calling = auto_calling
app.socketio = socketio
app.database = database
app.scheduler = scheduler

if __name__ == "__main__":

    # POUR L'instant RabbitMQ ne fonctionne pas avec Flask-SocketIO
    # VOir https://github.com/sensibill/socket.io-amqp pour faire le lien

    """
    if communication_mode == "rabbitmq":
        print("Starting RabbitMQ...", rabbitMQ_url)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        # Start threads for consuming different queues
        threading.Thread(target=consume_rabbitmq, args=(connection, channel, 'socket_update_patient', callback_update_patient)).start()
        threading.Thread(target=consume_rabbitmq, args=(connection, channel, 'socket_update_screen', callback_sound)).start()
        threading.Thread(target=consume_rabbitmq, args=(connection, channel, 'socket_admin', callback_admin)).start()
        threading.Thread(target=consume_rabbitmq, args=(connection, channel, 'socket_app_counter', callback_app_counter)).start()
        socketio.run(app, host='0.0.0.0', port=5000, debug=True) 
"""
    
    if communication_mode == "websocket":
        #eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 5000)), app)
        socketio.run(app, host='0.0.0.0', port=5000)   


    # Utilisez la variable d'environnement PORT si disponible, sinon défaut à 5000
    port = int(os.environ.get("PORT", 5000))
    # Activez le mode debug basé sur une variable d'environnement (définissez-la à True en développement)
    debug = os.environ.get("DEBUG", "False") == "True"

    # creation BDD si besoin et initialise certaines tables (Activités)
    def initialize_data():
        pass
            
    initialize_data()

    print("Starting Flask...")
    app.logger.info(f"Starting Flask on port {port} with debug={debug}")

    #app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)

    app.logger.info("Starting Flask app...")

