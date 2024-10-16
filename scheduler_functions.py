import os
from functools import wraps
from datetime import datetime
from flask import current_app
from models import db, Button, Activity, Patient
from routes.admin_queue import clear_all_patients_from_db
from bdd import transfer_patients_to_history
from app_holder import AppHolder
from config import time_tz

def with_app_context(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app
        with current_app.app_context():
            return f(*args, **kwargs)
    return decorated_function

def disable_buttons_for_activity_job(activity_id):
    """Efface tous les patients en utilisant le contexte de l'application globale"""
    app = AppHolder.get_app()  # Obtenir l'instance de l'application
    print("Disable boutons")

    # Créer explicitement un contexte d'application avec l'instance obtenue
    with app.app_context():
        disable_buttons_for_activity(app, activity_id)

@with_app_context
def disable_buttons_for_activity(app, activity_id):
    with current_app.app_context():
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
            current_app.communikation("patient", event="refresh_buttons")

def enable_buttons_for_activity_job(activity_id):
    """Efface tous les patients en utilisant le contexte de l'application globale"""
    app = AppHolder.get_app()  # Obtenir l'instance de l'application
    print("Enable boutons")

    # Créer explicitement un contexte d'application avec l'instance obtenue
    with app.app_context():
        enable_buttons_for_activity(app, activity_id)

@with_app_context
def enable_buttons_for_activity(app, activity_id):
    with current_app.app_context():    
        activity = Activity.query.get(activity_id)
        if activity:
            app.logger.info(f"Enabling buttons for activity: {activity.name}")
            buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
            print(buttons, "buttons")
            for button in buttons:
                print(button)
                # ici on ne regarde pas si on veut que le bouton soit grisé ou non
                # on réactive tout pour être sûr que le bouton est présent (ex: gère le fait qu'on a changé de mode en cours de programme)
                button.is_active = True
                button.is_present = True
            db.session.commit()

            app.communikation("patient", event="refresh_buttons")


def add_scheduler_clear_all_patients():
    job_id = 'Clear Patient Table'

    # Vérifier si le job existe avant de tenter de le supprimer
    if current_app.scheduler.get_job(job_id):
        try:
            current_app.scheduler.remove_job(job_id)
            current_app.logger.info(f"Existing job '{job_id}' removed.")
        except Exception as e:
            current_app.logger.error(f"Failed to remove job '{job_id}': {e}")

    try:
        hour = int(current_app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[0])
        minute = int(current_app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[1])

        # Ajouter la tâche avec une référence de fonction sans arguments
        current_app.scheduler.add_job(
            id=job_id,
            func=clear_all_patients_job,  # Utiliser la référence de fonction directe
            trigger='cron',
            hour=hour,
            minute=minute
        )
        current_app.logger.info(f"Job '{job_id}' successfully added.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to add job '{job_id}': {e}")
        return False
    

def clear_old_patients_table(app):
    # Vérifie si la fonctionnalité est activée dans la configuration
    if current_app.config.get("CRON_DELETE_PATIENT_TABLE_ACTIVATED", False):
        # Obtenez la date actuelle en UTC
        today = datetime.now(time_tz).date()
        
        # Construisez la requête pour trouver tous les patients dont la date est antérieure à aujourd'hui
        old_patients = Patient.query.filter(Patient.timestamp < today)
        
        # Supprimez ces patients
        if old_patients.count() > 0:
            old_patients.delete(synchronize_session='fetch')
            db.session.commit()
            # TODO à remettre une fois "communikation" déplacé
            #current_app.communikation("update_patient")
            current_app.logger.info(f"Deleted old patients not from today ({today}).")
    else:
        current_app.logger.info("Deletion of old patients is disabled.")


def remove_scheduler_clear_all_patients():
    try:
        # Supprime le job à l'aide de son id
        current_app.scheduler.remove_job('Clear Patient Table')
        current_app.logger.info("Job 'Clear Patient Table' successfully removed.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to remove job 'Clear Patient Table': {e}")
        return False
    

def scheduler_clear_announce_calls():
    # vide la table patient
    job_id = 'Clear Announce Calls'

    # Vérifier si le job existe déjà
    if current_app.scheduler.get_job(job_id):
        current_app.logger.info(f"Job '{job_id}' already exists. No new job added.")
        return False  # ou True si vous souhaitez indiquer que l'opération globale est réussie

    try:
        hour=int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[0])
        minute=int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[1])
        current_app.scheduler.add_job(id=job_id, 
                        func=clear_announce_calls_job, 
                        trigger='cron', 
                        hour=hour, 
                        minute=minute)
        current_app.logger.info(f"Job '{job_id}' successfully added.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to add job '{job_id}': {e}")
        return False


def remove_scheduler_clear_announce_calls():
    try:
        # Supprime le job à l'aide de son id
        current_app.scheduler.remove_job('Clear Announce Calls')
        current_app.logger.info("Job 'Clear Announce Calls' successfully removed.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to remove job 'Clear Announce Calls': {e}")
        return False


def clear_all_patients_job():
    """Efface tous les patients en utilisant le contexte de l'application globale"""
    app = AppHolder.get_app()  # Obtenir l'instance de l'application
    print("Clear all patients")

    # Créer explicitement un contexte d'application avec l'instance obtenue
    with app.app_context():
        success = True
        if app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"]:
            success = transfer_patients_to_history()
        if success:
            clear_all_patients_from_db(app)


def clear_announce_calls_job():
    """ Il faut appeler la fonction dans une fonction wrapper dans app context car les Threads sont différents"""
    with current_app.app_context():
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
        current_app.display_toast(success=True, message="Tous les fichiers audio ont été supprimés.")
        return "", 200
    else:
        current_app.display_toast(success=False, message="Le répertoire n'existe pas.")
        return "", 200
