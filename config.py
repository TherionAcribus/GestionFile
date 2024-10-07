import os
import boto3
import bleach
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore


database = "mysql"
site = "prod"

def uia_username_mapper(identity):
    return bleach.clean(identity, strip=True)

def get_parameter(name):
    """ Récupération des paramètres pour AWS"""
    ssm = boto3.client('ssm', region_name='eu-west-3')  
    response = ssm.get_parameter(Name=name, WithDecryption=True)
    return response['Parameter']['Value']

class Config:
    SECRET_KEY = 'your_secret_key'
    SECURITY_PASSWORD_SALT = os.getenv('SECURITY_PASSWORD_SALT', 'default_salt')
    SECURITY_PASSWORD_HASH = 'bcrypt'
    SECURITY_PASSWORD_SINGLE_HASH = 'plaintext'
    SECURITY_USER_IDENTITY_ATTRIBUTES = [{'username': {'case_insensitive': False}}]
    SECURITY_USERNAME_ENABLE = True
    SECURITY_USERNAME_REQUIRED = False
    SECURITY_EMAIL_REQUIRED = False  # Désactiver l'obligation d'un email
    SECURITY_REGISTERABLE = True  # Activer l'enregistrement manuel des utilisateurs (si besoin)
    SECURITY_LOGIN_URL = '/admin_security/login'
    SECURITY_POST_LOGIN_VIEW = '/admin_security/home'
    SECURITY_REDIRECT_BEHAVIOR = 'spa'

    # Définir les valeurs par défaut ici
    database = os.getenv('DATABASE_TYPE', 'mysql')  # Assurez-vous que la valeur est définie correctement
    site = os.getenv('SITE', 'prod')

    if database == "mysql":

        if site == "aws":
            MYSQL_USER = get_parameter('MYSQL_USER')
            MYSQL_PASSWORD = get_parameter('MYSQL_PASSWORD')
            HOST = get_parameter('MYSQL_HOST')
            DB_NAME = get_parameter('MYSQL_DATABASE')
            BASE32_KEY = get_parameter('BASE32_KEY')

        else:
            MYSQL_USER = os.getenv('MYSQL_USER')
            MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
            HOST = os.getenv('MYSQL_HOST')
            DB_NAME = os.getenv('MYSQL_DATABASE')
            BASE32_KEY = os.getenv('BASE32_KEY')

        # MySQL Configuration
        SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/{DB_NAME}'
        SQLALCHEMY_DATABASE_URI_SCHEDULER = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/queueschedulerdatabase'
        # SQLALCHEMY_BINDS configuration to include MySQL
        SQLALCHEMY_BINDS = {
            'users': f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{HOST}/userdatabase'
        }

        # Scheduler
        SCHEDULER_JOBSTORES = {
            'default': SQLAlchemyJobStore(url=SQLALCHEMY_DATABASE_URI_SCHEDULER)
        }
        SCHEDULER_API_ENABLED = True
        JOBS = []

    elif database == "sqlite":
        SQLALCHEMY_DATABASE_URI = 'sqlite:///queuedatabase.db'
        SQLALCHEMY_BINDS = {
            'users': 'sqlite:///userdatabase.db'
        }

    # Flask SQLAlchemy Configuration
    SQLALCHEMY_TRACK_MODIFICATIONS = False