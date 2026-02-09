import json
import time
import logging
import pika
import threading
from functools import wraps
from flask import url_for, request, has_request_context, current_app
from routes.pyside import create_patients_list_for_pyside
from pika.exceptions import AMQPConnectionError, AMQPChannelError

def with_rabbitmq_connection(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 3
        print("monurl", current_app.config['RABBITMQ_URL'])
        for attempt in range(max_retries):
            try:
                connection = pika.BlockingConnection(pika.URLParameters(current_app.config['RABBITMQ_URL']))
                channel = connection.channel()
                channel.queue_declare(queue=current_app.config['RABBITMQ_QUEUE'], durable=True)
                channel.confirm_delivery()
                result = func(channel, *args, **kwargs)
                connection.close()
                return result
            except (AMQPConnectionError, AMQPChannelError) as e:
                logging.error(f"Tentative {attempt + 1} échouée: {str(e)}")
                if attempt == max_retries - 1:
                    raise
    return wrapper

def setup_rabbitmq():
    #config = get_rabbitmq_config()
    parameters = pika.URLParameters(current_app.config['RABBITMQ_URL'])
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=current_app.config['RABBITMQ_QUEUE'], durable=True)
    channel.confirm_delivery()
    return connection, channel

@with_rabbitmq_connection
def send_message_to_rabbitmq(channel, stream, data, flag=None, event="update"):
    message = {
        'namespace': f'/{stream}',
        'event': event,
        'data': {
            'flag': flag,
            'data': data
        }
    }
    try:
        confirm = channel.basic_publish(
            exchange='',
            routing_key=current_app.config['RABBITMQ_QUEUE'],
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # message persistant
                content_type='application/json'
            ),
            mandatory=True
        )
        if confirm:
            logging.info(f"Message envoyé à RabbitMQ: {stream}")
            return True
        else:
            logging.error("Le message n'a pas pu être confirmé par RabbitMQ")
            return False
    except pika.exceptions.UnroutableError:
        logging.error("Message non routable")
        return False
    except Exception as e:
        logging.error(f"Erreur inattendue lors de l'envoi à RabbitMQ: {e}")
        return False

def rabbitmq_to_socketio(app):
    with app.app_context():
        @with_rabbitmq_connection
        def consume(channel):
            def callback(ch, method, properties, body):
                try:
                    message = json.loads(body)
                    # Utiliser la méthode emit sans l'argument broadcast
                    current_app.socketio.emit(
                        message['event'],
                        message['data'],
                        namespace=message['namespace'],
                        room=None  # Cela envoie à tous les clients dans le namespace
                    )
                    logging.info(f"Message émis: {message['event']} dans {message['namespace']}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as e:
                    logging.error(f"Erreur lors du traitement du message RabbitMQ: {e}")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=current_app.config['RABBITMQ_QUEUE'], on_message_callback=callback)
            try:
                channel.start_consuming()
            except Exception as e:
                logging.error(f"Erreur dans la boucle de consommation RabbitMQ: {e}")

        while True:
            try:
                consume()
            except Exception as e:
                logging.error(f"Erreur de connexion RabbitMQ, tentative de reconnexion: {e}")
                time.sleep(5)

def start_rabbitmq_consumer(app):
    if app.config["START_RABBITMQ"]:
        def run_consumer():
            with app.app_context():
                try:
                    rabbitmq_to_socketio(app)
                except Exception as e:
                    logging.error(f"Erreur dans le consumer RabbitMQ: {e}")

        threading.Thread(target=run_consumer, daemon=True).start()
        logging.info("Consumer RabbitMQ démarré")
    else:
        logging.info("RabbitMQ n'est pas activé, le consumer n'a pas été démarré")

def communikation(stream, data=None, flag=None, event="update", client_id=None, use_rabbitmq=False):
    """ Effectue la communication avec les clients """
    logging.info(f"communikation called with stream={stream}, event={event}, use_rabbitmq={use_rabbitmq}")

    use_rabbitmq = current_app.config["USE_RABBITMQ"]

    if use_rabbitmq and not current_app.config["START_RABBITMQ"]:
        logging.warning("RabbitMQ demandé mais non activé, utilisation de SocketIO direct")
        use_rabbitmq = False

    if stream == "update_patient":
        patients = create_patients_list_for_pyside()
        print('PATIENT LIST', patients)
        _send_message("socket_app_counter", patients, flag=None, event="update_patient_list", use_rabbitmq=use_rabbitmq)
        #_send_message("socket_app_counter", patients, "my_patient", use_rabbitmq)
        _send_message("socket_update_patient", patients, event=event, use_rabbitmq=use_rabbitmq)
    elif stream == "update_audio":
        if event == "spotify":
            _send_message("socket_update_screen", data, flag, "spotify", use_rabbitmq)
        else:
            if current_app.config["ANNOUNCE_ALERT"]:
                signal_file = current_app.config["ANNOUNCE_ALERT_FILENAME"]
                audio_path = url_for('static', filename=f'audio/signals/{signal_file}', _external=True)
                if current_app.config["ANNOUNCE_PLAYER"] == "web":
                    _send_message("socket_update_screen", audio_path, event="audio", use_rabbitmq=use_rabbitmq)
                    print('AUDIO_PATH', audio_path)
                else:
                    _send_message("socket_app_screen", audio_path, "sound", use_rabbitmq=use_rabbitmq)
            if current_app.config["ANNOUNCE_PLAYER"] == "web":
                _send_message("socket_update_screen", data, event="audio", use_rabbitmq=use_rabbitmq)
            else:
                _send_message("socket_app_screen", data, "sound", use_rabbitmq=use_rabbitmq)
    else:
        _send_message(f"socket_{stream}", data, flag, event, use_rabbitmq)

def _send_message(stream, data, flag=None, event="update", use_rabbitmq=True):
    if use_rabbitmq:
        return send_message_to_rabbitmq(stream, data, flag, event)
    else:
        return communication_websocket(stream, data, flag, event=event)

def communication_websocket(stream, data=None, flag=None, client_id=None, event="update"):
    logging.info(f'communication_websocket: stream={stream}, event={event}')

    if has_request_context():
        stream = request.args.get('stream', stream)
        message = request.args.get('message', data)
    else:
        message = data

    try:
        namespace = f'/{stream}'
        current_app.socketio.emit(event, {"flag": flag, 'data': message}, namespace=namespace)
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