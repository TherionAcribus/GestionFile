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

# Ordonnanceur de tâches. Le magasin de tâches dépend d'une URL de base de
# données qui n'est connue qu'après le chargement de la configuration : il est
# donc posé par configure_scheduler(), pas ici.
scheduler = BackgroundScheduler()


def init_socketio(app):
    """Lie Socket.IO à l'application, avec le relais inter-processus optionnel."""
    kwargs = {"async_mode": "eventlet"}

    if app.config.get("SOCKETIO_CORS_ALLOWED_ORIGINS") is not None:
        kwargs["cors_allowed_origins"] = app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"]

    # Message queue optionnel (RabbitMQ). Sans lui, chaque processus ne diffuse
    # qu'à ses propres clients connectés -- ce qui est le comportement historique
    # et reste parfaitement valide pour un déploiement mono-processus (aucune
    # infra supplémentaire requise). Avec lui, Socket.IO relaie automatiquement
    # les messages entre tous les processus qui partagent le même message_queue,
    # y compris depuis un processus qui ne sert aucune connexion lui-même
    # (ex : le conteneur APP_ROLE=scheduler, cf. scheduler_functions.py).
    # Activé via le switch admin « Démarrer le serveur avec RabbitMQ » (nécessite
    # un redémarrage du process pour prendre effet).
    if app.config.get("START_RABBITMQ") and app.config.get("RABBITMQ_URL"):
        kwargs["message_queue"] = app.config["RABBITMQ_URL"]

    socketio.init_app(app, **kwargs)
    return socketio


def configure_scheduler(app):
    """Pose le magasin de tâches persistant de l'ordonnanceur."""
    scheduler.configure(
        jobstores={"default": SQLAlchemyJobStore(url=app.config["SQLALCHEMY_DATABASE_URI_SCHEDULER"])}
    )
    return scheduler


def start_scheduler(active: bool):
    """Démarre l'ordonnanceur (en pause si ``active`` est faux). Idempotent."""
    if scheduler.running:
        return
    scheduler.start(paused=not active)
