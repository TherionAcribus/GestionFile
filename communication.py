import json
import time
import logging
import pika
import os
import threading
from flask import url_for, request, has_request_context, current_app
from routes.pyside import create_patients_list_for_pyside

"""
# Configuration RabbitMQ
RABBITMQ_CONFIG = {
    'production': {
        'url': 'amqp://rabbitmq:ojp5seyp@rabbitmq-7yig:5672',
        'queue': 'socketio_messages'
    },
    'development': {
        'url': 'amqp://guest:guest@localhost:5672/%2F',
        'queue': 'socketio_messages'
    }
}


# Choisissez l'environnement ('production' ou 'development')
# Vous pouvez le définir via une variable d'environnement
ENVIRONMENT = os.environ.get('FLASK_ENV', 'development')

def get_rabbitmq_config():
    return RABBITMQ_CONFIG[ENVIRONMENT]"""

def setup_rabbitmq():
    #config = get_rabbitmq_config()
    parameters = pika.URLParameters(current_app.config['RABBITMQ_URL'])
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=current_app.config['RABBITMQ_QUEUE'], durable=True)
    channel.confirm_delivery()
    return connection, channel

def send_message_to_rabbitmq(stream, data, flag=None, event="update"):
    print("RABBIT!!")
    config = get_rabbitmq_config()
    connection, channel = setup_rabbitmq()
    message = {
        'namespace': f'/{stream}',
        'event': event,
        'data': {
            'flag': flag,
            'data': data
        }
    }
    try:
        channel.basic_publish(
            exchange='',
            routing_key=config['queue'],
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2),
            mandatory=True
        )
        logging.info(f"Message envoyé à RabbitMQ: {stream}")
        return True
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi à RabbitMQ: {e}")
        return False
    finally:
        connection.close()

def rabbitmq_to_socketio(app):
    with app.app_context():
        #config = get_rabbitmq_config()
        connection, channel = setup_rabbitmq()

        def callback(ch, method, properties, body):
            message = json.loads(body)
            try:
                current_app.socketio.emit(message['event'], message['data'], namespace=message['namespace'])
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
        finally:
            connection.close()

def start_rabbitmq_consumer(app):
    threading.Thread(target=rabbitmq_to_socketio, args=(app,), daemon=True).start()

def communikation(stream, data=None, flag=None, event="update", client_id=None, use_rabbitmq=False):
    """ Effectue la communication avec les clients """
    logging.info(f"communikation called with stream={stream}, event={event}, use_rabbitmq={use_rabbitmq}")

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
    elif origin == "printer_paper":
        if data["add_paper"]:
            message = "On est quasiment au bout du rouleau"
        else:
            message = "Une gentille personne a remis du papier"
    elif origin == "patient_taken":
        message = f"Le patient {data['patient'].call_number} vient d'être appelé par un autre comptoir."
        for_counter = data['counter_id']
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