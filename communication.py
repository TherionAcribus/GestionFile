import json
import time
import logging
from flask import url_for, request, has_request_context, current_app
from routes.pyside import create_patients_list_for_pyside
from models import bump_queue_revision


def communikation(stream, data=None, flag=None, event="update", client_id=None):
    """ Effectue la communication temps réel avec les clients.

    Passe toujours par SocketIO. Si l'app est configurée avec un message_queue
    (voir START_RABBITMQ / RABBITMQ_URL dans app.py), SocketIO relaie
    automatiquement le message aux clients connectés sur les autres processus
    (ex: le conteneur "scheduler" qui n'a pas de connexions directes). Sans
    message_queue configuré, le comportement est identique à avant : seuls les
    clients connectés à ce process reçoivent le message.
    """
    logging.info(f"communikation called with stream={stream}, event={event}")

    if stream == "update_patient":
        # Toute mutation de la file passe par ici pour diffuser la nouvelle liste
        # complète : on incrémente donc la révision et on la joint à l'enveloppe.
        # Le client (App comptoir) s'en sert pour écarter les messages
        # périmés/dupliqués et détecter un trou (évènement manqué) qui déclenche
        # un rechargement de l'état autoritatif via /api/counter/<id>/state.
        revision = bump_queue_revision()
        patients = create_patients_list_for_pyside()
        print('PATIENT LIST', patients)
        communication_websocket("socket_app_counter", patients, flag=None, event="update_patient_list", revision=revision)
        communication_websocket("socket_update_patient", patients, event=event, revision=revision)
    elif stream == "update_audio":
        if event == "spotify":
            communication_websocket("socket_update_screen", data, flag, event="spotify")
        else:
            if current_app.config["ANNOUNCE_ALERT"]:
                signal_file = current_app.config["ANNOUNCE_ALERT_FILENAME"]
                audio_path = url_for('static', filename=f'audio/signals/{signal_file}', _external=True)
                if current_app.config["ANNOUNCE_PLAYER"] == "web":
                    communication_websocket("socket_update_screen", audio_path, event="audio")
                    print('AUDIO_PATH', audio_path)
                else:
                    communication_websocket("socket_app_screen", audio_path, "sound")
            if current_app.config["ANNOUNCE_PLAYER"] == "web":
                communication_websocket("socket_update_screen", data, event="audio")
            else:
                communication_websocket("socket_app_screen", data, "sound")
    else:
        communication_websocket(f"socket_{stream}", data, flag, event=event)


def communication_websocket(stream, data=None, flag=None, client_id=None, event="update", revision=None):
    logging.info(f'communication_websocket: stream={stream}, event={event}')

    if has_request_context():
        stream = request.args.get('stream', stream)
        message = request.args.get('message', data)
    else:
        message = data

    try:
        namespace = f'/{stream}'
        current_app.socketio.emit(event, {"flag": flag, 'data': message, 'revision': revision}, namespace=namespace)
        logging.info(f"Message SocketIO envoyé: {namespace}")
        return "Message sent!"
    except Exception as e:
        logging.error(f"Échec de l'envoi du message SocketIO: {e}")
        return f"Failed to send message: {e}", 500



def send_app_notification(origin, data):
    # for_counter = None si c'est pour tout le monde, sinon mettre l'id du comptoir
    for_counter = None
    if origin == "activity":
        message = f"{data['activity'].name} : {data['patient'].call_number}"
        print("message_notif:", message)
    elif origin == "printer_error":
        message = f"Erreur d'impression: {data['message']}"
    elif origin == "low_paper":
        message = "On est quasiment au bout du rouleau"
    elif origin == "no_paper":
        message = "Il n'y a plus de papier dans la borne !"
    elif origin == "paper_ok":
        message = "Une gentille personne a remis du papier"
    elif origin == "patient_taken":
        message = f"Le patient {data['patient'].call_number} vient d'être appelé par un autre comptoir."
        for_counter = data['counter_id']
    elif origin == "patient_for_staff_from_app":
        message = f"Le patient {data['patient'].call_number} est redirigé vers vous."
        for_counter = data['counters']
    else:
        origin = origin
        message = data

    notification_data  = {
        "origin": origin,
        "message": message,
        "timestamp": int(time.time()),
        "for_counter": for_counter
    }
    communikation("app_counter", event="notification", flag=for_counter,  data = json.dumps(notification_data))


def notify_patient_phone(call_number):
    """Notifie un patient sur son téléphone que c'est son tour"""
    if not current_app.config["PHONE_DISPLAY_YOUR_TURN"]:
        return False
    try:
        current_app.socketio.emit('your_turn',
                                {'call_number': call_number},
                                namespace='/socket_phone',
                                room=f"call_{call_number}")
        logging.info(f"Notification envoyée au patient {call_number}")
        return True
    except Exception as e:
        logging.error(f"Échec de la notification du patient {call_number}: {e}")
        return False
