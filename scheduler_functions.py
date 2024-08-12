from functools import wraps

def with_app_context(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app
        with current_app.app_context():
            return f(*args, **kwargs)
    return decorated_function

@with_app_context
def disable_buttons_for_activity(activity_id):
    from flask import current_app
    from app import Activity, Button, db  # Importez vos modèles ici

    # Logique pour désactiver les boutons pour une activité donnée
    activity = Activity.query.get(activity_id)
    if activity:
        current_app.logger.info(f"Disabling buttons for activity: {activity.name}")
        buttons = Button.query.order_by(Button.order).filter_by(activity_id=activity.id).all()
        print(buttons, "buttons")
        for button in buttons:
            if current_app.config["PAGE_PATIENT_DISABLE_BUTTON"]:
                button.is_active = False
            else:
                button.is_present = False
        db.session.commit()


@with_app_context
def enable_buttons_for_activity(activity_id):
    from flask import current_app
    from app import Activity, Button, db  # Importez vos modèles ici
    # Logique pour activer les boutons pour une activité donnée
    
    activity = Activity.query.get(activity_id)
    if activity:
        current_app.logger.info(f"Enabling buttons for activity: {activity.name}")
        buttons = Button.query.order_by(Button.order).filter_by(activity_id=activity.id).all()
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
        #communikation("patient", event="refresh")
