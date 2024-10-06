import os
from functools import wraps
from datetime import datetime, timezone
from flask import current_app as app
from models import db, Patient
from routes.admin_queue import clear_all_patients_from_db

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
        buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
        print(buttons, "buttons")
        for button in buttons:
            if current_app.config["PAGE_PATIENT_DISABLE_BUTTON"]:
                button.is_active = False
            else:
                button.is_present = False
        db.session.commit()
        app.communikation("patient", event="refresh")


@with_app_context
def enable_buttons_for_activity(activity_id):
    from flask import current_app
    from app import Activity, Button, db  # Importez vos modèles ici
    # Logique pour activer les boutons pour une activité donnée
    
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
        app.communikation("patient", event="refresh")


def scheduler_clear_all_patients():
    # vide la table patient
    job_id = 'Clear Patient Table'

    # Vérifier si le job existe déjà
    if app.scheduler.get_job(job_id):
        app.logger.info(f"Job '{job_id}' already exists. No new job added.")
        return False  # ou True si vous souhaitez indiquer que l'opération globale est réussie

    try:
        hour=int(app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[0])
        minute=int(app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[1])
        app.scheduler.add_job(id=job_id, 
                        func=clear_all_patients_job, 
                        trigger='cron', 
                        hour=hour, 
                        minute=minute)
        app.logger.info(f"Job '{job_id}' successfully added.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to add job '{job_id}': {e}")
        return False
    

def clear_old_patients_table(app):
    # Vérifie si la fonctionnalité est activée dans la configuration
    if app.config.get("CRON_DELETE_PATIENT_TABLE_ACTIVATED", False):
        # Obtenez la date actuelle en UTC
        today = datetime.now(timezone.utc).date()
        
        # Construisez la requête pour trouver tous les patients dont la date est antérieure à aujourd'hui
        old_patients = Patient.query.filter(Patient.timestamp < today)
        
        # Supprimez ces patients
        if old_patients.count() > 0:
            old_patients.delete(synchronize_session='fetch')
            db.session.commit()
            app.communikation("update_patient")
            app.logger.info(f"Deleted old patients not from today ({today}).")
    else:
        app.logger.info("Deletion of old patients is disabled.")


def remove_scheduler_clear_all_patients():
    try:
        # Supprime le job à l'aide de son id
        app.scheduler.remove_job('Clear Patient Table')
        app.logger.info("Job 'Clear Patient Table' successfully removed.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to remove job 'Clear Patient Table': {e}")
        return False
    

def scheduler_clear_announce_calls():
    # vide la table patient
    job_id = 'Clear Announce Calls'

    # Vérifier si le job existe déjà
    if app.scheduler.get_job(job_id):
        app.logger.info(f"Job '{job_id}' already exists. No new job added.")
        return False  # ou True si vous souhaitez indiquer que l'opération globale est réussie

    try:
        hour=int(app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[0])
        minute=int(app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[1])
        app.scheduler.add_job(id=job_id, 
                        func=clear_announce_calls_job, 
                        trigger='cron', 
                        hour=hour, 
                        minute=minute)
        app.logger.info(f"Job '{job_id}' successfully added.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to add job '{job_id}': {e}")
        return False


def remove_scheduler_clear_announce_calls():
    try:
        # Supprime le job à l'aide de son id
        app.scheduler.remove_job('Clear Announce Calls')
        app.logger.info("Job 'Clear Announce Calls' successfully removed.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to remove job 'Clear Announce Calls': {e}")
        return False


def clear_all_patients_job():
    """ Il faut appeler la fonction dans une fonction wrapper dans app context car les Threads sont différents"""
    with app.app_context():
        clear_all_patients_from_db()


def clear_announce_calls_job():
    """ Il faut appeler la fonction dans une fonction wrapper dans app context car les Threads sont différents"""
    with app.app_context():
        clear_announces_call()

def clear_announces_call():
    """ Permet de vider le dossier static/audio/annonces des vieux fichiers audio """
    announce_folder = os.path.join(os.getcwd(), 'static/audio/annonces/')
    
    # Vérifie si le répertoire existe
    if os.path.exists(announce_folder):
        # Parcours tous les fichiers dans le répertoire
        for fichier in os.listdir(announce_folder):
            fichier_complet = os.path.join(announce_folder, fichier)
            # Vérifie si c'est un fichier (et non un sous-répertoire)
            if os.path.isfile(fichier_complet):
                os.remove(fichier_complet)  # Supprime le fichier
        app.display_toast(success=True, message="Tous les fichiers audio ont été supprimés.")
        return "", 200
    else:
        app.display_toast(success=False, message="Le répertoire n'existe pas.")
        return "", 200
