import os
import boto3
import bleach
import pytz
import secrets
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# mieux que datetime.timezone pour gérer les fuseaux horaires et les changements d'heure.
# TODO permettre de choisir le fuseau horaire
time_tz = pytz.timezone('Europe/Paris')

database = "mysql"
site = "prod"

load_dotenv()

def uia_username_mapper(identity):
    return bleach.clean(identity, strip=True)

def _load_or_create_secret(env_name: str, instance_filename: str, *, token_bytes: int = 48) -> str:
    """
    Returns a stable secret value for a given env var.

    Priority:
    1) environment variable
    2) file persisted under ./instance/
    3) generated ephemeral value (last resort)
    """
    value = os.getenv(env_name)
    if value:
        return value

    instance_dir = Path(__file__).resolve().parent / "instance"
    secret_path = instance_dir / instance_filename
    try:
        instance_dir.mkdir(parents=True, exist_ok=True)
        if secret_path.exists():
            persisted = secret_path.read_text(encoding="utf-8").strip()
            if persisted:
                return persisted
        generated = secrets.token_urlsafe(token_bytes)
        secret_path.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        return secrets.token_urlsafe(token_bytes)

def get_parameter(name):
    """ Récupération des paramètres pour AWS"""
    ssm = boto3.client('ssm', region_name='eu-west-3')  
    response = ssm.get_parameter(Name=name, WithDecryption=True)
    return response['Parameter']['Value']

class Config:
    SECRET_KEY = _load_or_create_secret("SECRET_KEY", "flask_secret_key.txt")
    SECURITY_PASSWORD_SALT = _load_or_create_secret("SECURITY_PASSWORD_SALT", "security_password_salt.txt")
    SECURITY_PASSWORD_HASH = 'bcrypt'
    SECURITY_PASSWORD_SINGLE_HASH = False
    SECURITY_USER_IDENTITY_ATTRIBUTES = [{'username': {'case_insensitive': False}}]
    SECURITY_USERNAME_ENABLE = True
    SECURITY_USERNAME_REQUIRED = False
    SECURITY_EMAIL_REQUIRED = False  # Désactiver l'obligation d'un email
    SECURITY_REGISTERABLE = True  # Activer l'enregistrement manuel des utilisateurs (si besoin)
    SECURITY_LOGIN_URL = '/admin_security/login'
    SECURITY_POST_LOGIN_VIEW = '/admin'
    SECURITY_REDIRECT_BEHAVIOR = 'spa'

    # Définir les valeurs par défaut ici
    database = os.getenv('DATABASE_TYPE', 'mysql')  # Assurez-vous que la valeur est définie correctement
    site = os.getenv('SITE', 'prod')
    DEBUG = os.getenv("FLASK_DEBUG", "").strip() in {"1", "true", "True", "yes", "on"}
    database_url = os.getenv("DATABASE_URL")
    scheduler_database_url = os.getenv("DATABASE_URL_SCHEDULER")

    _socketio_cors_raw = os.getenv("SOCKETIO_CORS_ALLOWED_ORIGINS", "").strip()
    if not _socketio_cors_raw:
        SOCKETIO_CORS_ALLOWED_ORIGINS = None
    elif _socketio_cors_raw == "*":
        SOCKETIO_CORS_ALLOWED_ORIGINS = "*" if DEBUG else None
    else:
        SOCKETIO_CORS_ALLOWED_ORIGINS = [origin.strip() for origin in _socketio_cors_raw.split(",") if origin.strip()]

    if database_url:

        SQLALCHEMY_DATABASE_URI = database_url
        SQLALCHEMY_DATABASE_URI_SCHEDULER = scheduler_database_url or database_url

    elif database == "mysql":

        if site == "aws":
            MYSQL_USER = get_parameter('MYSQL_USER')
            MYSQL_PASSWORD = get_parameter('MYSQL_PASSWORD')
            HOST = get_parameter('MYSQL_HOST')
            DB_NAME = get_parameter('MYSQL_DATABASE')
            BASE32_KEY = get_parameter('BASE32_KEY')
            RABBITMQ_URL = get_parameter("RABBITMQ_URL")

        else:
            MYSQL_USER = os.getenv('MYSQL_USER')
            MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
            HOST = os.getenv('MYSQL_HOST')
            DB_NAME = os.getenv('MYSQL_DATABASE')
            BASE32_KEY = os.getenv('BASE32_KEY')
            RABBITMQ_URL = os.getenv("RABBITMQ_URL")

        # MySQL Configuration
        SQLALCHEMY_DATABASE_URI = database_url or f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/{DB_NAME}'
        SQLALCHEMY_DATABASE_URI_SCHEDULER = scheduler_database_url or f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/queueschedulerdatabase'

        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_size": 5,  # Nombre maximum de connexions permanentes
            "max_overflow": 10,  # Connexions supplémentaires temporaires si nécessaire
            "pool_recycle": 7200,  # Recycle les connexions après 2 heures
            "pool_pre_ping": True,  # Vérifie la validité de la connexion avant utilisation
            "pool_timeout": 30,  # Temps d'attente pour obtenir une connexion du pool
            "connect_args": {
                "init_command": "SET time_zone = '+02:00'",
                # Paramètres MySQL pour les connexions longue durée
                "connect_timeout": 60,  # Timeout de connexion en secondes
                "read_timeout": 60 * 60,  # Timeout de lecture (1 heure)
                "write_timeout": 60 * 60,  # Timeout d'écriture (1 heure)
            }
        }
        # SQLALCHEMY_BINDS configuration to include MySQL
        #SQLALCHEMY_BINDS = {
        #    'users': f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/userdatabase'
        #}

        # Scheduler
        SCHEDULER_JOBSTORES = {
            'default': SQLAlchemyJobStore(
                url=SQLALCHEMY_DATABASE_URI_SCHEDULER,
                engine_options={
                    "pool_recycle": 7200,
                    "pool_pre_ping": True
                }
            )
        }
        SCHEDULER_API_ENABLED = True
        JOBS = []

    elif database == "sqlite":
        SQLALCHEMY_DATABASE_URI = database_url or 'sqlite:///queuedatabase.db'
        SQLALCHEMY_DATABASE_URI_SCHEDULER = scheduler_database_url or SQLALCHEMY_DATABASE_URI
        SQLALCHEMY_BINDS = {
            'users': 'sqlite:///userdatabase.db'
        }

    # Flask SQLAlchemy Configuration
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # RabbitMQ. L'URL est dans le .env 
    RABBITMQ_QUEUE = "socketio_messages"

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
