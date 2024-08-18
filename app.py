# TODO : on load comptoir verfier si on est en train de servir ou appeler qq (pb rechargement de page)
# POSSIBLE : laisser vide si comptoires vides et affichage uniquement si tous les comptoirs occupés
# TODO : Affichage d'un message en etranger si patient etranger "on going"
# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger
# TODO : Bouton Help ?

# deux lignes a appeler avant tout le reste (pour server Render)
import eventlet
eventlet.monkey_patch(thread=True, time=True)
from flask import Flask, render_template, request, redirect, url_for, session, current_app, jsonify, send_from_directory, Response, g, make_response, request, has_request_context, flash
import duckdb

from sqlalchemy.orm import scoped_session, sessionmaker, relationship, backref, session as orm_session, exc as sqlalchemy_exceptions, joinedload
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy import create_engine, ForeignKeyConstraint, UniqueConstraint, Sequence, func, CheckConstraint, and_, Boolean, DateTime, Column, Integer, String, ForeignKey
from flask_migrate import Migrate
from flask_mailman import Mail, EmailMessage
from flask_socketio import SocketIO
from datetime import datetime, timezone, date, time, timedelta
import time as tm
#from flask_babel import Babel
from gtts import gTTS
from werkzeug.utils import secure_filename
from flask_apscheduler import APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import qrcode
import json
import markdown2
import os
from queue import Queue, Empty
import logging
import subprocess
import threading
import socket
import pika

from urllib.parse import urlparse, urljoin
import random
import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError


from functools import partial

from flask_debugtoolbar import DebugToolbarExtension
from flask_security import Security, current_user, auth_required, hash_password, \
    SQLAlchemySessionUserDatastore, permissions_accepted, UserMixin, RoleMixin, AsaList, SQLAlchemyUserDatastore, login_required, lookup_identity, uia_username_mapper, verify_and_update_password, login_user
from sqlalchemy.ext.declarative import declarative_base
from flask_security.forms import LoginForm, BooleanField
from wtforms import StringField, PasswordField, HiddenField, SubmitField, MultipleFileField
from wtforms.validators import DataRequired
from flask_wtf import FlaskForm
import jwt

from models import db, Patient, Counter, Pharmacist, Activity, Button, Language, Text, AlgoRule, ActivitySchedule, ConfigOption, ConfigVersion, User, Weekday, TextTranslation, activity_schedule_link
from init_restore import init_default_buttons_db_from_json, init_default_options_db_from_json, init_default_languages_db_from_json, init_or_update_default_texts_db_from_json, init_update_default_translations_db_from_json, init_default_algo_rules_db_from_json, init_days_of_week_db_from_json, init_activity_schedules_db_from_json, clear_counter_table, restore_config_table_from_json, init_staff_data_from_json, restore_staff, restore_counters, init_counters_data_from_json, restore_schedules, restore_algorules, restore_activities, init_default_activities_db_from_json, restore_buttons, restore_databases
from utils import validate_and_transform_text, parse_time, convert_markdown_to_escpos
from routes.backup import backup_config_all, backup_staff, backup_counters, backup_schedules, backup_algorules, backup_activities, backup_buttons, backup_databases
from scheduler_functions import enable_buttons_for_activity, disable_buttons_for_activity
from bdd import init_database

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

class Config:
    SCHEDULER_API_ENABLED = True
    SECRET_KEY = 'your_secret_key'

    if database == "mysql":
        MYSQL_USER = 'admin'
        MYSQL_PASSWORD = '1Licornecornue'
        HOST = 'localhost'
        DB_NAME = 'queuedatabase'

        # MySQL Configuration
        SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/{DB_NAME}'
        SQLALCHEMY_DATABASE_URI_SCHEDULER = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/queueschedulerdatabase'
        # SQLALCHEMY_BINDS configuration to include MySQL
        SQLALCHEMY_BINDS = {
            'users': f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/userdatabase'
        }
            # Config MySQL

    elif database == "sqlite":
        SQLALCHEMY_DATABASE_URI = 'sqlite:///queuedatabase.db'
        SQLALCHEMY_DATABASE_URI_SCHEDULER = 'sqlite:///instance/queueschedulerdatabase.db'
        SQLALCHEMY_BINDS = {
            'users': 'sqlite:///userdatabase.db'
        }

    #SQLALCHEMY_DATABASE_URI = 'duckdb:///database.duckdb'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    ALLOWED_AUDIO_EXTENSIONS = {'wav', 'mp3'}
    AUDIO_FOLDER = '/static/audio'
    BABEL_DEFAULT_LOCALE = 'fr'  # Définit la langue par défaut
    # sécurité
    SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT", '146585145368132386173505678016728509634')
    SECURITY_REGISTERABLE = False
    SECURITY_RECOVERABLE = True
    SECURITY_USER_IDENTITY_ATTRIBUTES = [{"username": {"mapper": uia_username_mapper}}]
    REMEMBER_COOKIE_NAME = 'remember_me'
    REMEMBER_COOKIE_SECURE = True  # uniquement si HTTPS
    # mails
    MAIL_SERVER = "live.smtp.mailtrap.io"
    MAIL_PORT = 587 
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = "api"
    MAIL_PASSWORD = "6f04dfe4bbf9eaaf656f18a2698db1ec"
    MAIL_DEFAULT_SENDER = "hi@demomailtrap.com."
    # gallery
    GALLERIES_FOLDER = 'static/galleries'


app = Flask(__name__)

socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*") #, logger=True, engineio_logger=True
app.config.from_object(Config())
app.debug = True
mail = Mail(app)

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


@socketio.on('connect', namespace='/socket_update_patient')
def connect_general():
    logging.info("Client connected to update patient namespace")

@socketio.on('disconnect', namespace='/socket_update_patient')
def disconnect_general():
    logging.info("Client disconnected from general namespace")

@socketio.on('connect', namespace='/socket_update_screen')
def connect_screen():
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_update_screen')
def disconnect_screen():
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_admin')
def connect_admin():
    logging.info("Client connected to screen namespace")

@socketio.on('disconnect', namespace='/socket_admin')
def disconnect_admin():
    logging.info("Client disconnected from screen namespace")

@socketio.on('connect', namespace='/socket_patient')
def connect_patient():
    logging.info("Client connected to update patient namespace")

@socketio.on('disconnect', namespace='/socket_patient')
def disconnect_patient():
    logging.info("Client disconnected from patient namespace")

@socketio.on('connect', namespace='/socket_app_counter')
def connect_app_counter():
    logging.info("Client connected to app counter namespace")

@socketio.on('disconnect', namespace='/socket_app_counter')
def disconnect_app_counter():
    logging.info("Client disconnected from app counter namespace")

@socketio.on('connect', namespace='/socket_app_patient')
def connect_app_patient():
    logging.info("Client connected to test namespace")

@socketio.on('disconnect', namespace='/socket_app_patient')
def disconnect_app_patient():
    logging.info("Client disconnected from test namespace")

@socketio.on('connect', namespace='/socket_app_screen')
def connect_app_screen():
    logging.info("Client connected to test namespace")

@socketio.on('disconnect', namespace='/socket_app_screen')
def disconnect_app_screen():
    logging.info("Client disconnected from test namespace")

@socketio.on('connect', namespace='/socket_counter')
def connect_counter():
    logging.info("Client connected to counter namespace")

@socketio.on('disconnect', namespace='/socket_counter')
def disconnect_counter():
    logging.info("Client disconnected from counter namespace")


@app.route('/send_message', methods=['POST'])
def send_message():
    print("MESSAGEss")
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

# Gestion du scheduler / CRON
# Configuration du Scheduler
class ConfigScheduler:
    JOBS = []
    SCHEDULER_JOBSTORES = {
        'default': SQLAlchemyJobStore(url=app.config['SQLALCHEMY_DATABASE_URI_SCHEDULER'])  
    }
    SCHEDULER_API_ENABLED = True  # Permet d'activer l'API pour interroger le scheduler


# Pour le logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

"""
@app.teardown_appcontext
def remove_session(ex=None):
    db_session.remove()
    """
db.init_app(app) 
migrate = Migrate(app, db)  # Initialisation de Flask-Migrate
#babel = Babel(app)


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

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




@app.route('/login', methods=['GET', 'POST'])
def login():
    form = ExtendedLoginForm()
    print("LOGIN!!")
    # Récupérez 'next' de l'URL ou du formulaire
    next_url = request.args.get('next') or form.next.data
    
    # Assurez-vous que form.next.data est toujours défini
    form.next.data = next_url
    
    if form.validate_on_submit():
        user = form.user
        remember = form.remember.data 
        login_user(user, remember=remember)
        
        # Vérifiez si l'URL next est sûre avant de rediriger
        if not next_url or not is_safe_url(next_url):
            next_url = url_for('home')
        
        return redirect(next_url)
    
    return render_template('security/login.html', form=form)


class ExtendedLoginForm(LoginForm):
    username = StringField('Nom d\'utilisateur', [DataRequired()])
    password = PasswordField('Mot de passe', [DataRequired()])
    remember = BooleanField('Se souvenir de moi')
    email = None
    next = HiddenField()

    def validate(self, extra_validators=None):
        print("VALIDATE")
        self.user = User.query.filter_by(username=self.username.data).first()
        print(self.user)
        """if not super(ExtendedLoginForm, self).validate(extra_validators=extra_validators):
            print("Erreurs de validation:", self.errors)
            print("TRUC BIZARRE")
            return False"""

        self.user = User.query.filter_by(username=self.username.data).first()
        if not self.user:
            print("Unknown username")
            return False

        if not verify_and_update_password(self.password.data, self.user):
            print("Invalid password")
            return False
        print("OK !!!!")
        return home()

user_datastore = SQLAlchemyUserDatastore(db, User, None)
security = Security(app, user_datastore, login_form=ExtendedLoginForm)



# Permet de définir le type de fichiers autorisés pour l'ajout d'images
def allowed_image_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


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


@app.route('/')
@login_required
def home():
    return "Bonjour la pharmacie!"

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
            return redirect(url_for('security.login', next=request.url))
    elif request.path.startswith('/counter'):
        if app.config["SECURITY_LOGIN_COUNTER"] and not current_user.is_authenticated:
            return redirect(url_for('security.login', next=request.url))
    elif request.path.startswith('/display'):
        if app.config["SECURITY_LOGIN_SCREEN"] and not current_user.is_authenticated:
            return redirect(url_for('security.login', next=request.url))
    # on mets en code sur les pages patients, mais pas patient/phone
    elif request.path.startswith('/patient') and not request.path.startswith('/patient/phone'):
        if app.config["SECURITY_LOGIN_PATIENT"] and not current_user.is_authenticated:
            return redirect(url_for('security.login', next=request.url))
    elif request.path.startswith('/app'):
        if app.config["SECURITY_LOGIN_COUNTER"] and not (current_user.is_authenticated or is_valid_app_request):
            if is_valid_app_request:
                return jsonify({"error": "Unauthorized"}), 401
            else:
                return redirect(url_for('security.login', next=request.url))
        
    

# ---------------- Fin Sécurité ----------------------------


# --------   ADMIN   ---------

@app.route('/admin')
@login_required
def admin():
    return render_template('/admin/admin.html')


# -------- ADMIN -> Sécurité --------------------

@app.route('/admin/security')
def admin_security():
    return render_template('admin/security.html',
                        security_login_admin=app.config["SECURITY_LOGIN_ADMIN"],
                        security_login_counter=app.config["SECURITY_LOGIN_COUNTER"],
                        security_login_screen=app.config["SECURITY_LOGIN_SCREEN"],
                        security_login_patient=app.config["SECURITY_LOGIN_PATIENT"],
                        security_remember_duration=app.config["SECURITY_REMEMBER_DURATION"])

@app.route('/admin/security/table')
def display_security_table():
    users = User.query.all()
    return render_template('admin/security_htmx_table.html', users=users)

# affiche le formulaire pour ajouter une regle de l'algo
@app.route('/admin/security/add_user_form')
def add_user_form():
    return render_template('/admin/security_add_user_form.html')


@app.route('/admin/security/add_new_user', methods=['POST'])
def add_new_user():
    try:
        username = request.form.get('username')
        email = request.form.get("email")
        password1 = request.form.get("password1")
        password2 = request.form.get("password2")

        if not username:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoire")
            return display_security_table()
        if not password1 or not password2:
            display_toast(success=False, message="Les deux champs de mots de passe sont obligatoires")
            return display_security_table()
        if password1 != password2:
            display_toast(success=False, message="Les deux champs de mots de passe doivent être similaires")
            return display_security_table()

        new_user = User(
            username = username,
            email = email,
            password = hash_password(password1)
        )
        db.session.add(new_user)
        db.session.commit()

        communication('update_admin', data={"action": "delete_add_rule_form"})
        display_toast(success=True, message="Utilisateur ajouté avec succès")

        return display_security_table()

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        print(e)
        return display_algo_table()


@app.route('/admin/security/user_update/<int:user_id>', methods=['POST'])
def security_update_user(user_id):
    try:
        user = User.query.get(user_id)
        if user:
            if request.form.get('username') == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return ""
            elif request.form.get("password1") == "" or request.form.get("password2") == "":
                display_toast(success=False, message="Les deux mots de passe sont obligatoires")
            elif request.form.get("password1") != request.form.get("password2"):
                display_toast(success=False, message="Les deux mots de passe doivent être similaires")

            user.username = request.form.get('username', user.username)
            user.email = request.form.get('email', user.email)
            user.password = hash_password(request.form.get('password1', user.password))
            user.active = True
            user.confirmed_at=datetime.now()

            db.session.commit()

            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Règle introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500
    

# affiche la modale pour confirmer la suppression d'un membre
@app.route('/admin/security/confirm_delete_user/<int:user_id>', methods=['GET'])
def confirm_delete_user(user_id):
    user = User.query.get(user_id)
    return render_template('/admin/security_modal_confirm_delete_user.html', user=user)


# supprime une regle de l'algo
@app.route('/admin/security/delete_user/<int:user_id>', methods=['GET'])
def delete_user(user_id):
    print("DELETE")
    try:
        user = User.query.get(user_id)
        if not user:
            display_toast(success=False, message="Utilisateur non trouvé")
            return display_security_table()

        db.session.delete(user)
        db.session.commit()

        display_toast(success=True, message="Utilisateur supprimé")
        return display_security_table()

    except Exception as e:
        display_toast(success=False, message="erreur : " + str(e))
        return display_security_table()  
    

@app.route('/send_test_email')
def send_test_email():
    print("MAIL", mail)
    msg = EmailMessage(
        subject="Test Email",
        body="This is a test email sent from Flask-Mailman.",
        to=["arggg55@gmail.com"],
    )
    msg.send()
    return "Email sent!"


# --------- ADMIN -> fin sécurité -------------

# --------  ADMIN -> Queue ---------

@app.route('/admin/queue')
def queue():
    activities = Activity.query.all()
    return render_template('admin/queue.html', activities=activities)


# affiche le tableau des patients
@app.route('/admin/queue/table', methods=['POST'])
def display_queue_table():
    # Récupération des statuts en une liste pour tri ultérieur
    filters = [status for status, value in request.form.items() if value == 'true']
    print("Filters received:", filters)

    # Filtrage des patients en fonction des statuts sélectionnés
    if filters:
        patients = Patient.query.filter(Patient.status.in_(filters)).all()
    else:
        patients = Patient.query.all()

    return render_template('admin/queue_htmx_table.html', 
                            patients=patients, 
                            activities=Activity.query.all(), 
                            status_list=status_list,
                            counters=Counter.query.all())


# affiche la modale pour confirmer la suppression de toute la table patient
@app.route('/admin/database/confirm_delete_patient_table', methods=['GET'])
def confirm_delete_patient_table():
    return render_template('/admin/queue_modal_confirm_delete.html')


@app.route('/admin/database/clear_all_patients')
def clear_all_patients_from_db():
    print("Suppression de la table Patient")
    with app.app_context():  # Nécessaire pour pouvoir effacer la table via le CRON
        try:
            db.session.query(Patient).delete()
            db.session.commit()
            app.logger.info("La table Patient a été vidée")
            communication("update_patients")
            communikation("update_patient")
            announce_refresh()
            clear_counter_table(db, Counter, Patient)
            return display_toast(message="La table Patient a été vidée")
        except Exception as e:
            db.session.rollback()
            app.logger.error(str(e))
            return display_toast(success = False, message=str(e))


# mise à jour des informations d'un patient
@app.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
def update_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if patient:
            print(request.form)
            if request.form.get('call_number') == '':
                display_toast(success = False, message="Un numéro d'appel est obligatoire")
                return ""
            patient.call_number = request.form.get('call_number', patient.call_number)
            patient.status = request.form.get('status', patient.status)
            activity_id = request.form.get('activity_id', patient.activity)
            patient.activity = Activity.query.get(activity_id)
            counter_id = request.form.get('counter_id', patient.counter)
            patient.counter = Counter.query.get(counter_id)

            db.session.commit()

            clear_counter_table(db, Counter, Patient)

            announce_refresh()

            display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            display_toast(success = False, message="Membre de l'équipe introuvable")
            return ""

    except Exception as e:
            display_toast(success = False, message=str(e))
            app.logger(e)
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un patient particulier
@app.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
def confirm_delete_patient(patient_id):
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient)


# supprime un patient
@app.route('/admin/queue/delete_patient/<int:patient_id>', methods=['GET'])
def delete_patient(patient_id):
    print("id", patient_id)
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            return display_toast(success=False, message="Patient introuvable")

        db.session.delete(patient)
        db.session.commit()
        communication("update_patients")
        communikation("update_patient")
        announce_refresh()
        clear_counter_table(db, Counter, Patient)
        return display_toast()

    except Exception as e:
        app.logger(e)
        return display_toast(success=False, message=str(e))


# TODO PREVOIR "NONE" pour Activité ou équivalent 
@app.route('/admin/queue/create_new_patient_auto', methods=['POST'])
def create_new_patient_auto():
    activity = Activity.query.get(request.form.get('activity_id'))
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    communikation("update_patient")
    communication("update_patients")

    return "", 204



# --------  FIn de ADMIN -> Queue ---------


@app.route('/admin/update_switch', methods=['POST'])
def update_switch():
    """ Mise à jour des switches d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')
    print("key, value", key, value)
    try:
        # MAJ BDD
        config_option = ConfigOption.query.filter_by(key=key).first()
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
    

@app.route('/admin/update_input', methods=['POST'])
def update_input():
    """ Mise à jour des input d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')
    check = request.values.get('check')
    print("RESQUEST", request.form)
    
    if check:
        if check == "int":
            if value.isdigit():
                value = int(value)
            else:
                return display_toast(success=False, message="L'entrée doit être un nombre.")
        else:
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
        config_option = ConfigOption.query.filter_by(key=key_printer).first()
        if config_option:
            print("escpos_text2", escpos_text)
            config_option.value_str = escpos_text
            db.session.commit()

    try:
        # MAJ Config
        app.config[key.upper()] = value

        # MAJ BDD
        config_option = ConfigOption.query.filter_by(key=key).first()        

        if config_option:
            if check == "int":
                config_option.value_int = value
            else:
                config_option.value_str = value
            db.session.commit()
            
            return display_toast(success=True, message="Option mise à jour.")
        else:
            return display_toast(success=False, message="Option non trouvée.")
    except Exception as e:
            print(e)
            return display_toast(success=False, message=str(e))


@app.route('/admin/update_select', methods=['POST'])
def update_select():
    """ Mise à jour des selects d'options de l'application """
    print(request.form)
    key = request.values.get('key')
    value = request.values.get('value')
    try:
        # MAJ BDD
        config_option = ConfigOption.query.filter_by(key=key).first()        
        # MAJ Config
        app.config[key.upper()] = value
        if config_option:
            config_option.value_str = value
            db.session.commit()            
            return display_toast(success=True)
        else:
            return display_toast(success=False, message="Option non trouvée.")
    except Exception as e:
            print(e)
            return display_toast(success=False, message=str(e))


def call_function_with_switch(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un switch en plus de la sauvegarde"""
    if key == "cron_delete_patient_table_activated":
        if value == "true":
            scheduler_clear_all_patients()
        else:
            remove_scheduler_clear_all_patients()


def check_balises_before_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    print("call_function_with_input", value)
    
    #return validate_and_transform_text_for_before_validation(value)

def check_balises_after_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    print("call_function_with_input", value)
    
    #return validate_and_transform_text_for_after_validation(value)

# --------  ADMIN -> App  ---------

@app.route('/admin/app')
def admin_app():
    token_info, authorized = get_spotify_token()
    spotify_connected = authorized
    return render_template('/admin/app.html',
                            network_adress = app.config["NETWORK_ADRESS"],
                            numbering_by_activity = app.config["NUMBERING_BY_ACTIVITY"], 
                            announce_sound = app.config["ANNOUNCE_SOUND"],
                            pharmacy_name = app.config["PHARMACY_NAME"],
                            music_spotify = app.config["MUSIC_SPOTIFY"],
                            music_spotify_user = app.config["MUSIC_SPOTIFY_USER"],
                            music_spotify_key = app.config["MUSIC_SPOTIFY_KEY"],
                            spotify_connected=spotify_connected
    )

@app.route('/admin/app/update_numbering_by_activity', methods=['POST'])
def update_numbering_by_activity():
    new_value = request.values.get('numbering_by_activity')
    try:
        # Récupérer la valeur du checkbox à partir de la requête
        new_value = request.values.get('numbering_by_activity')
        config_option = ConfigOption.query.filter_by(key="numbering_by_activity").first()
        if config_option:
            config_option.value_bool = True if new_value == "true" else False
            db.session.commit()
            display_toast(success=True, message="Configuration mise à jour.")
            return ""
        else:
            display_toast(success=False, message="Configuration non trouvée.")
            return ""
    except Exception as e:
            display_toast(success=False, message=str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500
    

def spotify_authorized():
    print("spotify_authorized", app.config["MUSIC_SPOTIFY_USER"], app.config["MUSIC_SPOTIFY_KEY"])
    return SpotifyOAuth(client_id=app.config["MUSIC_SPOTIFY_USER"],
                            client_secret=app.config["MUSIC_SPOTIFY_KEY"],
                            redirect_uri=url_for('spotify_callback', _external=True),
                            scope='user-library-read user-read-playback-state user-modify-playback-state streaming')


@app.route('/spotify/login')
def spotify_login():
    clear_spotify_tokens()
    spotify_authorized()
    sp_oauth = SpotifyOAuth(
        client_id=app.config["MUSIC_SPOTIFY_USER"],
        client_secret=app.config["MUSIC_SPOTIFY_KEY"],
        redirect_uri=url_for('spotify_callback', _external=True),
        scope='user-library-read user-read-playback-state user-modify-playback-state streaming'
    )

    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

def clear_spotify_tokens():
    # Supprimez les informations de token de la session
    session.pop('token_info', None)
    session.modified = True

@app.route('/spotify/logout')
def spotify_logout():
    clear_spotify_tokens()
    return redirect(url_for('admin_app'))

@app.route('/spotify/callbacke')
def spotify_callbacke():
    sp_oauth = SpotifyOAuth(
        client_id=app.config["MUSIC_SPOTIFY_USER"],
        client_secret=app.config["MUSIC_SPOTIFY_KEY"],
        redirect_uri=url_for('spotify_callback', _external=True),
        scope='user-library-read user-read-playback-state user-modify-playback-state streaming'
    )

    try:
        token_info = sp_oauth.get_cached_token()

        session['token_info'] = token_info
        session.modified = True
    except SpotifyOauthError as e:
        print(f"Error obtaining token: {e}")
        clear_spotify_tokens()  # Supprimez les anciens tokens
        # Afficher une page d'erreur ou rediriger vers une page différente pour interrompre la boucle
        return redirect(url_for('error_page'))  # Remplacez 'error_page' par une page d'erreur appropriée

    return redirect(url_for('admin_app'))


def get_spotify_token():
    token_info = session.get('token_info', None)
    if not token_info:
        return None, False

    now = int(time.time())
    is_token_expired = token_info['expires_at'] - now < 60

    if is_token_expired:
        try:
            sp_oauth = SpotifyOAuth(
        client_id=app.config["MUSIC_SPOTIFY_USER"],
        client_secret=app.config["MUSIC_SPOTIFY_KEY"],
        redirect_uri=url_for('spotify_callback', _external=True),
        scope='user-library-read user-read-playback-state user-modify-playback-state streaming'
    )
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            session['token_info'] = token_info
        except SpotifyOauthError as e:
            print(f"Error refreshing token: {e}")
            clear_spotify_tokens()
            return None, False

    return token_info, True

@app.route('/error')
def error_page():
    return "Une erreur s'est produite avec votre authentification Spotify. Veuillez essayer de vous reconnecter.", 400

@app.route('/play_playlist', methods=['POST'])
def play_playlist():
    token_info, authorized = get_spotify_token()
    if not authorized:
        return redirect(url_for('spotify_login'))

    print("PLAYLISTES")
    print(request.values)
    sp = spotipy.Spotify(auth=token_info['access_token'])
    playlist_uri = request.form['playlist_uri']
    shuffle = request.form.get('shuffle') == 'true'  # Vérifie si la checkbox est cochée

    print("PLAYLIST_URI", playlist_uri)
    print("SHUFFLE", shuffle)

    # Envoie la commande à la page "announce" via WebSocket ou un autre mécanisme
    communikation("update_audio", 
                    event="spotify", 
                    data={
                        'playlist_uri': playlist_uri, 
                        'access_token': token_info['access_token'],
                        'shuffle': shuffle  # Ajoute l'option shuffle dans les données
                    })

    #socketio.emit('play_playlist', {'playlist_uri': playlist_uri}, namespace='/announce')

    return redirect(url_for('admin_music'))

# --------  FIn ADMIN -> App  ---------


    
"""
    token_info, authorized = get_spotify_token()
    spotify_connected = authorized
    if spotify_connected:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        playlists = sp.current_user_playlists()

        return render_template('/admin/music.html',
                                spotify_connected=spotify_connected, 
                                playlists=playlists['items'])

    else:
        return render_template('/admin/music.html',
                                spotify_connected=spotify_connected, 
                                playlists=[])
"""

# --------  ADMIN -> DataBase  ---------

@app.route('/admin/database')
def admin_database():
    cron_delete_patient_table_activated = app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"]
    return render_template('/admin/database.html',
                        cron_delete_patient_table_activated = cron_delete_patient_table_activated)


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


def scheduler_clear_all_patients():
    # vide la table patient à minuit
    try:
        scheduler.add_job(id='Clear Patient Table', func=clear_all_patients_from_db, trigger='cron', hour=00, minute=00)
        app.logger.info("Job 'Clear Patient Table' successfully added.")
        communication("update_admin", data="schedule_tasks_list")
        return True
    except Exception as e:
        app.logger.error(f"Failed to add job 'Clear Patient Table': {e}")
        return False


def remove_scheduler_clear_all_patients():
    try:
        # Supprime le job à l'aide de son id
        scheduler.remove_job('Clear Patient Table')
        app.logger.info("Job 'Clear Patient Table' successfully removed.")
        communication("update_admin", data="schedule_tasks_list")
        return True
    except Exception as e:
        app.logger.error(f"Failed to remove job 'Clear Patient Table': {e}")
        return False


def clear_old_patients_table():
    # Vérifie si la fonctionnalité est activée dans la configuration
    if app.config.get("CRON_DELETE_PATIENT_TABLE_ACTIVATED", False):
        # Obtenez la date actuelle en UTC
        today = datetime.now(timezone.utc).date()
        
        # Construisez la requête pour trouver tous les patients dont la date est antérieure à aujourd'hui
        old_patients = Patient.query.filter(Patient.timestamp < today)
        
        # Supprimez ces patients
        if old_patients.count() > 0:
            old_patients.delete(synchronize_session='fetch')
            db.session.commit()
            communikation("update_patient")
            communication("update_patients")
            app.logger.info(f"Deleted old patients not from today ({today}).")
    else:
        app.logger.info("Deletion of old patients is disabled.")


# --------  FIn ADMIN -> DataBase  ---------


# --------  ADMIN -> Staff   ---------

# base
@app.route('/admin/staff')
def staff():    
    return render_template('/admin/staff.html',
                            activities = Activity.query.all())

# affiche la table de l'équipe
@app.route('/admin/staff/table')
def display_staff_table():
    staff = Pharmacist.query.all()
    activities = Activity.query.all()
    return render_template('admin/staff_htmx_table.html', staff=staff, activities=activities)


# mise à jour des informations d'un membre
@app.route('/admin/staff/member_update/<int:member_id>', methods=['POST'])
def update_member(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if member:
            if request.form.get('name') == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return ""
            if request.form.get('initials') == '':
                display_toast(success=False, message="Les initiales sont obligatoires")
                return ""
            member.name = request.form.get('name', member.name)
            member.initials = request.form.get('initials', member.initials)
            member.language = request.form.get('language', member.language)
            activities_ids = request.form.getlist('activities')

            # Suppression des activités ajoutées pour éviter les erreur de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            member.activities = new_activities

            db.session.commit()
            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Membre de l'équipe introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="Erreur : " + str(e))
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un membre
@app.route('/admin/staff/confirm_delete/<int:member_id>', methods=['GET'])
def confirm_delete(member_id):
    staff = Pharmacist.query.get(member_id)
    return render_template('/admin/staff_modal_confirm_delete.html', staff=staff)


# supprime un membre de l'equipe
@app.route('/admin/staff/delete/<int:member_id>', methods=['GET'])
def delete_staff(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if not member:
            display_toast(success=False, message="Membre de l'équipe non trouvé")
            return display_staff_table()

        db.session.delete(member)
        db.session.commit()
        display_toast(success=True, message="Suppression réussie")
        return display_staff_table()

    except Exception as e:
        display_toast(success=False, message="Erreur : " + str(e))
        return display_staff_table()
    

# affiche le formulaire pour ajouter un membre
@app.route('/admin/staff/add_form')
def add_staff_form():
    activities = Activity.query.all()
    return render_template('/admin/staff_add_form.html', activities=activities)


# enregistre le membre dans la Bdd
@app.route('/admin/staff/add_new_staff', methods=['POST'])
def add_new_staff():
    try:
        name = request.form.get('name')
        initials = request.form.get('initials')
        language = request.form.get('language')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoire")
            return display_staff_table()
        if not initials:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Initiales obligatoires")
            return display_staff_table()

        new_staff = Pharmacist(
            name=name,
            initials=initials,
            language=language,
        )
        db.session.add(new_staff)
        db.session.commit()

        # Associer les activités sélectionnées avec le nouveau pharmacien
        for activity_id in activities_ids:
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_staff.activities.append(activity)
        db.session.commit()

        communication("update_admin", data={"action": "delete_add_staff_form"})
        display_toast(success=True, message="Membre ajouté avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_staff_form"></div>"""

        return f"{display_staff_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=True, message= "Erreur : " + str(e))
        return display_staff_table()




# --------  FIN  de ADMIN -> Staff   ---------

# --------  ADMIN -> Activity  ---------

# page de base
@app.route('/admin/activity')
def admin_activity():
    return render_template('/admin/activity.html')

# affiche le tableau des activités 
@app.route('/admin/activity/table')
def display_activity_table():
    activities = Activity.query.filter_by(is_staff=False).all()
    schedules = ActivitySchedule.query.all()
    return render_template('admin/activity_htmx_table.html',
                            activities=activities,
                            schedules=schedules)


# affiche le tableau des activités spécifique pour les membres de l'équipe
@app.route('/admin/activity/table_staff')
def display_activity_table_staff():
    activities = Activity.query.filter_by(is_staff=True).all()
    schedules = ActivitySchedule.query.all()
    staff = Pharmacist.query.all()
    return render_template('admin/activity_htmx_table.html',
                            staff=staff,
                            activities=activities,
                            schedules=schedules)


# mise à jour des informations d'une activité 
@app.route('/admin/activity/activity_update/<int:activity_id>', methods=['POST'])
def update_activity(activity_id):
    activity = Activity.query.get(activity_id)
    old_schedules = activity.schedules

    if activity:
        if request.form.get('name') == '':
            display_toast(success=False, message="Le nom est obligatoire")
            return ""
        if request.form.get('letter') == '':
            display_toast(success=False, message="La lettre est obligatoire")
            return ""
        activity.name = request.form.get('name', activity.name)
        activity.letter = request.form.get('letter', activity.letter)
        activity.inactivity_message = request.form.get('message', activity.inactivity_message)
        activity.notification = True if request.form.get('notification', activity.notification) == "true" else False

        # Mettre à jour les horaires
        schedule_ids = request.form.getlist('schedules')  # Cela devrait retourner une liste de IDs
        activity.schedules = [ActivitySchedule.query.get(int(id)) for id in schedule_ids]
        update_scheduler_for_activity(activity)
        
        # Si on a modifier les schedules, on met à jour le bouton
        if activity.schedules != old_schedules:
            update_bouton_after_scheduler_changed(activity)
        
        if request.form.get("staff_id"):
            activity.is_staff = True
            activity.staff = Pharmacist.query.get(int(request.form.get("staff_id")))
        else:
            activity.is_staff = False

        db.session.commit()
        display_toast(success=True, message="Activité ajoutée avec succès")
        return ""
    else:
        return display_toast(success=False, message="Activité introuvable")


def update_bouton_after_scheduler_changed(activity):
    """ Si on modifie le scheduler d'une activité, il faut vérifier où en est le bouton.
    Il faut donc éventuellement remettre le bouton en activité ou au contraire le rendre inactif."""
    # Obtenir l'heure actuelle et le jour actuel
    current_time = datetime.now().time()
    current_weekday = datetime.now().strftime('%A')  # Renvoie le jour de la semaine en anglais
    print(current_weekday, current_time)

    # Charger l'activité avec ses horaires et boutons associés
    activity = Activity.query.options(
        joinedload(Activity.schedules).joinedload(ActivitySchedule.weekdays),
        joinedload(Activity.buttons)
    ).filter_by(id=activity.id).first()

    if not activity:
        print(f"Activity with id {activity.id} not found.")
        return

    # Initialiser le drapeau d'activité à False
    is_activity_active = False

    # Parcourir les créneaux horaires de l'activité
    for schedule in activity.schedules:
        print(schedule)
        for weekday in schedule.weekdays:
            print(weekday.english_name)
            if weekday.english_name.lower() == current_weekday.lower():
                if schedule.start_time <= current_time <= schedule.end_time:
                    is_activity_active = True
                    break
        if is_activity_active:
            break

    # Mettre à jour les boutons associés à l'activité
    for button in activity.buttons:
        if button.is_active != is_activity_active:
            button.is_active = is_activity_active
            db.session.add(button)  # Ajouter le bouton à la session pour la mise à jour
            display_toast(success=True, message=f"Le bouton '{button.label} 'vient de changer d'activité.")

    db.session.commit()  # Sauvegarder les modifications dans la base de données

    app.logger.info(f"UPDATE BOUTON: Activity {activity.name} is_active={is_activity_active}")
    


# affiche la modale pour confirmer la suppression d'une activité
@app.route('/admin/activity/confirm_delete/<int:activity_id>', methods=['GET'])
def confirm_delete_activity(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity)


# affiche la modale pour confirmer la suppression d'une activité quand c'est un membre de l'équipe
@app.route('/admin/activity/confirm_delete/staff/<int:activity_id>', methods=['GET'])
def confirm_delete_activity_staff(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity, staff=True)


# supprime un membre de l'equipe
@app.route('/admin/activity/delete/<int:activity_id>', methods=['GET'])
def delete_activity(activity_id, staff=None):
    try:
        activity = Activity.query.get(activity_id)
        if not activity:
            display_toast(success=False, message="Activité non trouvée")
            return return_good_display_activity(staff)

        db.session.delete(activity)
        db.session.commit()
        display_toast(success=True, message="Activité supprimée avec succès")
        return return_good_display_activity(staff)

    except Exception as e:
        db.session.rollback()
        app.logger.error(str(e))
        display_toast(success=False, message="erreur : " + str(e))
        return return_good_display_activity(staff)


@app.route('/admin/activity/delete/staff/<int:activity_id>', methods=['GET'])
def delete_activity_staff(activity_id, staff=None):
    return delete_activity(activity_id, staff=True)

# affiche le formulaire pour ajouter un activité
@app.route('/admin/activity/add_form')
def add_activity_form():
    schedules = ActivitySchedule.query.all()
    return render_template('/admin/activity_add_form.html', schedules=schedules)


# affiche le formulaire pour ajouter un activité lié à un membre de l'équipe
@app.route('/admin/activity/add_staff_form')
def add_activity_staff_form():

    print(Pharmacist.query.all())
    return render_template('/admin/activity_add_form.html', 
                            schedules=ActivitySchedule.query.all(),
                            staff=Pharmacist.query.all())


# enregistre l'activité' dans la Bdd
@app.route('/admin/activity/add_new_activity', methods=['POST'])
def add_new_activity():
    try:
        name = request.form.get('name')
        letter = request.form.get('letter')
        schedule_ids = request.form.getlist('schedules')
        message = request.form.get('message')
        notification = True if request.form.get('notification') == "true" else False
        staff_id = request.form.get("staff_id")

        
        if not name:  # Vérifiez que les champs obligatoires sont remplis
            communication("update_admin", data='Nom obligatoire')
            return return_good_display_activity(staff_id)
        

        new_activity = Activity(
            name=name,
            letter=letter,
            inactivity_message=message,
            notification=notification
        )
        
        if staff_id:
            new_activity.is_staff = True
            new_activity.staff = Pharmacist.query.get(int(staff_id))
            
        db.session.add(new_activity)

        db.session.commit()

        for schedule_id in schedule_ids:
            schedule = ActivitySchedule.query.get(int(schedule_id))
            if schedule:
                new_activity.schedules.append(schedule)
        db.session.commit()

        for schedule_id in schedule_ids:
            schedule = ActivitySchedule.query.get(int(schedule_id))
            scheduler.add_job(func=update_button_presence, args=[new_activity.id, True, app],
                            trigger="cron", day_of_week='mon-sun', 
                            hour=schedule.start_time.hour, minute=schedule.start_time.minute,
                            id=f'activate_activity{new_activity.id}_schedule{schedule.id}')
            scheduler.add_job(func=update_button_presence, args=[new_activity.id, False, app],
                            trigger="cron", day_of_week='mon-sun', 
                            hour=schedule.end_time.hour, minute=schedule.end_time.minute,
                            id=f'desactivate_activity{new_activity.id}_schedule{schedule.id}')

        print("communication", staff_id)
        if staff_id:
            print("staff_id", staff_id)
            communication("update_admin", data={"action":"delete_add_activity_form_staff"})
        else:
            communication("update_admin", data={"action":"delete_add_activity_form"})

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_staff_form"></div>"""

        return f"{return_good_display_activity(staff_id)}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        return return_good_display_activity(staff_id)


def return_good_display_activity(staff):
    """ Sert uniquement à retourner le bon affichage entre activité et activité == équipier"""
    if staff:
        print("staff", staff)
        return display_activity_table_staff()
    else:
        return display_activity_table()

# affiche le tableau des plages horaires
@app.route('/admin/schedule/table')
def display_schedule_table():
    schedules = ActivitySchedule.query.all()
    weekdays = Weekday.query.all()
    return render_template('admin/schedule_htmx_table.html',
                            schedules=schedules,
                            weekdays=weekdays)


# mise à jour des informations d'une activité 
@app.route('/admin/schedule/schedule_update/<int:schedule_id>', methods=['POST'])
def update_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if schedule:
            schedule.name = request.form.get('name_schedule', schedule.name)
            start_time_str = request.form.get('start_time')
            end_time_str = request.form.get('end_time')
            schedule.start_time = parse_time(start_time_str) if start_time_str else schedule.start_time
            schedule.end_time = parse_time(end_time_str) if end_time_str else schedule.end_time

            # Mettre à jour les horaires
            weekdays_ids = request.form.getlist('weekdays')  # Cela devrait retourner une liste de IDs
            schedule.weekdays = [Weekday.query.get(int(id)) for id in weekdays_ids]

            db.session.commit()
            display_toast(success=True, message="Plage horaire mise à jour")

            # Mise à jour des boutons des activités qui dépendent du schedule
            activities_with_this_schedule = Activity.query.join(activity_schedule_link).filter(
                activity_schedule_link.c.schedule_id == schedule_id
            ).all()
            print("activities_with_this_schedule", activities_with_this_schedule)
            for activity in activities_with_this_schedule:
                update_bouton_after_scheduler_changed(activity)

            # mise à jour de la table activité si nouvelle plage horaire
            communikation("admin", event="refresh_activity_table")

            return ""
        else:
            display_toast(success=False, message="Plage horaire introuvable")
            return ""

    except Exception as e:
        app.logger.error(str(e))
        display_toast(success = False, message=str(e))
        return ""


# affiche le formulaire pour ajouter un activité
@app.route('/admin/schedule/add_form')
def add_schedule_form():
    weekdays = Weekday.query.all()
    return render_template('/admin/schedule_add_form.html', weekdays=weekdays)


# enregistre l'activité' dans la Bdd
@app.route('/admin/schedule/add_new_schedule', methods=['POST'])
def add_new_schedule():
    try:
        name = request.form.get('name_schedule')
        start_time_str = request.form.get('start_time')
        end_time_str = request.form.get('end_time')
        start_time = parse_time(start_time_str)
        end_time = parse_time(end_time_str)

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoire")
            return display_schedule_table()

        new_schedule = ActivitySchedule(
            name=name,
            start_time=start_time,
            end_time=end_time)

        db.session.add(new_schedule)

        db.session.commit()

        weekdays_ids = request.form.getlist('weekdays')  # Cela devrait retourner une liste de IDs
        for weekdays_id in weekdays_ids:
            weekday = Weekday.query.get(int(weekdays_id))
            if weekday:
                new_schedule.weekdays.append(weekday)
        db.session.commit()

        communication("update_admin", data={"action": "delete_add_schedule_form"})

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")
        
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_schedule_form"></div>"""

        return f"{display_schedule_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()
    


def update_button_presence(activity_id, is_present, app):
    with app.app_context():  # Crée un contexte d'application
        try:
            buttons = Button.query.order_by(Button.order).filter_by(activity_id=activity_id).all()
            for button in buttons:
                button.is_present = is_present
            db.session.commit()
            print(f"Buttons for activity {activity_id} set to {'present' if is_present else 'not present'}")
        except Exception as e:
            print(f"Failed to update button presence: {str(e)}")
            db.session.rollback()


# affiche la modale pour confirmer la suppression d'une plage horaire
@app.route('/admin/schedule/confirm_delete/<int:schedule_id>', methods=['GET'])
def confirm_delete_schedule(schedule_id):
    schedule = ActivitySchedule.query.get(schedule_id)
    return render_template('/admin/schedule_modal_confirm_delete.html', schedule=schedule)


# supprime un membre de l'equipe
@app.route('/admin/schedule/delete/<int:schedule_id>', methods=['GET'])
def delete_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if not schedule:
            display_toast(success=False, message="Plage horaire introuvable")
            return display_schedule_table()

        db.session.delete(schedule)
        db.session.commit()
        display_toast(success=True, message="Suppression réussie'")

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")

        return display_schedule_table()

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()




def update_scheduler_for_activity(activity):
    # Supprimer les anciens jobs pour cette activité
    job_id_disable_prefix = f"disable_{activity.name}_"
    job_id_enable_prefix = f"enable_{activity.name}_"

    for job in scheduler.get_jobs():
        if job.id.startswith(job_id_disable_prefix) or job.id.startswith(job_id_enable_prefix):
            scheduler.remove_job(job.id)

    # Fonction utilitaire pour vérifier si les horaires couvrent toute la journée
    def is_full_day(start_time, end_time):
        return start_time == time(0, 0) and end_time == time(23, 59)
    
    # Fonction utilitaire pour vérifier si les jours couvrent toute la semaine
    def is_full_week(weekdays):
        all_days = {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
        active_days = {day.abbreviation.strip().lower() for day in weekdays}
        return active_days == all_days
    
    # Pour chaque schedule, ajouter des jobs en fonction des horaires et des jours actifs
    for schedule in activity.schedules:
        full_day = is_full_day(schedule.start_time, schedule.end_time)
        full_week = is_full_week(schedule.weekdays)

        if full_day and full_week:
            # Ne pas créer de jobs car l'activité est toujours active
            print(f"Full day and full week: No jobs created for {activity.name}")
            continue
        
        # Si l'activité est active toute la journée mais pas tous les jours
        if full_day:
            start_day = min(schedule.weekdays, key=lambda x: x.id).abbreviation.strip().lower()
            end_day = max(schedule.weekdays, key=lambda x: x.id).abbreviation.strip().lower()

            scheduler.add_job(
                id=f"{job_id_enable_prefix}{start_day}",
                func='app:enable_buttons_for_activity',
                args=[activity.id],
                trigger='cron',
                day_of_week=start_day,
                hour=0,
                minute=0
            )
            scheduler.add_job(
                id=f"{job_id_disable_prefix}{end_day}",
                func='app:disable_buttons_for_activity',
                args=[activity.id],
                trigger='cron',
                day_of_week=end_day,
                hour=23,
                minute=59
            )
            print(f"Scheduled full-day jobs for activity {activity.name} from {start_day} to {end_day}")
            continue
        
        # Si l'activité a des plages horaires spécifiques
        for weekday in schedule.weekdays:
            day = weekday.abbreviation.strip().lower()

            scheduler.add_job(
                id=f"{job_id_enable_prefix}{day}_{schedule.start_time.strftime('%H%M')}",
                func='app:enable_buttons_for_activity',
                args=[activity.id],
                trigger='cron',
                day_of_week=day,
                hour=schedule.start_time.hour,
                minute=schedule.start_time.minute
            )
            scheduler.add_job(
                id=f"{job_id_disable_prefix}{day}_{schedule.end_time.strftime('%H%M')}",
                func='app:disable_buttons_for_activity',
                args=[activity.id],
                trigger='cron',
                day_of_week=day,
                hour=schedule.end_time.hour,
                minute=schedule.end_time.minute
            )
            print(f"Scheduled job from {schedule.start_time} to {schedule.end_time} on {day} for activity {activity.name}")










# -------- Fin de ADMIN -> Activity  ---------


# --------  ADMIN -> Algo  ---------

# page de base
@app.route('/admin/algo')
def admin_algo():
    algo_overtaken_limit = app.config['ALGO_OVERTAKEN_LIMIT']
    return render_template('/admin/algo.html',
                            algo_overtaken_limit=algo_overtaken_limit)

@app.route('/admin/algo/table')
def display_algo_table():
    rules = AlgoRule.query.all()
    activities = Activity.query.all()
    return render_template('admin/algo_htmx_table.html', rules=rules, activities=activities)

# affiche le formulaire activer ou desactiver l'algorithme
@app.route('/admin/button_des_activate_algo')
def button_des_activate_algo():
    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated= app.config['ALGO_IS_ACTIVATED'])

# active ou desactive l'algorithme, enregistre l'info, retourne les boutons
@app.route('/admin/algo/toggle_activation')
def toggle_activation():
    action = request.args.get('action', 'activate')
    is_activated = action == 'activate'
    
    app.config['ALGO_IS_ACTIVATED'] = is_activated
    algo_activated = ConfigOption.query.filter_by(key="algo_activate").first()
    algo_activated.value_bool = is_activated
    db.session.commit()

    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated=app.config['ALGO_IS_ACTIVATED'])


@app.route('/admin/algo/change_overtaken_limit', methods=['POST'])
def change_overtaken_limit():
    overtaken_limit = request.form.get('overtaken_limit')

    app.config['ALGO_OVERTAKEN_LIMIT'] = overtaken_limit
    try:
        algo_overtaken_limit = ConfigOption.query.filter_by(key="algo_overtaken_limit").first()
        algo_overtaken_limit.value_int = overtaken_limit
        db.session.commit()
        return display_toast()
    except Exception as e:
        print(e)
        return display_toast(success=False, message=str(e))


# affiche le formulaire pour ajouter une regle de l'algo
@app.route('/admin/algo/add_rule_form')
def add_rule_form():
    activities = Activity.query.all()
    return render_template('/admin/algo_add_rule_form.html', activities=activities)


# enregistre la regledans la Bdd
@app.route('/admin/algo/add_new_rule', methods=['POST'])
def add_new_rule():
    try:
        name = request.form.get('name')
        activity = Activity.query.get(request.form.get('activity_id'))
        priority_level = request.form.get('priority_level')
        min_patients = request.form.get('min_patients')
        max_patients = request.form.get('max_patients')
        max_overtaken = request.form.get('max_overtaken')
        start_time_str = request.form.get('start_time')            
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_str = request.form.get('end_time')
        end_time = datetime.strptime(end_time_str, "%H:%M").time()

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoire")
            return display_algo_table()

        new_rule = AlgoRule(
            name=name,
            activity = activity,
            priority_level = priority_level,
            min_patients = min_patients,
            max_patients = max_patients,
            max_overtaken = max_overtaken,
            start_time = start_time,
            end_time = end_time
        )
        db.session.add(new_rule)
        db.session.commit()

        communication('update_admin', data={"action": "delete_add_rule_form"})
        display_toast(success=True, message="Règle ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_rule_form"></div>"""

        return f"{display_algo_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        print(e)
        return display_algo_table()


# affiche la modale pour confirmer la suppression d'un membre
@app.route('/admin/algo/confirm_delete_rule/<int:rule_id>', methods=['GET'])
def confirm_delete_rule(rule_id):
    rule = AlgoRule.query.get(rule_id)
    return render_template('/admin/algo_modal_confirm_delete_rule.html', rule=rule)


# supprime une regle de l'algo
@app.route('/admin/algo/delete_rule/<int:algo_id>', methods=['GET'])
def delete_algo(algo_id):
    try:
        rule = AlgoRule.query.get(algo_id)
        if not rule:
            display_toast(success=False, message="Règle non trouvée")
            return display_algo_table()

        db.session.delete(rule)
        db.session.commit()

        display_toast(success=True, message="Règle supprimée")
        return display_algo_table()

    except Exception as e:
        display_toast(success=False, message="erreur : " + str(e))
        return display_algo_table()


@app.route('/admin/algo/rule_update/<int:rule_id>', methods=['POST'])
def update_algo_rule(rule_id):
    try:
        rule = AlgoRule.query.get(rule_id)
        if rule:
            if request.form.get('name') == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return ""

            rule.name = request.form.get('name', rule.name)
            activity = Activity.query.get(request.form.get('activity_id', rule.activity_id))
            rule.activity = activity
            rule.priority_level = request.form.get('priority_level', rule.priority_level)
            rule.min_patients = request.form.get('min_patients', rule.min_patients)
            rule.max_patients = request.form.get('max_patients', rule.max_patients)
            rule.max_overtaken = request.form.get('max_overtaken', rule.max_overtaken)
            start_time_str = request.form.get('start_time', rule.start_time.strftime("%H:%M"))            
            rule.start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time_str = request.form.get('end_time', rule.end_time.strftime("%H:%M"))
            rule.end_time = datetime.strptime(end_time_str, "%H:%M").time()

            db.session.commit()

            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Règle introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500


# -------- Fin de ADMIN -> Algo  ---------


# --------  ADMIN -> Counter  ---------

# page de base
@app.route('/admin/counter')
def admin_counter():
    return render_template('/admin/counter.html',
                            counter_order = app.config['COUNTER_ORDER'])


# affiche le tableau des counters 
@app.route('/admin/counter/table')
def display_counter_table():
    counters = Counter.query.all()
    activities = Activity.query.all()
    return render_template('admin/counter_htmx_table.html', counters=counters, activities = activities)


# mise à jour des informations d'un counter
@app.route('/admin/counter/counter_update/<int:counter_id>', methods=['POST'])
def update_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if counter:
            if request.form.get('name') == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return ""
            counter.name = request.form.get('name', counter.name)
            activities_ids = request.form.getlist('activities')

            # Suppression des activités ajoutées pour éviter les erreur de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            counter.activities = new_activities

            db.session.commit()
            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Comptoir introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return ""


# affiche la modale pour confirmer la suppression d'un comptoir
@app.route('/admin/counter/confirm_delete/<int:counter_id>', methods=['GET'])
def confirm_delete_counter(counter_id):
    counter = Counter.query.get(counter_id)
    print("counter", counter)
    return render_template('/admin/counter_modal_confirm_delete.html', counter=counter)


# supprime un comptoir
@app.route('/admin/counter/delete/<int:counter_id>', methods=['GET'])
def delete_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if not counter:
            display_toast(success=False, message="Comptoir introuvable")
            return display_counter_table()

        db.session.delete(counter)
        db.session.commit()

        display_toast(success=True, message="Comptoir supprimé")
        communikation("admin", event="refresh_counter_order")

        return display_counter_table()

    except Exception as e:
        display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_counter_table()


# affiche le formulaire pour ajouter un counter
@app.route('/admin/counter/add_form')
def add_counter_form():
    activities = Activity.query.all()
    return render_template('/admin/counter_add_form.html', activities=activities)


# enregistre le comptoir dans la Bdd
@app.route('/admin/counter/add_new_counter', methods=['POST'])
def add_new_counter():
    try:
        name = request.form.get('name')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Le nom est obligatoire")
            return display_activity_table()
        
        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order_counter = Counter.query.order_by(Counter.order.desc()).first()
        order = max_order_counter.order + 1 if max_order_counter else 0

        new_counter = Counter(
            name=name,
            order=order
        )
        db.session.add(new_counter)
        db.session.commit()


        # Associer les activités sélectionnées avec le nouveau pharmacien
        for activity_id in activities_ids:
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_counter.activities.append(activity)
        db.session.commit()

        communication("update_admin", data={"action": "delete_add_counter_form"})
        display_toast(success=True, message="Comptoir ajouté")
        communikation("admin", event="refresh_counter_order")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_counter_form"></div>"""

        return f"{display_counter_table()}{clear_form_html}"


    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_activity_table()


@app.route('/admin/counter/order_counter')
def order_counter_table():
    counters = Counter.query.order_by(Counter.order).all()
    return render_template('admin/counter_order_counters.html', counters=counters)


@app.route('/admin/counter/update_counter_order', methods=['POST'])
def update_counter_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, counter_id in enumerate(order_data):
            print(counter_id, index)
            counter = Counter.query.order_by(Counter.order).get(counter_id)
            print(counter)
            counter.order = index
        db.session.commit()
        display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        display_toast(success=False, message=f"Erreur: {e}")






# -------- fin de ADMIN -> Counter  ---------

# --------  ADMIN -> Page patient  ---------
@app.route('/admin/patient')
def admin_patient():
    buttons = Button.query.all()

    return render_template('/admin/patient_page.html', buttons=buttons,
                            page_patient_structure = app.config['PAGE_PATIENT_STRUCTURE'],
                            page_patient_disable_button = app.config['PAGE_PATIENT_DISABLE_BUTTON'],
                            page_patient_disable_default_message = app.config['PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE'],
                            page_patient_title = app.config['PAGE_PATIENT_TITLE'],
                            page_patient_subtitle = app.config['PAGE_PATIENT_SUBTITLE'],
                            page_patient_display_qrcode = app.config['PAGE_PATIENT_QRCODE_DISPLAY'],
                            page_patient_qrcode_web_page = app.config['PAGE_PATIENT_QRCODE_WEB_PAGE'],
                            page_patient_qrcode_data = app.config['PAGE_PATIENT_QRCODE_DATA'],
                            page_patient_print_ticket_display = app.config['PAGE_PATIENT_PRINT_TICKET_DISPLAY'],
                            page_patient_end_timer = app.config['PAGE_PATIENT_END_TIMER'],
                            ticket_header = app.config['TICKET_HEADER'],
                            ticket_message = app.config['TICKET_MESSAGE'],
                            ticket_footer = app.config['TICKET_FOOTER'],
                            printer_width = app.config['PRINTER_WIDTH']
                            )


# affiche le tableau des boutons 
@app.route('/admin/patient/button_table')
def display_button_table():
    buttons = Button.query.order_by(Button.order).all()
    activities = Activity.query.all()
    return render_template('admin/patient_page_htmx_buttons_table.html', buttons=buttons, activities = activities)

@app.route('/admin/patient/order_buttons')
def order_button_table():
    buttons = Button.query.order_by(Button.order).all()
    return render_template('admin/patient_page_order_buttons.html', buttons=buttons)


# affiche la liste des boutons pour le 
@app.route('/admin/patient/display_parent_buttons/<int:button_id>', methods=['GET'])
def display_children_buttons(button_id):
    buttons = Button.query.order_by(Button.order).filter_by(is_parent=True).all()
    button = Button.query.get(button_id)
    return render_template('admin/patient_page_button_display_children.html', buttons=buttons, button=button)


# mise à jour des informations d'un bouton
@app.route('/admin/patient/button_update/<int:button_id>', methods=['POST'])
def update_button(button_id):
    try:
        button = Button.query.order_by(Button.order).get(button_id)
        if button:
            # Récupérer l'ID de l'activité depuis le formulaire
            activity_id = request.form.get('activity')
            # GEstion du cas ou le bouton est un bouton parent
            if activity_id == "parent_button":
                button.is_parent = True
                button.activity = None
            else:
                # Récupérer l'instance de l'activité correspondante
                if activity_id:
                    activity = Activity.query.get(activity_id)
                    if activity:
                        button.activity = activity
                        button.is_parent = False
                    else:
                        print("Activité non trouvée")
                        return "Activité non trouvée", 404
                else:
                    # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                    button.activity = None
            
            parent_btn_id = request.form.get('parent_btn')
            if parent_btn_id:
                parent_button = Button.query.get(parent_btn_id)
                if parent_button:
                    button.parent_button = parent_button

            is_present = True if request.form.get('is_present') == "true" else False
            button.is_present = is_present

            button.label = request.form.get('label', button.label)

            button.shape = request.form.get('shape', button.shape)            

            db.session.commit()
            display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            display_toast(success=False, message="Membre de l'équipe introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500


@app.route('/admin/patient/update_button_order', methods=['POST'])
def update_button_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, button_id in enumerate(order_data):
            print(button_id, index)
            button = Button.query.order_by(Button.order).get(button_id)
            print(button)
            button.order = index
        db.session.commit()
        display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        display_toast(success=False, message=f"Erreur: {e}")


# affiche le formulaire pour ajouter un membre
@app.route('/admin/button/add_form')
def add_button_form():
    activities = Activity.query.all()
    parent_buttons = Button.query.filter_by(is_parent=True).all()
    return render_template('/admin/patient_button_add_form.html', 
                            activities=activities,
                            parent_buttons=parent_buttons)

@app.route('/admin/patient/add_new_button', methods=['POST'])
def add_new_button():
    try:
        activity_id = request.form.get('activity')
        
        # GEstion du cas ou le bouton est un bouton parent
        if activity_id == "parent_button":
            is_parent = True
            activity = None
        else:
            is_parent = False
            if activity_id:
                activity = Activity.query.get(activity_id)
                if activity:
                    activity = activity
                else:
                    print("Activité non trouvée")
                    return "Activité non trouvée", 404
            else:
                # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                activity = None
                
        parent_btn_id = request.form.get('parent_btn')
        if parent_btn_id:
            parent_button = Button.query.get(parent_btn_id)
        else:
            parent_button = None
                
        is_present = True if request.form.get('is_present') == "true" else False
        
        label = request.form.get('label')

        shape = request.form.get('shape')
        
        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order_button = Button.query.order_by(Button.order.desc()).first()
        order = max_order_button.order + 1 if max_order_button else 0
        

        new_button = Button(
            is_parent=is_parent,
            activity=activity,
            label=label,
            shape=shape,
            parent_button=parent_button,
            is_present=is_present,
            order=order
        )
        
        if not label:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Le nom est obligatoire")
            return display_button_table()        

        db.session.add(new_button)
        db.session.commit()

        communication("update_admin", data={"action": "delete_add_button_form"})
        display_toast(success=True, message="Bouton ajouté")
        communikation("admin", event="refresh_button_order")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_button_form"></div>"""

        return f"{display_button_table()}{clear_form_html}"


    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_activity_table()


# affiche la modale pour confirmer la suppression d'un patient
@app.route('/admin/patient/confirm_delete_button/<int:button_id>', methods=['GET'])
def confirm_delete_button(button_id):
    button = Button.query.get(button_id)
    return render_template('/admin/patient_page_button_modal_confirm_delete.html', button=button)


# supprime un bouton
@app.route('/admin/patient/delete_button/<int:button_id>', methods=['GET'])
def delete_button(button_id):
    try:
        button = Button.query.order_by(Button.order).get(button_id)
        if not button:
            display_toast(success=False, message="Bouton non trouvé")
            return display_button_table()

        db.session.delete(button)
        db.session.commit()
        display_toast(success=True, message="Bouton supprimé")

        communikation("admin", event="refresh_button_order")

        return display_button_table()

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_button_table()


@app.route('/upload_image/<int:button_id>', methods=['POST'])
def upload_image(button_id):
    """ Pas réussi à faire sans rechargement de page, car problème pour passer image sans formulaire """
    button = Button.query.order_by(Button.order).get(button_id)
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    if file and allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.static_folder, 'images/buttons',  filename)
        file.save(file_path)
        button.image_url = filename
        db.session.commit()
        # Retour à la page admin/patient
        return redirect("/admin/patient", code=302)
    return "Invalid file", 400


@app.route('/admin/patient/gallery_button_images/<int:button_id>', methods=['GET'])
def gallery_button_images(button_id):
    directory = os.path.join(current_app.static_folder, 'images/buttons')
    images = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    button = Button.query.order_by(Button.order).get(button_id)
    print(images)
    return render_template('/admin/patient_page_button_modal_gallery.html', images=images, button=button)


@app.route('/admin/patient/update_button_image_from_gallery', methods=['POST'])
def update_button_image_from_gallery():
    button_id = request.form.get('button_id')
    image_url = request.form.get('image')
    button = Button.query.order_by(Button.order).get(button_id)
    print(request.form)
    button.image_url = image_url
    db.session.commit()
    return """<img src="{{ url_for('static', filename='images/buttons/' ~ button.image_url) }}" alt="Button Image" style="width: 100px;">"""


@app.route("/admin/patient/delete_button_image/<int:button_id>", methods=['GET'])
def delete_button_image(button_id):
    button = Button.query.order_by(Button.order).get(button_id)
    button.image_url = None
    db.session.commit()
    return "<div>Pas d'image</div>"


@app.route("/admin/patient/print_ticket_test")
def print_ticket_test():
    text = "12345678901234567890123456789012345678901234567890"
    print(text)
    communikation(stream="app_patient", data=text, flag="print")
    communication("update_patient_app", data={"type": "print", "message": text})
    return "", 204


# -------- fin de ADMIN -> Page patient  ---------


# -------- ADMIN -> Page Announce  ---------

@app.route('/admin/announce')
def announce_page():
    return render_template('/admin/announce.html', 
                            announce_sound = app.config['ANNOUNCE_SOUND'],
                            announce_alert = app.config['ANNOUNCE_ALERT'],
                            announce_player = app.config['ANNOUNCE_PLAYER'],
                            announce_voice = app.config['ANNOUNCE_VOICE'],
                            anounce_style = app.config['ANNOUNCE_STYLE'],
                            announce_call_text=app.config['ANNOUNCE_CALL_TEXT'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],
                            announce_call_text_transition=app.config['ANNOUNCE_CALL_TEXT_TRANSITION'],
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_ongoing_text=app.config['ANNOUNCE_ONGOING_TEXT'],
                            announce_title=app.config['ANNOUNCE_TITLE'],
                            announce_title_size=app.config["ANNOUNCE_TITLE_SIZE"],
                            announce_subtitle=app.config['ANNOUNCE_SUBTITLE'],
                            announce_text_up_patients=app.config['ANNOUNCE_TEXT_UP_PATIENTS'],
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_down_patients=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS'],
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            announce_infos_display=app.config['ANNOUNCE_INFOS_DISPLAY'],
                            announce_infos_display_time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION']   ,
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],
                            announce_infos_mix_folders=app.config['ANNOUNCE_INFOS_MIX_FOLDERS'],
                            )


@app.route('/admin/announce/gallery_audio')
def gallery_audio():
    # Lister tous les fichiers wav dans le répertoire SOUND_FOLDER
    sounds = [f for f in os.listdir("static/audio/signals") if f.endswith('.wav') or f.endswith('.mp3')]
    print(sounds)
    return render_template('/admin/announce_audio_gallery.html', sounds=sounds)


@app.route('/sounds/<filename>')
def serve_sound(filename):
    return send_from_directory("static/audio/signals", filename)


@app.route('/admin/announce/audio/current_signal')
def current_signal():
    return render_template('/admin/announce_audio_current_signal.html',
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],)


@app.route('/admin/announce/audio/select_signal/<filename>')
def select_signal(filename):
    if filename:
        app.config['ANNOUNCE_ALERT_FILENAME'] = filename
        config = ConfigOption.query.filter_by(key='announce_alert_filename').first()
        print(config)
        config.value_str = filename
        db.session.commit()
    return "", 204


def allowed_audio_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_AUDIO_EXTENSIONS"]


@app.route('/admin/announce/audio/upload', methods=['POST'])
def upload_signal_file():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file and allowed_audio_file(file.filename):
        filename = file.filename
        file.save(os.path.join("static/audio/signals", filename))
    return redirect(url_for('gallery_audio'))


# -------- fin de ADMIN -> Page Announce  ---------


# -------- ADMIN -> Page INfos ---------

@app.route('/admin/info')
def admin_info():
    return render_template('/admin/gallery.html',
                            galleries = os.listdir(app.config['GALLERIES_FOLDER']))


@app.route('/admin/gallery/choose_gallery', methods=['POST'])
def choose_gallery(gallery_name="", checked=""):
    """ 
    Ajout ou suppression de la galerie via le panel d'admin (POST)
    Ou lors de la suppression du dossier via le panel admin (arguments de la fonction)    
    """
    print("choose_gallery")
    print(request.form)
    if request.method == 'POST':
        gallery_name = request.form.get('gallery_name')
        checked = request.form.get('checked')

    # Récupérer l'entrée existante ou créer une nouvelle
    config_option = ConfigOption.query.filter_by(key="announce_infos_gallery").first()
    if config_option is None:
        config_option = ConfigOption(key="announce_infos_gallery", value_str=json.dumps([]))
        db.session.add(config_option)

    # Charger les galeries existantes à partir de la chaîne JSON
    galleries = json.loads(config_option.value_str)

    print("CHECK", checked)
    message = ""    
    if checked == "true":
        message="Galerie selectionnée"
        if gallery_name not in galleries:
            galleries.append(gallery_name)
    else:
        message="Galerie deselectionnée"
        if gallery_name in galleries:
            galleries.remove(gallery_name)            

    # Enregistrer les galeries mises à jour
    config_option.value_str = json.dumps(galleries)
    db.session.commit()

    display_toast(success=True, message=message)

    return "", 200

class UploadForm(FlaskForm):
    photos = MultipleFileField('Upload Images')
    submit = SubmitField('Upload')

def get_images_with_dates(folder):
    try:
        files = os.listdir(folder)
        images = []
        for file in files:
            filepath = os.path.join(folder, file)
            date = tm.strftime('%Y-%m-%d %H:%M:%S', tm.localtime(os.path.getmtime(filepath)))
            images.append({'filename': file, 'date': date})
        return images
    except FileNotFoundError:
        print("File not found")
        return []
    

@app.route("/admin/gallery/list", methods=['GET'])
def gallery_list():
    galleries = os.listdir(app.config['GALLERIES_FOLDER'])
    config_option = ConfigOption.query.filter_by(key="announce_infos_gallery").first()
    if config_option:
        selected_galleries = json.loads(config_option.value_str)
    else:
        selected_galleries = []
    return render_template('admin/gallery_list_galleries.html', 
                            galleries=galleries,
                            selected_galleries=selected_galleries)


@app.route('/admin/gallery/<name>', methods=['GET', 'POST'])
def gallery(name):
    #if request.method == 'POST':
    #    for file in request.files.getlist('photos'):
    #        filename = secure_filename(file.filename)
    #        os.makedirs(os.path.join(app.config['GALLERIES_FOLDER'], name), exist_ok=True)
    #        file.save(os.path.join(app.config['GALLERIES_FOLDER'], name, filename))
    #images = get_images_with_dates(os.path.join(app.config['GALLERIES_FOLDER'], name))
    return render_template('/admin/gallery_manage.html', gallery=name)


@app.route('/admin/gallery/images_list/<name>', methods=['GET'])
def gallery_images_list(name):
    if not name:
        return "No gallery name provided", 400
    images = get_images_with_dates(os.path.join(app.config['GALLERIES_FOLDER'], name))
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@app.route('/admin/gallery/upload/<name>', methods=['POST'])
def upload_gallery(name):
    print(name)
    request.files.getlist('photos')
    for file in request.files.getlist('photos'):
        print("FILES", file)
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['GALLERIES_FOLDER'], name, filename))
    images = get_images_with_dates(os.path.join(app.config['GALLERIES_FOLDER'], name))
    print("images", images)
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@app.route('/admin/gallery/delete_image/<gallery>/<image>', methods=['DELETE'])
def delete_image(gallery, image):
    os.remove(os.path.join(app.config['GALLERIES_FOLDER'], gallery, image))
    images = get_images_with_dates(os.path.join(app.config['GALLERIES_FOLDER'], gallery))
    return render_template('admin/gallery_list_images.html', gallery=gallery, images=images)


@app.route('/admin/gallery/delete_gallery/<name>', methods=['DELETE'])
def delete_gallery(name):
    for image in os.listdir(os.path.join(app.config['GALLERIES_FOLDER'], name)):
        os.remove(os.path.join(app.config['GALLERIES_FOLDER'], name, image))
    os.rmdir(os.path.join(app.config['GALLERIES_FOLDER'], name))

    # on supprime la selection pour cette galerie si elle est selectionnée
    choose_gallery(gallery_name=name, checked="false")

    communikation("admin", event="refresh_gallery_list")

    return "", 200


@app.route('/admin/gallery/create_gallery', methods=['POST'])
def create_gallery():
    name = request.form.get('name')
    if name == "":
        display_toast(success=False, message="Le nom de la galerie doit être renseigné")
        return "", 400
    try:
        os.makedirs(os.path.join(app.config['GALLERIES_FOLDER'], name))
    except FileExistsError:
        display_toast(success=False, message="La galerie doit avoir un nom unique")
        return "", 400
    galleries = os.listdir(app.config['GALLERIES_FOLDER'])
    communikation("admin", event="display_new_gallery", data=name)
    return render_template('admin/gallery_list_galleries.html', galleries=galleries)



# --------  Fin ADMIN -> Page INfos ---------

# -------- ADMIN -> Page Phone ---------

@app.route('/admin/phone')
def admin_phone():
    phone_lines = []
    for line in range(1, 7):
        exec(f"phone_line{line} = app.config['PHONE_LINE{line}']"),
        phone_lines.append(eval(f"phone_line{line}"))
    print("PL", phone_lines)
    print("CENTER", app.config['PHONE_CENTER'])
    return render_template('/admin/phone.html',
                            phone_center = app.config['PHONE_CENTER'], 
                            phone_title=app.config['PHONE_TITLE'],
                            phone_lines=phone_lines)
                            


# --------  Fin ADMIN -> Page Phone ---------


@app.route('/patient_right_page_default')
def patient_right_page_default():
    print("default")
    return render_template('htmx/patient_right_page_default.html')



def generate_audio_calling(counter_number, next_patient):

    # voir pour la possibilité d'utiliser https://cloud.google.com/text-to-speech/ 
    # en version basique semble pas trop cher

    print("SOUND", app.config["ANNOUNCE_SOUND"])
    # Si on ne veux pas de son, on quitte
    if not app.config["ANNOUNCE_SOUND"]:
        return
    
    # Texte pour la synthèse vocale
    text_template = app.config["ANNOUNCE_CALL_SOUND"]
    text = replace_balise_announces(text_template, next_patient)
    print('TEXT', text)

    # choix de la voix
    if app.config["ANNOUNCE_VOICE"] == "fr-ca":
        lang = "fr"
        tld = "ca"
    elif app.config["ANNOUNCE_VOICE"] == "fr-fr":
        lang = "fr"
        tld = "fr"
    tts = gTTS(text, lang=lang, tld=tld)  # Utilisation de gTTS avec langue française

    # Chemin de sauvegarde du fichier audio
    audiofile = f'patient_{next_patient.call_number}.mp3'
    audio_path = os.path.join(app.static_folder, 'audio/annonces', audiofile)  # Enregistrement dans le dossier 'static/audio'

    # Assurer que le répertoire existe
    if not os.path.exists(os.path.dirname(audio_path)):
        os.makedirs(os.path.dirname(audio_path))

    # Sauvegarde du fichier audio
    tts.save(audio_path)

    # Envoi du chemin relatif via SSE
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)
    print("URL", audio_url)
    communikation("update_audio", event="audio", data=audio_url)

    #communication("update_audio", audio_source=audio_url)


@app.route('/call_specific_patient/<int:counter_id>/<int:patient_id>')
def call_specific_patient(counter_id, patient_id):
    print("specifique", patient_id)

    validate_current_patient(counter_id)

    # Récupération du patient spécifique
    next_patient = Patient.query.get(patient_id)
    
    if next_patient:
        print("Appel du patient :", patient_id, "au comptoir", counter_id)
        # Mise à jour du statut du patient
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()

        print("Bientot actif")
        # mise à jour du comptoir:
        counter_become_active(counter_id)

        # Notifier tous les clients et mettre à jour le comptoir
        communikation("update_patient")
        text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], next_patient)
        print("TEXTEEEE", text)
        communikation("update_screen", event="add_calling", data={"id": next_patient.id, "text": text})

        communication("update_patients")
        communication("update_counter", client_id=counter_id)

        # counter pyside
        next_patient_pyside = next_patient.to_dict()
        communication("update_counter_pyside", {"type":"my_patient", "data":{"counter_id": counter_id, "next_patient": next_patient_pyside}})

        generate_audio_calling(counter_id, next_patient)

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
        db.session.commit()

    communikation("update_patient")
    communikation("update_screen", event="remove_calling", data={"id": patient_id})

    communication("update_patients")
    communication("update_counter", client_id=counter_id)

    if isinstance(current_patient, Patient):
        current_patient_pyside = current_patient.to_dict()
    communication("update_counter_pyside", {"type":"my_patient", "data":{"counter_id": counter_id, "next_patient": current_patient_pyside}})  

    #return redirect(url_for('counter', counter_number=counter_number, current_patient_id=current_patient.id))
    return jsonify(current_patient.to_dict()), 200  


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

@app.route('/patient')
def patients_front_page():
    return render_template('patient/patient_front_page.html', 
                            page_patient_title=app.config['PAGE_PATIENT_TITLE'], 
                            page_patient_subtitle=app.config['PAGE_PATIENT_SUBTITLE'],
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"])


# affiche les boutons
@app.route('/patient/patient_buttons')
def patient_right_page():
    buttons = Button.query.order_by(Button.order).filter_by(is_present = True, parent_button_id = None).all()
    print("BUTTONS", buttons)
    max_length = 2 if buttons[0].shape == "square" else 4
    print("MAX_LENGTH", max_length)
    return render_template('patient/patient_buttons_left.html', 
                            buttons=buttons,
                            max_length=max_length,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"])


@app.route('/patients_submit', methods=['POST'])
def patients_submit():
    print("patients_submit")
    # Récupération des données du formulaire
    print('SUBMIT', request.form)
    if request.form.get('is_active')  == 'False':
        return display_activity_inactive(request)
    if request.form.get('is_parent')  == 'True':
        return display_children_buttons_for_right_page(request)
    else:
        return display_validation_after_choice(request)


def display_activity_inactive(request):
    activity = Activity.query.get(request.form.get('activity_id'))
    message = app.config['PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE']
    if activity.inactivity_message != "":
        message = activity.inactivity_message
    return render_template('patient/activity_inactive.html',
                            page_patient_disable_default_message=message,
                            default_subtitle=app.config['PAGE_PATIENT_SUBTITLE'],
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"],
                            page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"])


@app.route("/patient/default_subtitle")
def display_default_children_text():
    page_patient_subtitle = app.config['PAGE_PATIENT_SUBTITLE']
    return render_template('patient/patient_default_subtitle.html',
                            page_patient_subtitle=page_patient_subtitle)    

@app.route("/patients/cancel_children")
def cancel_children():
    return patients_front_page()

# affiche les boutons "enfants" de droite
def display_children_buttons_for_right_page(request):
    children_buttons = Button.query.order_by(Button.order).filter_by(is_present = True, parent_button_id = request.form.get('button_id')).all()
    print("children_buttons", children_buttons)
    max_length = 2 if children_buttons[0].shape == "square" else 4
    return render_template('patient/patient_buttons_left.html', 
                            buttons=children_buttons,
                            max_length=max_length,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"],
                            children=True)


# affiche la page de validation pour page gauche et droite
def display_validation_after_choice(request):
    activity_id = request.form.get('activity_id')
    print("reason", activity_id)

    # Si le bouton contient bien une activité
    if activity_id != "":
        activity = Activity.query.get(activity_id)
        #socketio.emit('trigger_valide_activity', {'activity': activity.id})
        return left_page_validate_patient(activity)
    

# page de validation (QR Code, Impression, Validation, Annulation)
def left_page_validate_patient(activity):
    call_number = get_next_call_number(activity)
    #new_patient = add_patient(call_number, activity)
    futur_patient = get_futur_patient(call_number, activity)
    print("futur_patient", futur_patient.id)
    image_name_qr = create_qr_code(futur_patient)
    text = f"{activity.name}"
    # rafraichissement des pages display et counter
    # envoye de data pour être récupéré sous forme de liste par PySide
    
    communikation("update_patient")

    communication("update_patients")

    return render_template('patient/patient_qr_right_page.html', 
                            image_name_qr=image_name_qr, 
                            text=text,
                            activity=activity,
                            futur_patient=futur_patient,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"])

@app.route('/patient/print_and_validate', methods=['POST'])
def print_and_validate():
    activity = Activity.query.get(request.form.get('activity_id'))
    new_patient = register_patient(activity)
    print("new_patient", new_patient)
    text = format_ticket_text(new_patient)
    print("text", text)

    if activity.notification:
        communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    communikation("app_patient", flag="print", data=text)
    communication("update_patient_app", data={"type": "print", "message": text})
    return patient_conclusion_page(new_patient.call_number)

def patient_validate_scan(activity_id):
    """ Fct appelée lors du scan du QRCode (validation) """
    activity = Activity.query.get(activity_id)
    new_patient = register_patient(activity)
    if activity.notification:
        communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    return new_patient

@app.route('/patient/scan_already_validate', methods=['POST'])
def patient_scan_already_validate():
    """ Fct appelée une fois la scan fait pour retourner la page de confirmation sur l'interface patient"""
    patient_call_number = request.form.get('patient_call_number')
    print("already scanned", patient_call_number)
    return patient_conclusion_page(patient_call_number)

@app.route('/patient/scan_and_validate', methods=['POST'])
def patient_scan_and_validate():
    """ Fct appelée si clic sur le bouton de validation """
    activity = Activity.query.get(request.form.get('activity_id'))
    new_patient = register_patient(activity)
    if activity.notification:
        communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    return patient_conclusion_page(new_patient.call_number)


def register_patient(activity):
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)

    
    auto_calling()

    communikation("update_patient")
    communication("update_patients")
    return new_patient

@with_app_context
def auto_calling():
    # si il y a des comptoirs en appel automatique on lance l'appel automatique

    print(app.config["AUTO_CALLING"])
    if len(app.config["AUTO_CALLING"]) > 0:
        counters = db.session.query(Counter).filter(
            Counter.id.in_(current_app.config["AUTO_CALLING"]),
            Counter.is_active == False,
            Counter.staff_id != None
        ).all()

        if app.config["COUNTER_ORDER"] == "order":
            counters = sorted(counters, key=lambda x: x.order)
        elif app.config["COUNTER_ORDER"] == "random":
            random.shuffle(counters)

        for counter in counters:
            if not counter.is_active:
                call_next(int(counter.id))
                counter.is_active = True
                db.session.commit()
                break

@app.route('/patient/cancel_patient')
def cancel_patient():
    return patient_right_page()


@app.route('/patient/conclusion_page')
def patient_conclusion_page(call_number):
    image_name_qr = f"qr_patient-{call_number}.png"
    return render_template('patient/conclusion_page.html',
                            call_number=call_number,
                            image_name_qr=image_name_qr,
                            page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"]
                            )


def format_ticket_text(new_patient):
    print("ticket_text", new_patient)
    text_list = [
        app.config['TICKET_HEADER_PRINTER'],
        app.config['TICKET_MESSAGE_PRINTER'],
        app.config['TICKET_FOOTER_PRINTER']
    ]
    print("text_list", text_list)
    combined_text = "\n".join(text_list)
    combined_text = replace_balise_phone(combined_text, new_patient)
    formatted_text = convert_markdown_to_escpos(combined_text, line_width=app.config["PRINTER_WIDTH"])
    return formatted_text
    


def add_patient(call_number, activity):
    """ CRéation d'un nouveau patient et ajout à la BDD"""
    # Création d'un nouvel objet Patient
    print('    call_number 2', call_number)
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(timezone.utc),
        status='standing'
    )    
    # Ajout à la base de données
    db.session.add(new_patient)
    db.session.commit()  # Enregistrement des changements dans la base de données

    return new_patient


def get_futur_patient(call_number, activity):
    """ CRéation d'un nouveau patient SANS ajout à la BDD
    Permet de simuler sa création pour pouvoir générer les infos utiles dans le QR Code"""
    # Création d'un nouvel objet Patient
    print('    call_number 2', call_number)
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(timezone.utc),
        status='standing'
    ) 
    return new_patient


# liste des patients en attente : Nécessaire pour être transmis à Pyside
def list_patients_standing():
    patients_standing = Patient.query.filter_by(status='standing').all()
    patients_data = [patient.to_dict() for patient in patients_standing]
    return patients_data


def get_next_call_number(activity):
    """ Récupérer le numéro d'appel en fonction de la méthode choisie"""
    numbering_by_activity = app.config.get('NUMBERING_BY_ACTIVITY', False)
    if numbering_by_activity:
        call_number = get_next_category_number(activity)
    else:
        call_number = get_next_call_number_simple()
    print("call_number", call_number)
    return call_number


def get_next_call_number_simple():
    # Obtenir le dernier patient enregistré aujourd'hui
    # BUG Connu : Si on passe de simple à par activité puis retour à simple -> repart à 1
    last_patient_today = Patient.query.filter(db.func.date(Patient.timestamp) == date.today()).order_by(Patient.id.desc()).first()
    if last_patient_today:
        if str(last_patient_today.call_number).isdigit():
            print(last_patient_today.call_number, type(last_patient_today.call_number))
            return last_patient_today.call_number + 1
        return 1
    return 1  # Réinitialiser le compteur si aucun patient n'a été enregistré aujourd'hui


# Générer le numéro d'appel en fonction de l'activité
def get_next_category_number(activity):
    # on utilise le code prévu de l'activité. Plusieurs activités peuvent avoir la même lettre
    letter_prefix = activity.letter
    today = date.today()

    # Compter combien de patients sont déjà enregistrés aujourd'hui avec le même préfixe de lettre
    today_count = Patient.query.filter(
        db.func.date(Patient.timestamp) == today,
        db.func.substr(Patient.call_number, 1, 1) == letter_prefix
    ).count()

    # Le prochain numéro sera le nombre actuel + 1
    next_number = today_count + 1

    return f"{letter_prefix}-{next_number}"


def create_qr_code(patient):
    print("create_qr_code")
    print(patient, patient.id, patient.call_number, patient.activity)
    if app.config['PAGE_PATIENT_QRCODE_WEB_PAGE']:
        if "SERVER_URL" not in app.config:
            set_server_url(app, request)
        data = f"{app.config['SERVER_URL']}/patient/phone/{patient.call_number}/{patient.activity.id}"
    else :
        template = app.config['PAGE_PATIENT_QRCODE_DATA']
        data = replace_balise_phone(template, patient)
    """patient_info = {
            "id": patient.id,
            "patient_number": patient.call_number,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": patient.activity.name
    }
        
    # Convertir les données en chaîne JSON
    data = json.dumps(patient_info)"""

    # Générer le QR Code
    img = qrcode.make(data)
    
    # Utiliser app.static_folder pour obtenir le chemin absolu vers le dossier static
    directory = os.path.join(current_app.static_folder, 'qr_patients')
    filename = f'qr_patient-{patient.call_number}.png'
    img_path = os.path.join(directory, filename)

    # Assurer que le répertoire existe
    if not os.path.exists(directory):
        os.makedirs(directory)  # Créer le dossier s'il n'existe pas

    # Enregistrement de l'image dans le dossier static
    img.save(img_path)

    return filename

@app.route('/patient/refresh')
def patient_refresh():
    """ Permet de rafraichir la page des patients pour effectuer des changements """
    print("patient_refresh")
    communikation("patient", event="refresh")
    communication("update_page_patient", data={"action": "refresh page"})
    return '', 204


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

@app.route('/counter/update_switch_auto_calling', methods=['POST'])
def update_switch_auto_calling():
    counter_id = request.values.get('counter_id')
    value = request.values.get('value')
    try:
        # MAJ BDD
        counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
        if counter:
            counter.auto_calling = True if value == "true" else False
            db.session.commit()
            # MAJ app.Config
            if value == "true":
                app.config["AUTO_CALLING"].append(counter.id)
            elif value == "false":
                app.config["AUTO_CALLING"].remove(counter.id)
            communikation("app_counter", event="change_auto_calling", data={"counter_id": counter_id, "value": value})
        else:
            app.logger.error("Counter not found")
    except Exception as e:
        app.logger.error(f'Erreur: {e}')
    return "", 204



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
    start_time = tm.time()
    print('validate_and_call_next', counter_id)

    current_patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if current_patient:
        communikation("update_screen", event="remove_calling", data={"id": current_patient.id})

    validate_current_patient(counter_id)
    print('patient_valide')
    # TODO Prevoir que ne renvoie rien
    next_patient = call_next(counter_id)

    # si pas de patient suivant, le comptoir devient inactif
    if not next_patient:
        counter_become_inactive(counter_id)
    else:
        counter_become_active(counter_id)

    print("prochain patient")

    communikation("update_patient")
    
    text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], next_patient)
    communikation("update_screen", event="add_calling", data={"id": next_patient.id, "text": text})

    communication("update_patients")
    communication("update_counter", client_id=counter_id)
    # counter pyside
    if isinstance(next_patient, Patient):
        next_patient_pyside = next_patient.to_dict()
    communication("update_counter_pyside", {"type":"my_patient", "data":{"counter_id": counter_id, "next_patient": next_patient_pyside}})
    end_time = tm.time()
    print('validate_and_call_next', end_time - start_time)

    return jsonify(next_patient.to_dict()), 200  


def validate_current_patient(counter_id):

    current_patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if current_patient:
        communikation("update_screen", event="remove_calling", data={"id": current_patient.id})
    
    # si patient actuel
    patients_at_counter = Patient.query.filter_by(counter_id=counter_id).all()
    if patients_at_counter :
        print("patient dans le comptoir")
        # Mise à jour du statut pour tous les patients au comptoir
        Patient.query.filter_by(counter_id=counter_id).update({'status': 'done'})
        db.session.commit()        
    else:
        print("pas de patient")


@app.route('/call_next/<int:counter_id>')
def call_next(counter_id):

    # CHoix du patient suivant pour un comptoir appelant
    if Patient.query.count() ==  0:
        return None
    
    next_patient = algo_choice_next_patient(counter_id)

    if next_patient:
        # Met à jour le statut du patient
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()
        #socketio.emit('trigger_patient_calling', {'last_patient_number': next_patient.call_number})
        # Optionnel: Ajoutez ici u système pour annoncer le patient au système audio ou un écran d'affichage

        generate_audio_calling(counter_id, next_patient)

    else:
        next_patient = None

    return next_patient


def algo_choice_next_patient(counter_id):

    counter = Counter.query.get(counter_id)

    # activités possible par ce pharmacien
    staff_activities = set(activity.id for activity in counter.staff.activities)

    # choix parmi les patients qui attendent 
    next_possible_patient = Patient.query.filter_by(status='standing')

    # choix parmi les patient qui correspondent aux activités du pharmacien
    next_possible_patient = next_possible_patient.filter(
        Patient.activity_id.in_(staff_activities)
    )

    # permet de voir si un patient s'est fait doubler plus que le nombre prévu
    # Si oui on bloque l'algo le temps de rétablir l'équilibre
    is_patient_waiting_too_long = Patient.query.filter(
        and_(Patient.status == 'standing',
                Patient.overtaken >= app.config["ALGO_OVERTAKEN_LIMIT"])).first()
    print("is_patient_waiting_too_long", is_patient_waiting_too_long)

    applicable_rules = None
    if app.config['ALGO_IS_ACTIVATED'] and not is_patient_waiting_too_long:
    # priorité à un type d'activité si un patient répond aux critère
    # Récupération des règles applicables
        current_day = datetime.now().weekday()
        current_time = datetime.now().time()
        print('current_time', current_time)
        print('current_day', current_day)
        number_of_patients = Patient.query.filter_by(status='standing').count()
        print('number_of_patients', number_of_patients)

        # cherche des regles applicables
        applicable_rules = AlgoRule.query.filter(
            AlgoRule.start_time <= current_time,  # L'heure actuelle doit être après l'heure de début
            AlgoRule.end_time >= current_time,    # et avant l'heure de fin
            AlgoRule.min_patients <= number_of_patients,  # Le nombre de patients doit être dans l'intervalle
            AlgoRule.max_patients >= number_of_patients,
            #AlgoRule.days_of_week.contains(current_day)  # Le jour actuel doit être inclus dans les jours valides
        )
        print("applicable_rules", applicable_rules)

        # S'il y a des regles applicables, regarde niveau par niveau les activités correspondantes
        if applicable_rules:
            for level in range(1, 6):
                rules_at_level = applicable_rules.filter(AlgoRule.priority_level == level)
                if rules_at_level:
                    print("level", level)
                    activity_ids_from_rules  = [rule.activity_id for rule in rules_at_level]
                    next_possible_patient_via_rules = next_possible_patient.filter(
                        Patient.activity_id.in_(activity_ids_from_rules)
                    )

                    print("next_possible_patient", next_possible_patient_via_rules.all())
                    # pour les patients qui rentrent dans les priorité on va regarder si on ne dépasse pas le nombre de patients à dépasser de la régle
                    if next_possible_patient_via_rules.all():
                        for patient in next_possible_patient_via_rules.all():
                            print("patient", patient)
                            patients_ahead_count = next_possible_patient.filter(
                                                                                        Patient.timestamp < patient.timestamp
                                                                                    ).count()
                            max_overtaken = min(rule.max_overtaken for rule in patient.activity.priority_rules)
                            print("max_overtaken", max_overtaken, patients_ahead_count)
                            if patients_ahead_count < max_overtaken:
                                next_possible_patient = next_possible_patient_via_rules
                                break

    print('next_possible_patient', next_possible_patient)
    
    # tri par date 
    next_patient = next_possible_patient.order_by(Patient.timestamp).first()

    if applicable_rules:
        patient_overtaken(next_patient)

    print('next_patient', next_patient)

    return next_patient


def patient_overtaken(next_patient):
    """ Met a jour le nombre de fois que le patient a été doublé"""
    patients_overtaken = Patient.query.filter(
        and_(
            Patient.status == 'standing',
            Patient.timestamp < next_patient.timestamp 
        )
    ).all() 

    for patient in patients_overtaken:
        patient.overtaken = patient.overtaken + 1
        print("patient overtaken", patient)
        db.session.commit()



@app.route('/pause_patient/<int:counter_id>/<int:patient_id>', methods=['POST', 'GET'])
def pause_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("pause_patient")
    print("p", patient_id, "c",counter_id)
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'done'
        db.session.commit()

    # le comptoir devient inactif
    counter_become_inactive(counter_id)
    
    communikation("update_patient")

    communication("update_patients")
    communication("update_counter_pyside", {"type":"my_patient", "data":{"counter_id": counter_id, "next_patient": None }})

    return jsonify({"id": None, "counter_id": counter_id}), 200  

def counter_become_inactive(counter_id):
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    counter.is_active = False
    db.session.commit()


def counter_become_active(counter_id):
    print("counter_become_activ")
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    print(counter, counter.is_active)
    if not counter.is_active:
        print('change')
        counter.is_active = True
        db.session.commit()


@app.route('/api/counter/is_patient_on_counter/<int:counter_id>', methods=['GET'])
def app_is_patient_on_counter(counter_id):
    """ Renvoie les informations du patient actuel au comptoir (pour le client) pour l'App (démarrage)"""
    patient = Patient.query.filter(
        Patient.counter.has(id=counter_id),
        Patient.status.in_(['ongoing', 'calling'])
        ).first()
    print("PATIENT!!!", patient)
    if patient:
        return jsonify(patient.to_dict()), 200
    else:
        return jsonify({"id": None, "counter_id": counter_id}), 200   



@app.route('/counter/patients_queue_for_counter/<int:counter_id>', methods=['GET'])
def patients_queue_for_counter(counter_id):
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('/counter/patients_queue_for_counter.html', patients=patients, counter_id=counter_id)


@app.route('/counter/is_staff_on_counter/<int:counter_id>', methods=['GET'])
def is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    # emet un signal pour provoquer le réaffichage de la liste des activités
    #socketio.emit("trigger_connect_staff", {})
    return render_template('counter/staff_on_counter.html', staff=counter.staff)


@app.route('/api/counter/is_staff_on_counter/<int:counter_id>', methods=['GET'])
def api_is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    if counter.staff:
        print("counter", counter.staff)
        return jsonify({"staff": counter.staff.to_dict()}), 200
    else:
        return "", 204 


@app.route('/app/counter/auto_calling', methods=['POST'])
def app_auto_calling():
    print("COUNTER AUTOCALLING", request.values)
    counter_id = request.form.get('counter_id')
    print("COUNTER ID", counter_id)
    counter = Counter.query.get(counter_id)

    if request.form.get('action') is None:
        return jsonify({"status": counter.auto_calling}), 200 # 

    auto_calling_action = True if request.form.get('action') == "activate" else False
    print("COUNTER AUTOCALLING", request.values)
    counter = Counter.query.get(counter_id)
    print("counter", counter.auto_calling)
    try:
        counter.auto_calling = auto_calling_action
        db.session.commit()
        # MAJ app.Config
        if auto_calling_action:
            app.config["AUTO_CALLING"].append(counter.id)
        else:
            app.config["AUTO_CALLING"].remove(counter.id)

        communikation("counter", event="refresh_auto_calling")

        return jsonify({"status": counter.auto_calling}), 200 # 
    except Exception as e:
        app.logger.error(f'Erreur: {e}')
        return e, 500


@app.route('/app/counter/remove_staff', methods=['POST'])
def app_remove_counter_staff():
    print("deconnction")
    remove_counter_staff()
    return '', 200

@app.route('/counter/remove_staff', methods=['POST'])
def web_remove_counter_staff():
    return remove_counter_staff()

def remove_counter_staff():
    counter = Counter.query.get(request.form.get('counter_id')) 
    counter.staff = None
    db.session.commit()

    # mise à jour des boutons
    communikation("counter", event="update buttons")
    return is_staff_on_counter(request.form.get('counter_id'))

@app.route('/app/counter/update_staff', methods=['POST'])
def app_update_counter_staff():
    return update_counter_staff()

@app.route('/counter/update_staff', methods=['POST'])
def web_update_counter_staff():
    return update_counter_staff()

def update_counter_staff():
    print("RECONNEXION ")
    print(request.form)
    counter = Counter.query.get(request.form.get('counter_id'))  
    initials = request.form.get('initials')
    # la demande vient elle de l'App en mode réduit ?
    app = request.form.get("app") == "True"
    staff = Pharmacist.query.filter(func.lower(Pharmacist.initials) == func.lower(initials)).first()
    if staff:
        # si demande de déconnexion
        if request.form.get('deconnect').lower() == "true":
            # deconnexion de tous les postes
            deconnect_staff_from_all_counters(staff)
        # Ajout du membre de l'équipe au comptoir        
        counter.staff = staff
        db.session.commit()

        # mise à jour des boutons
        communikation("counter", event="update buttons")
        # On rappelle la base de données pour être sûr que bonne personne au bon comptoir
        if app:
            return api_is_staff_on_counter(request.form.get('counter_id'))
        else:
            return is_staff_on_counter(request.form.get('counter_id'))

    # Si les initiales ne correspondent à rien
    # on déconnecte l'utilisateur précedemement connecté
    counter.staff = None
    db.session.commit()
    # mise à jour des boutons
    communikation("counter", event="update buttons")
    # on affiche une erreur à la place du nom
    if app:
        return "", 204
    else:
        return render_template('counter/staff_on_counter.html', staff=False)


def deconnect_staff_from_all_counters(staff):
    """ Déconnecte le membre de l'équipe de tous les comptoirs """
    print("deconnecte")
    for counter in Counter.query.all():
        if counter.staff == staff:
            counter.staff = None
            db.session.commit()
            #socketio.emit("trigger_disconnect_staff", {})
            # mise à jour des boutons
            communikation("counter", event="update buttons")


@app.route('/counter/list_of_activities', methods=['POST'])
def list_of_activities():
    activities = Activity.query.all()
    staff_id = request.form.get('staff_id')
    if staff_id == "0":
        # TODO Créer un user "Anonyme" ????
        # si personne au comptoir, on affiche toutes les activités
        staff_activities_ids = [activity.id for activity in activities]

    else:     
        staff = Pharmacist.query.get(staff_id)
        # on renvoie les activités du membre de l'équipe pour les cocher dans la liste
        staff_activities_ids = [activity.id for activity in staff.activities]

    return render_template('counter/counter_list_of_activities.html', activities=activities, staff_activities_ids=staff_activities_ids)


@app.route("/counter/select_patient/<int:counter_id>/<int:patient_id>", methods=['GET'])
def counter_select_patient(counter_id, patient_id):
    """ Appeler lors du choix d'un patient spécifique au comptoir """
    print("counter_select_patient", counter_id, patient_id)
    call_specific_patient(counter_id, patient_id)

    communikation("update_patient")
    communication("update_patients")
    communication("update_counter", client_id=counter_id)    

    return '', 204


@app.route("/app/counter/paper_add", methods=['POST'])
def app_paper_add():
    if request.form.get('action') is None:
        return jsonify({"status": app.config["ADD_PAPER"]}), 200 # 
    else:
        add_paper_action = True if request.form.get('action') == "activate" else False
        try:
            config_option = ConfigOption.query.filter_by(key="add_paper").first()
            config_option.value_bool = add_paper_action
            db.session.commit()
            app.config["ADD_PAPER"] = add_paper_action

            communikation("counter", event="paper")
            communikation("app_counter", event="paper")

        except Exception as e:
            print(e)


@app.route("/counter/paper_add")
def counter_paper_add():
    return render_template('counter/paper_add.html',
                            add_paper=app.config["ADD_PAPER"])

@app.route("/counter/paper_add/<int:add_paper>", methods=['GET'])
def action_add_paper(add_paper):
    try:
        print("action_add_paper", add_paper)
        config_option = ConfigOption.query.filter_by(key="add_paper").first()
        config_option.value_bool = add_paper
        db.session.commit()
        app.config["ADD_PAPER"] = add_paper
        communikation("counter", event="paper")
        communikation("app_counter", event="paper")
        return counter_paper_add()
    except Exception as e:
        print(e)

# ---------------- FIN  PAGE COUNTER FRONT ----------------


# ---------------- PAGE AnnoNces FRONT ----------------


@app.route('/display')
def display():
    app.logger.debug("start display")
    # TODO verifier qu'existe
    return render_template('/announce/announce.html', 
                            current_patients=current_patients,
                            announce_infos_display= app.config['ANNOUNCE_INFOS_DISPLAY'],
                            announce_title=app.config['ANNOUNCE_TITLE'] ,
                            announce_subtitle=app.config['ANNOUNCE_SUBTITLE'],
                            announce_text_up_patients=app.config['ANNOUNCE_TEXT_UP_PATIENTS'],
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_down_patients=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS'],
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            call_patients = patient_list_for_init_display(),
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_title_size=app.config['ANNOUNCE_TITLE_SIZE'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],)

def patient_list_for_init_display():
    """ Création de la liste de patients pour initialiser l'écran d'annonce"""
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    announce_call_text = ConfigOption.query.filter_by(key="announce_call_text").first().value_str
    call_patients = []
    for patient in patients:
        print("PATATOR", patient)
        call_patient = {
            'id': patient.id,
            'text': replace_balise_announces(announce_call_text, patient)
        }
        call_patients.append(call_patient)
    return call_patients

@app.route('/announce/patients_ongoing')
def patients_ongoing():
    announce_ongoing_text = app.config['ANNOUNCE_ONGOING_TEXT']
    patients = Patient.query.filter_by(status='ongoing').order_by(Patient.counter_id).all()
    ongoing_patients = []
    for patient in patients:
        print("PATATE", patient)
        ongoing_patients.append(replace_balise_announces(announce_ongoing_text, patient))
    return render_template('announce/patients_ongoing.html', ongoing_patients=ongoing_patients)


def replace_balise_announces(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)"""
    print(template)
    print("replace_balise_announces", template, patient)
    try:
        if patient.counter.staff.name:
            return template.format(N=patient.call_number, C=patient.counter.name, M=patient.counter.staff.name)
        else:
            template = "Patient {N} : {C}"
            return template.format(N=patient.call_number, C=patient.counter.name)
    except AttributeError:
        return f"Erreur {patient.call_number}. Demandez à notre personnel"


def replace_balise_phone(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)"""
    print("replace_balise_announces", template, patient)
    return template.format(P=app.config["PHARMACY_NAME"],
                            N=patient.call_number, 
                            A=patient.activity.name, 
                            D=date.today().strftime("%d/%m/%y"),
                            H=datetime.now().strftime("%H:%M"))


@app.route('/announce/init_gallery')
def announce_init_gallery():
    """ Création de la liste des images pour la galerie"""
    app.logger.debug("Init gallery")
    
    # Récupérer la liste des galeries sélectionnées, si rien on envoie une liste vide
    config_option = ConfigOption.query.filter_by(key="announce_infos_gallery").first()
    if config_option:
        announce_infos_galleries = json.loads(config_option.value_str)
    else:
        announce_infos_galleries = []

    app.logger.debug("announce_infos_galleries : " + str(announce_infos_galleries))
    
    images = []
    for gallery in announce_infos_galleries:
        try:
            image_dir = os.path.join(app.static_folder, "galleries", gallery)
            images.extend([url_for('static', filename=f"galleries/{gallery}/{image}") for image in os.listdir(image_dir) if image.endswith((".png", ".jpg", ".jpeg"))])
        except FileNotFoundError:
            app.logger.error(f"Gallery {gallery} not found")


    # Mélange des images si l'option est active
    if app.config.get("ANNOUNCE_INFOS_MIX_FOLDERS", False):
        print(images)
        images.sort(key=lambda x: os.path.basename(x))
        print(images)
    
    return render_template('announce/gallery.html', images=images,
                            time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION'],
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],)


@app.route('/announce/refresh')
def announce_refresh():
    """ Permet de rafraichir la page des annonces pour appliquer les changements """
    communikation("update_screen", event="refresh")
    communication("update_announce")
    return '', 204

# ---------------- FIN  PAGE AnnoNces FRONT ----------------



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

def communikation(stream, data=None, flag=None, event="update", client_id=None):
    """ Effectue la communication avec les clients """
    print("communikation", communication_mode, data, event)
    if communication_mode == "websocket":
        #communication_websocket(stream=f"socket_{stream}", data=data)
        if stream == "update_patient":
            print("UPDATE:::")
            patients = create_patients_list_for_pyside()
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
                    print("ANNOUNCE_PLAYER", app.config["ANNOUNCE_PLAYER"])
                    if app.config["ANNOUNCE_PLAYER"] == "web":
                        print("websouns", audio_path)
                        communication_websocket(stream="socket_update_screen", event="audio", data=audio_path)
                    else:
                        communication_websocket(stream="socket_app_screen", data=audio_path, flag="sound")
                if app.config["ANNOUNCE_PLAYER"] == "web":
                    communication_websocket(stream="socket_update_screen", data=data, event="audio")
                else:
                    communication_websocket(stream="socket_app_screen", data=data, flag="sound")
        else:
            print("basique")
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
    print('communication_rabbitmq')
    print("queue", queue)

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
        return f"Failed to send message: {e}", 500


def communication(stream, data=None, client_id=None, audio_source=None):
    """ Effectue la communication avec les clients """
    return None
    message = {"type": stream, "data": ""}
    
    if stream == "update_patients":
        for client in update_patients:
            client.put(message)
        for client in update_patient_pyside:
            patients = create_patients_list_for_pyside()
            client.put(json.dumps({"type": "patient", "list": patients}))
            if data and data["type"] == "notification_new_patient":
                client.put(json.dumps(data))
    elif stream == "update_counter_pyside":
        for client in update_patient_pyside:
            client.put(json.dumps(data))
    elif stream == "update_announce":
        for client in update_announce:
            client.put(message)
    elif stream == "update_page_patient":
        for client in update_page_patient:
            client.put(json.dumps(data))
    elif stream == "update_patient_app":
        app.logger.debug(f"update_patient_app: {data} - {update_patient_app}")
        for client in update_patient_app:
            app.logger.debug(f"client {client}")
            client.put(json.dumps(data))
    elif stream == "update_admin":
        for client in update_admin:
            client.put(json.dumps(data))
    elif stream == "update_counter":
        if client_id in counter_streams:
            counter_streams[client_id].put(message)
    elif stream == "update_audio":
        if app.config["ANNOUNCE_ALERT"]:
            signal_file = app.config["ANNOUNCE_ALERT_FILENAME"]
            audio_path = url_for('static', filename=f'audio/signals/{signal_file}', _external=True)
            message["data"] = {"audio_url": audio_path}
            if app.config["ANNOUNCE_PLAYER"] == "web":
                for client in play_sound_streams:
                    client.put(json.dumps(message))
            else:
                for client in update_screen_app:
                    client.put(json.dumps(message))
        message["data"] = {"audio_url": audio_source}
        if app.config["ANNOUNCE_PLAYER"] == "web":
            for client in play_sound_streams:
                client.put(json.dumps(message))
        else:
            for client in update_screen_app:
                client.put(json.dumps(message))

@app.route('/api/patients_list_for_pyside', methods=['GET'])
def create_patients_list_for_pyside():
    patients = Patient.query.filter_by(status="standing").all()
    patients_list = [{"id": patient.id, "call_number": patient.call_number, "activity_id": patient.activity_id, "activity": patient.activity.name} for patient in patients]
    return patients_list


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


@app.route('/patient/phone/<patient_id>/<int:activity_id>', methods=['GET'])
def phone_patient(patient_id, activity_id):
    """ 
    Page pour téléphone appelé lors du scan
    Affiche la structure puis les infos spécifiques au patient sont chargées lors du ping en htmx
    On regarde s'il y a un cookie déja placé (par ping). Si c'est le cas et que le numéro est différent c'est qu'il y a un nouvel enregistrement
    Dans ce cas on efface le cookie, sinon c'est un rafraichissement de la page et donc on le laisse.
    """
    app.logger.debug(f"TITLE2 {app.config['PHONE_TITLE']}")
    if request.cookies.get('patient_call_number') != patient_id:
        if request.cookies.get('patient_id') != patient_id:
            response = make_response(render_template('/patient/phone.html', 
                                                    patient_id=patient_id, 
                                                    activity_id=activity_id,
                                                    phone_title=app.config['PHONE_TITLE']))
            response.set_cookie('patient_id', "", expires=0)
            response.set_cookie('patient_call_number', "", expires=0)
            return response
    return render_template('/patient/phone.html',
                            phone_title=app.config['PHONE_TITLE'],
                            patient_id=patient_id, 
                            activity_id=activity_id)


@app.route('/patient/phone/ping', methods=['POST'])
def phone_patient_ping():
    """ 
    Fct qui s'execute une fois que la page phone est chargee.
    Renvoie la page de confirmation de l'activité
    Place un cookie pendant 20 minutes. Le but du cookie est de stocker l'id du patient
    Si le cookie existe, c'est qu'il s,'est déja inscrit et qu'il ne faut pas l'inscrire une seconde fois
    mais simplement lui réafficher les informations. Cela arrive s'il rafraichit la page.
    S'il vient à la page phone avec un autre numéro (nouvelle inscription) le cookie est effacé dans la fonction précédente
    """
    activity_id = request.form.get('activity_id')
    # si déja inscrit
    if request.cookies.get('patient_id'):
        patient = Patient.query.get(request.cookies.get('patient_id'))
    # si pas encore inscrit
    else:
        patient = patient_validate_scan(activity_id)
        communikation("patient", event="update_scan_phone")
    print("update_scan_phone")

    phone_lines = []

    for line in range(1, 7):
        exec(f"phone_line{line} = app.config['PHONE_LINE{line}']"),
        exec(f"phone_line{line} = replace_balise_phone(phone_line{line}, patient)"),
        phone_lines.append(eval(f"phone_line{line}"))

    print(phone_lines)

    # Convertir le texte des phone_lines de markdown en HTML
    phone_lines = [markdown2.markdown(line) for line in phone_lines]

    print(phone_lines)

    response = make_response(render_template('/patient/phone_confirmation.html', 
                                            patient=patient,
                                            phone_lines=phone_lines,
                                            phone_center=app.config['PHONE_CENTER']))
    response.set_cookie('patient_id', str(patient.id), max_age=60*30)  # Cookie valable pour 20 minutes
    response.set_cookie('patient_call_number', str(patient.call_number), max_age=60*30)
    return response



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


@app.route('/current_patients')
def current_patients():
    # Supposons que vous vouliez afficher les patients dont le statut est "au comptoir"
    patients = Patient.query.filter_by(status='au comptoir').all()
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


def set_server_url(app, request):
    # Stockage de l'adresse pour la génération du QR code
    if request.host_url == "http://127.0.0.1:5000/":
        server_url = app.config.get('NETWORK_ADRESS')
    else:
        server_url = request.host_url
    app.config['SERVER_URL'] = server_url


# Charge des valeurs qui ne sont pas amener à changer avant redémarrage APP
def load_configuration():
    app.logger.info("Loading configuration from database")

    config_mappings = {
        "pharmacy_name": ("PHARMACY_NAME", "value_str"),
        "network_adress": ("NETWORK_ADRESS", "value_str"),
        "numbering_by_activity": ("NUMBERING_BY_ACTIVITY", "value_bool"),
        "algo_activate": ("ALGO_IS_ACTIVATED", "value_bool"),
        "algo_overtaken_limit": ("ALGO_OVERTAKEN_LIMIT", "value_int"),
        "printer": ("PRINTER", "value_bool"),
        "printer_width": ("PRINTER_WIDTH", "value_int"),
        "add_paper": ("ADD_PAPER", "value_bool"),
        "announce_title": ("ANNOUNCE_TITLE", "value_str"),
        "announce_title_size": ("ANNOUNCE_TITLE_SIZE", "value_int"),
        "announce_subtitle": ("ANNOUNCE_SUBTITLE", "value_str"),
        "announce_text_up_patients": ("ANNOUNCE_TEXT_UP_PATIENTS", "value_str"),
        "announce_text_up_patients_display": ("ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY", "value_str"),
        "announce_text_down_patients": ("ANNOUNCE_TEXT_DOWN_PATIENTS", "value_str"),
        "announce_text_down_patients_display": ("ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY", "value_str"),
        "announce_sound": ("ANNOUNCE_SOUND", "value_bool"),
        "announce_alert": ("ANNOUNCE_ALERT", "value_bool"),
        "announce_alert_filename": ("ANNOUNCE_ALERT_FILENAME", "value_str"),
        "announce_style": ("ANNOUNCE_STYLE", "value_str"),
        "announce_player": ("ANNOUNCE_PLAYER", "value_str"),
        "announce_voice": ("ANNOUNCE_VOICE", "value_str"),
        "announce_infos_display": ("ANNOUNCE_INFOS_DISPLAY", "value_bool"),
        "announce_infos_display_time": ("ANNOUNCE_INFOS_DISPLAY_TIME", "value_int"),
        "announce_infos_transition": ("ANNOUNCE_INFOS_TRANSITION", "value_str"),
        "announce_infos_gallery": ("ANNOUNCE_INFOS_GALLERY", "value_str"),
        "announce_infos_mix_folders": ("ANNOUNCE_INFOS_MIX_FOLDERS", "value_bool"),
        "announce_infos_width": ("ANNOUNCE_INFOS_WIDTH", "value_int"),
        "announce_infos_height": ("ANNOUNCE_INFOS_HEIGHT", "value_int"),
        "announce_call_text": ("ANNOUNCE_CALL_TEXT", "value_str"),
        "announce_call_text_size": ("ANNOUNCE_CALL_TEXT_SIZE", "value_int"),
        "announce_call_text_transition": ("ANNOUNCE_CALL_TEXT_TRANSITION", "value_str"),
        "announce_ongoing_display": ("ANNOUNCE_ONGOING_DISPLAY", "value_bool"),
        "announce_ongoing_text": ("ANNOUNCE_ONGOING_TEXT", "value_str"),
        "announce_call_sound": ("ANNOUNCE_CALL_SOUND", "value_str"),
        "counter_order": ("COUNTER_ORDER", "value_str"),
        "music_spotify": ("MUSIC_SPOTIFY", "value_bool"),
        "music_spotify_user": ("MUSIC_SPOTIFY_USER", "value_str"),
        "music_spotify_key": ("MUSIC_SPOTIFY_KEY", "value_str"),
        "page_patient_structure" : ("PAGE_PATIENT_STRUCTURE", "value_str"),
        "page_patient_disable_button": ("PAGE_PATIENT_DISABLE_BUTTON", "value_bool"),
        "page_patient_disable_default_message": ("PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE", "value_str"),
        "page_patient_title": ("PAGE_PATIENT_TITLE", "value_str"),
        "page_patient_subtitle": ("PAGE_PATIENT_SUBTITLE", "value_str"),
        "page_patient_qrcode_display": ("PAGE_PATIENT_QRCODE_DISPLAY", "value_bool"),
        "page_patient_qrcode_web_page": ("PAGE_PATIENT_QRCODE_WEB_PAGE", "value_bool"),
        "page_patient_qrcode_data": ("PAGE_PATIENT_QRCODE_DATA", "value_str"),
        "page_patient_print_ticket_display": ("PAGE_PATIENT_PRINT_TICKET_DISPLAY", "value_bool"),
        "page_patient_end_timer": ("PAGE_PATIENT_END_TIMER", "value_int"),
        "ticket_header": ("TICKET_HEADER", "value_str"),
        "ticket_header_printer": ("TICKET_HEADER_PRINTER", "value_str"),
        "ticket_message": ("TICKET_MESSAGE", "value_str"),
        "ticket_message_printer": ("TICKET_MESSAGE_PRINTER", "value_str"),
        "ticket_footer": ("TICKET_FOOTER", "value_str"),
        "ticket_footer_printer": ("TICKET_FOOTER_PRINTER", "value_str"),
        "phone_center": ("PHONE_CENTER", "value_bool"),
        "phone_title": ("PHONE_TITLE", "value_str"),
        "phone_line1": ("PHONE_LINE1", "value_str"),
        "phone_line2": ("PHONE_LINE2", "value_str"),
        "phone_line3": ("PHONE_LINE3", "value_str"),
        "phone_line4": ("PHONE_LINE4", "value_str"),
        "phone_line5": ("PHONE_LINE5", "value_str"),
        "phone_line6": ("PHONE_LINE6", "value_str"),
        "cron_delete_patient_table_activated": ("CRON_DELETE_PATIENT_TABLE_ACTIVATED", "value_bool"),
        "security_login_admin": ("SECURITY_LOGIN_ADMIN", "value_bool"),
        "security_login_counter": ("SECURITY_LOGIN_COUNTER", "value_bool"),
        "security_login_screen": ("SECURITY_LOGIN_SCREEN", "value_bool"),
        "security_login_patient": ("SECURITY_LOGIN_PATIENT", "value_bool"),
        "security_remember_duration": ("SECURITY_REMEMBER_DURATION", "value_int"),
    }

    for key, (config_name, value_type) in config_mappings.items():
        config_option = ConfigOption.query.filter_by(key=key).first()
        if config_option:
            app.config[config_name] = getattr(config_option, value_type)

    # Handling special case for cron_delete_patient_table_activated
    #if app.config.get('CRON_DELETE_PATIENT_TABLE_ACTIVATED'):
    #    scheduler_clear_all_patients()
    # TODO A Créer seulement si n'existe pas 

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

        
with app.app_context():

    init_database(database, db)

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

    init_days_of_week_db_from_json(Weekday, db, app)
    init_activity_schedules_db_from_json(ActivitySchedule, Weekday, db, app)
    init_default_activities_db_from_json(ConfigVersion, Activity, ActivitySchedule, db)
    init_counters_data_from_json(ConfigVersion, Counter, Activity, db)  # a verifier
    init_staff_data_from_json(ConfigVersion, Pharmacist, Activity, db)
    init_default_options_db_from_json(db, ConfigVersion, ConfigOption)
    init_default_buttons_db_from_json(ConfigVersion, Button, Activity, db)
    init_default_languages_db_from_json(Language, db)
    init_or_update_default_texts_db_from_json(ConfigVersion, Text, db)
    init_update_default_translations_db_from_json(ConfigVersion, TextTranslation, Text, Language, db)
    init_default_algo_rules_db_from_json(ConfigVersion, AlgoRule, db)
    load_configuration()
    clear_old_patients_table()
    clear_counter_table(db, Counter, Patient)

app.config.from_object(ConfigScheduler())
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# Fonctions attachées à app afin de pouvoir les appeler depuis un autre fichier via current_app
app.load_configuration = load_configuration
app.display_toast = display_toast


if __name__ == "__main__":

    # POUR L'instant RabbitMQ ne fonctionne pas avec Flask-SocketIO
    # VOir https://github.com/sensibill/socket.io-amqp pour faire le lien

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

