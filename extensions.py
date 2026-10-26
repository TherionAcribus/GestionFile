"""Extensions Flask partagées, instanciées **sans** application.

Motif classique de la fabrique d'application : chaque extension est créée ici à
vide, puis liée à l'application dans ``create_app`` via ``init_app``. Les autres
modules importent l'objet depuis ici plutôt que de le récupérer sur
``current_app`` (attributs greffés à la main sur l'objet app), ce qui supprime la
dépendance circulaire qui obligeait ``app.py`` à tout héberger.

``db`` reste défini dans ``models.py`` (les modèles en dépendent) et n'est
ré-exporté ici que pour offrir un point d'import unique.
"""

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask_mailman import Mail
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect

from config import time_tz
from models import db  # noqa: F401  (ré-export volontaire)

mail = Mail()
migrate = Migrate()

# Protection CSRF (Flask-WTF). Initialisée dans create_app. On désactive la
# vérification automatique globale (WTF_CSRF_CHECK_DEFAULT=False) et on décide
# nous-mêmes, requête par requête, ce qui doit être protégé : les requêtes
# navigateur (session + cookie) le sont, les requêtes machine (App_Comptoir,
# borne, imprimante — authentifiées par jeton applicatif) et le transport
# Socket.IO en sont exemptés. Voir csrf_protect_browser_requests().
csrf = CSRFProtect()

# Serveur temps réel. Créé à vide : les gestionnaires d'évènements sont déclarés
# dans sockets.py par décorateur, et l'application est liée par init_socketio().
socketio = SocketIO()

# Ordonnanceur de tâches. Le fuseau est épinglé explicitement : sans lui,
# APScheduler prend celui du système — l'image Docker tourne en UTC (ni le
# Dockerfile ni le compose ne définissent TZ) et les cron partent avec 1-2 h
# de décalage. Les déclencheurs des tâches nouvellement planifiées héritent
# de ce fuseau ; celles déjà persistées dans le jobstore gardent le leur
# (picklé à la création) et doivent être recréées pour en bénéficier.
# Le magasin de tâches dépend d'une URL de base de données qui n'est connue
# qu'après le chargement de la configuration : il est donc posé par
# configure_scheduler(), pas ici.
scheduler = BackgroundScheduler(timezone=time_tz)


def init_socketio(app):
    """Lie Socket.IO à l'application, avec le relais inter-processus optionnel."""
    kwargs = {"async_mode": "eventlet"}

    if app.config.get("SOCKETIO_CORS_ALLOWED_ORIGINS") is not None:
        kwargs["cors_allowed_origins"] = app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"]

    # Message queue (RabbitMQ) : AUTO-DÉTECTION. Dès qu'une URL AMQP est
    # résolue (``RABBITMQ_URL`` explicite ou construite depuis
    # RABBITMQ_HOST/USER/PASSWORD dans config.py), le relais inter-processus
    # est câblé — sans lui, un processus sans clients connectés (ex : le
    # conteneur APP_ROLE=scheduler) émet dans le vide : file jamais diffusée,
    # validations automatiques invisibles jusqu'aux resyncs périodiques.
    # Il n'y a plus de switch applicatif : la présence de l'URL reflète
    # l'infrastructure réelle, contrairement à un booléen en base qui pouvait
    # rester faux pendant que les émissions du scheduler se perdaient.
    # Installation mono-processus sans broker : laisser les variables
    # RABBITMQ_* non renseignées (URL None) → aucun relais.
    rabbitmq_url = app.config.get("RABBITMQ_URL")
    if rabbitmq_url:
        kwargs["message_queue"] = rabbitmq_url

    socketio.init_app(app, **kwargs)
    return socketio


def realtime_relay():
    """Relais Socket.IO réellement actif dans ce processus.

    Lu sur le gestionnaire de clients instancié (KombuManager, BaseManager…)
    et non sur la configuration : ``init_socketio`` n'a pu brancher le
    message_queue que si l'URL AMQP était résolue au démarrage, et c'est cet
    état effectif — pas l'intention — qui doit être affiché."""
    try:
        name = type(socketio.server.manager).__name__
    except AttributeError:
        return None
    return {'BaseManager': 'none', 'KombuManager': 'rabbitmq'}.get(
        name, name.lower())


def configure_scheduler(app):
    """Pose le magasin de tâches persistant de l'ordonnanceur.

    ``SCHEDULER_JOBSTORES`` (config.py) est honoré s'il est défini ; sinon le
    jobstore est construit ici avec ``SCHEDULER_ENGINE_OPTIONS`` — sans
    ``pool_pre_ping``, une connexion poolée tuée par MySQL (wait_timeout)
    faisait échouer ``get_jobs()`` avec « MySQL server has gone away ».
    """
    jobstores = app.config.get("SCHEDULER_JOBSTORES") or {
        "default": SQLAlchemyJobStore(
            url=app.config["SQLALCHEMY_DATABASE_URI_SCHEDULER"],
            engine_options=app.config.get("SCHEDULER_ENGINE_OPTIONS"),
        )
    }
    scheduler.configure(jobstores=jobstores)
    return scheduler


def start_scheduler(active: bool):
    """Démarre l'ordonnanceur (en pause si ``active`` est faux). Idempotent."""
    if scheduler.running:
        return
    scheduler.start(paused=not active)
