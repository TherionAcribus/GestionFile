from flask import url_for, request, has_request_context, current_app
import json
from routes.pyside import create_patients_list_for_pyside

communication_mode = "websocket"

def communikation(stream, data=None, flag=None, event="update", client_id=None):
    """ Effectue la communication avec les clients """
    print(f"communikation called with stream={stream}, data={data}, flag={flag}, event={event}, client_id={client_id}")
    print("communikation", communication_mode, data, event)
    if communication_mode == "websocket":
        #communication_websocket(stream=f"socket_{stream}", data=data)
        if stream == "update_patient":
            patients = create_patients_list_for_pyside()
            print("PYSODE", patients)
            #data = json.dumps({"flag": "patient", "data": patients})
            communication_websocket(stream="socket_app_counter", data=patients, flag="update_patient_list")
            communication_websocket(stream="socket_app_counter", data=patients, flag="my_patient")
            communication_websocket(stream="socket_update_patient", data=patients)
        elif stream == "update_audio":
            if event == "spotify":
                print("spotify!")
                communication_websocket(stream="socket_update_screen", data=data, flag=flag, event=event)
            
            else:
                if current_app.config["ANNOUNCE_ALERT"]:
                    signal_file = current_app.config["ANNOUNCE_ALERT_FILENAME"]
                    audio_path = url_for('static', filename=f'audio/signals/{signal_file}', _external=True)
                    if current_app.config["ANNOUNCE_PLAYER"] == "web":
                        communication_websocket(stream="socket_update_screen", event="audio", data=audio_path)
                    else:
                        communication_websocket(stream="socket_app_screen", data=audio_path, flag="sound")
                if current_app.config["ANNOUNCE_PLAYER"] == "web":
                    communication_websocket(stream="socket_update_screen", data=data, event="audio")
                else:
                    communication_websocket(stream="socket_app_screen", data=data, flag="sound")
        else:
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
    print("data", data)
    print("event", event)

    # Utiliser request.args.get uniquement si dans le contexte de la requête
    if has_request_context():
        stream = request.args.get('stream', stream)
        message = request.args.get('message', data)
    else:
        message = data

    try:
        namespace = f'/{stream}'
        current_app.socketio.emit(event, {"flag": flag, 'data': message}, namespace=namespace)
        print("message:", message)
        print("namespace:", namespace)
        return "Message sent!"
    except Exception as e:
        print("message failed:", message)
        return f"Failed to send message: {e}", 500
    

def communication_rabbitmq(queue, data=None, client_id=None):
    return None
    """essage = data
    try:
        channel.basic_publish(exchange='',
                            routing_key=queue,
                            body=message)
        print("message:", message)
        print("queue:", queue)
        return "Message sent to RabbitMQ!"
    except Exception as e:
        print("message failed:", message)
        return f"Failed to send message: {e}", 500"""

# TODO A SUPPRIMER
def communication(stream, data=None, client_id=None, audio_source=None):
    """ Effectue la communication avec les clients """
    return None

