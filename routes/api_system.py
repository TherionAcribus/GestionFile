"""Routes système : sondes de santé, jeton applicatif, diagnostics RabbitMQ.

Extrait d'``app.py`` (point 9.5d). Aucune logique métier ici : uniquement ce dont
l'infrastructure a besoin (orchestrateur, clients machine) pour savoir si le
serveur est vivant, prêt, et pour obtenir un jeton d'accès.
"""

import os
import time

import pika
from flask import Blueprint, current_app as app, jsonify, request

from auth_utils import check_app_secret, generate_app_token, require_app_token_or_login
from extensions import socketio
from models import db, Counter

api_system_bp = Blueprint('api_system', __name__)

@api_system_bp.route('/send_message', methods=['POST'])
@require_app_token_or_login
def send_message():
    message = request.json.get('message', 'Hello from server')
    try:
        socketio.emit('new_message', {'data': message})
        return "Message sent!"
    except Exception as e:
        return f"Failed to send message: {e}", 500



@api_system_bp.route('/send')
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
            # `time` designe bien le module standard ici. Dans app.py il pointait
            # sur `datetime.time` (importe par `from datetime import datetime,
            # time, timedelta`) : `time.sleep` n'existe pas dessus, et la boucle
            # de reessai levait donc une AttributeError des le premier echec.
            time.sleep(5)  # Attendez 5 secondes avant de réessayer

    app.logger.error("Failed to connect to RabbitMQ after 5 attempts")
    return jsonify({"message": "Failed to connect to RabbitMQ"}), 500



@api_system_bp.route('/test')
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



@api_system_bp.route('/test_local')
@require_app_token_or_login
def rabbitmq_status_local():
    rabbitmq_url = app.config.get('RABBITMQ_URL') or os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
    params = pika.URLParameters(rabbitmq_url)

    try:
        connection = pika.BlockingConnection(params)
        connection.close()
        return jsonify({"status": "RabbitMQ is running"})
    except Exception as e:
        # Auparavant cette sonde renvoyait 204 meme quand la connexion echouait :
        # elle affichait donc "tout va bien" en toute circonstance, le diagnostic
        # partant sur stdout. Meme contrat que /test ci-dessus.
        app.logger.warning("Connexion RabbitMQ (local) impossible : %s", e)
        return jsonify({"status": "RabbitMQ is not running", "error": str(e)}), 500



@api_system_bp.route('/healthz')
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



@api_system_bp.route('/readyz')
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



@api_system_bp.route('/api/get_app_token', methods=['POST'])
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


@api_system_bp.route('/api/counters', methods=['GET'])
@require_app_token_or_login
def get_counters():
    counters = Counter.query.all()
    counters_list = [{'id': counter.id, 'name': counter.name} for counter in counters]
    return jsonify(counters_list)


