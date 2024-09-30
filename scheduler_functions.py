from functools import wraps
from flask import current_app
from models import Activity, Button, db
from threading import Thread
import eventlet
eventlet.monkey_patch()

def with_app_context(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from app import app  # Importation locale de l'instance de l'application
        with app.app_context():
            return f(*args, **kwargs)
    return decorated_function

@with_app_context
def disable_buttons_for_activity(activity_id):

    print("DISABLING BUTTONS FOR ACTIVITY", activity_id)
    # Logique pour désactiver les boutons pour une activité donnée
    activity = Activity.query.get(activity_id)
    if activity:
        current_app.logger.info(f"Disabling buttons for activity: {activity.name}")
        buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
        print(buttons, "buttons")
        for button in buttons:
            if current_app.config["PAGE_PATIENT_DISABLE_BUTTON"]:
                button.is_active = False
            else:
                button.is_present = False
        db.session.commit()
        current_app.communikation("patient", event="refresh")


@with_app_context
def enable_buttons_for_activity(activity_id):
    print("ENABLING BUTTONS FOR ACTIVITY", activity_id)
    
    activity = Activity.query.get(activity_id)
    if activity:
        current_app.logger.info(f"Enabling buttons for activity: {activity.name}")
        buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
        print(buttons, "buttons")
        for button in buttons:
            print(button)
            # ici on ne regarde pas si on veut que le bouton soit grisé ou non
            # on réactive tout pour être sûr que le bouton est présent (ex: gère le fait qu'on a changé de mode en cours de programme)
            button.is_active = True
            button.is_present = True
        db.session.commit()
        # TODO trouver une solution pour APSCHEDULER + Websocket -> Celery ???
        #communication("update_page_patient", data={"action": "refresh buttons"})
        thread = Thread(target=send_refresh_message, args=(current_app._get_current_object(),))
        thread.start()

def send_refresh_message(app):
    with app.app_context():
        try:
            socketio = current_app.extensions['socketio']
            current_app.logger.info("Attempting to emit 'refresh' event")
            eventlet.spawn(socketio.emit, 'refresh', {'data': 'Refresh triggered'}, namespace='/socket_patient')
            current_app.logger.info("'refresh' event emitted successfully")
        except Exception as e:
            current_app.logger.error(f"Error in send_refresh_message: {str(e)}")
