"""Routes système : sondes de santé, jeton applicatif, diagnostics RabbitMQ.

Extrait d'``app.py`` (point 9.5d). Aucune logique métier ici : uniquement ce dont
l'infrastructure a besoin (orchestrateur, clients machine) pour savoir si le
serveur est vivant, prêt, et pour obtenir un jeton d'accès.
"""

import os

import pika
from flask import Blueprint, current_app as app, jsonify, request

from auth_utils import check_app_secret, generate_app_token, require_app_token_or_login
from extensions import socketio
from models import db, Counter

api_system_bp = Blueprint('api_system', __name__)

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @api_system_bp.route('/send_message', methods=['POST'])
@require_app_token_or_login
def send_message():
    message = request.json.get('message', 'Hello from server')
    try:
        socketio.emit('new_message', {'data': message})
        return "Message sent!"
    except Exception as e:
        return f"Failed to send message: {e}", 500



# /send et /test retires (point C13) : ce ne sont pas des routes d'usage mais
# des restes de mise au point RabbitMQ — /send publiait « Hello World! » dans
# une file 'hello' que rien ne consomme, avec 5 x 5 s de reessai bloquant dans
# la vue ; /test pingait le broker. Le seul usage reel de RabbitMQ est le
# message_queue de Socket.IO (extensions.py) et le diagnostic equivalent est
# deja fait proprement par /readyz (check AMQP conditionne a START_RABBITMQ).
# Les sondes manuelles dormantes ci-dessous restent cataloguees dans
# tests/test_code_mort.py (ROUTES_DESACTIVEES).


# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @api_system_bp.route('/test_local')
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


