import os
from functools import wraps
from datetime import datetime
from flask import current_app
from models import db, Button, Activity, Patient, JobExecutionLog
from routes.admin_queue import clear_all_patients_from_db
from bdd import transfer_patients_to_history
from app_holder import AppHolder
from config import time_tz
from communication import communikation

def with_app_context(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app
        with current_app.app_context():
            return f(*args, **kwargs)
    return decorated_function

def disable_buttons_for_activity_job(activity_id):
    """Désactive les boutons pour une activité"""
    app = AppHolder.get_app()
    
    with app.app_context():
        try:
            activity = Activity.query.get(activity_id)
            if not activity:
                raise ValueError(f"Activity with id {activity_id} not found")
                
            disable_buttons_for_activity(app, activity_id)
            
            # Log du succès
            log = JobExecutionLog(
                job_id=f'Disable_Buttons_Activity_{activity_id}',
                status='success'
            )
            db.session.add(log)
            db.session.commit()
            app.logger.info(f"Successfully disabled buttons for activity: {activity.name}")
            
        except Exception as e:
            # Log de l'erreur
            log = JobExecutionLog(
                job_id=f'Disable_Buttons_Activity_{activity_id}',
                status='failed',
                error_message=str(e)
            )
            db.session.add(log)
            db.session.commit()
            app.logger.error(f"Failed to disable buttons for activity {activity_id}: {str(e)}")

@with_app_context
def disable_buttons_for_activity(app, activity_id):
    """Logique de désactivation des boutons"""
    activity = Activity.query.get(activity_id)
    if activity:
        buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
        buttons_count = len(buttons)
        
        for button in buttons:
            if app.config["PAGE_PATIENT_DISABLE_BUTTON"]:
                button.is_active = False
            else:
                button.is_present = False
        
        db.session.commit()
        communikation("patient", event="refresh_buttons")
        app.logger.info(f"Disabled {buttons_count} buttons for activity: {activity.name}")

def enable_buttons_for_activity_job(activity_id):
    """Active les boutons pour une activité"""
    app = AppHolder.get_app()
    
    with app.app_context():
        try:
            activity = Activity.query.get(activity_id)
            if not activity:
                raise ValueError(f"Activity with id {activity_id} not found")
                
            enable_buttons_for_activity(app, activity_id)
            
            # Log du succès
            log = JobExecutionLog(
                job_id=f'Enable_Buttons_Activity_{activity_id}',
                status='success'
            )
            db.session.add(log)
            db.session.commit()
            app.logger.info(f"Successfully enabled buttons for activity: {activity.name}")
            
        except Exception as e:
            # Log de l'erreur
            log = JobExecutionLog(
                job_id=f'Enable_Buttons_Activity_{activity_id}',
                status='failed',
                error_message=str(e)
            )
            db.session.add(log)
            db.session.commit()
            app.logger.error(f"Failed to enable buttons for activity {activity_id}: {str(e)}")

@with_app_context
def enable_buttons_for_activity(app, activity_id):
    """Logique d'activation des boutons"""
    activity = Activity.query.get(activity_id)
    if activity:
        buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity.id).all()
        buttons_count = len(buttons)
        
        for button in buttons:
            button.is_active = True
            button.is_present = True
        
        db.session.commit()
        communikation("patient", event="refresh_buttons")
        app.logger.info(f"Enabled {buttons_count} buttons for activity: {activity.name}")

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
            minute=minute,
            misfire_grace_time=300,  # peut retenter la tâche en cas de retard (5 minutes)
            coalesce=True,           # Évite les exécutions multiples si plusieurs sont ratées
            max_instances=1          # Empêche les exécutions parallèles
        )

        # Vérification que le job a bien été créé
        if not current_app.scheduler.get_job(job_id):
            current_app.logger.error(f"Job '{job_id}' was not properly scheduled")
            current_app.display_toast(success=False, message=f"La tâche '{job_id}' n'a pas été planifiée correctement")
            
            return False
            
        current_app.logger.info(f"Job '{job_id}' scheduled for {hour:02d}:{minute:02d}")
        current_app.display_toast(success=True, message=f"La tâche '{job_id}' à {hour:02d}:{minute:02d} a bien été planifiée")
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
    job_id = 'Clear Announce Calls'

    # Vérifier si le job existe déjà
    if current_app.scheduler.get_job(job_id):
        current_app.logger.info(f"Job '{job_id}' already exists. No new job added.")
        return False

    try:
        hour = int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[0])
        minute = int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[1])
        
        current_app.scheduler.add_job(
            id=job_id, 
            func=clear_announce_calls_job, 
            trigger='cron', 
            hour=hour, 
            minute=minute,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1
        )
        
        # Vérification que le job a bien été créé
        if not current_app.scheduler.get_job(job_id):
            current_app.logger.error(f"Job '{job_id}' was not properly scheduled")
            return False
            
        current_app.logger.info(f"Job '{job_id}' scheduled for {hour:02d}:{minute:02d}")
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
    app = AppHolder.get_app()
    print("Clear all patients")

    with app.app_context():
        try:
            success = True
            if app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"]:
                success = transfer_patients_to_history()
                
            if success:
                clear_all_patients_from_db(app)
                
                # Log du succès
                log = JobExecutionLog(
                    job_id='Clear Patient Table',
                    status='success'
                )
                db.session.add(log)
                db.session.commit()
                app.logger.info("Clear patients job completed successfully")
                
            else:
                # Log de l'échec du transfert
                log = JobExecutionLog(
                    job_id='Clear Patient Table',
                    status='failed',
                    error_message='Transfer to history failed'
                )
                db.session.add(log)
                db.session.commit()
                app.logger.error("Clear patients job failed: Transfer to history failed")
                
        except Exception as e:
            # Log de l'erreur
            log = JobExecutionLog(
                job_id='Clear Patient Table',
                status='failed',
                error_message=str(e)
            )
            db.session.add(log)
            db.session.commit()
            app.logger.error(f"Clear patients job failed with error: {str(e)}")

def clear_announce_calls_job():
    """Wrapper pour le nettoyage des annonces"""
    app = AppHolder.get_app()  # Récupérer l'instance de l'application
    
    with app.app_context():
        try:
            clear_announces_call()
            
            # Log du succès
            log = JobExecutionLog(
                job_id='Clear Announce Calls',
                status='success'
            )
            db.session.add(log)
            db.session.commit()
            app.logger.info("Clear announce calls job completed successfully")
            
        except Exception as e:
            # Log de l'erreur
            log = JobExecutionLog(
                job_id='Clear Announce Calls',
                status='failed',
                error_message=str(e)
            )
            db.session.add(log)
            db.session.commit()
            app.logger.error(f"Clear announce calls job failed with error: {str(e)}")

def clear_announces_call():
    """Nettoyage des fichiers audio d'annonces"""
    announce_folder = os.path.join(os.getcwd(), 'static/audio/annonces/')
    files_count = 0  # Compteur de fichiers supprimés
    
    try:
        if not os.path.exists(announce_folder):
            raise FileNotFoundError("Le répertoire d'annonces n'existe pas")
            
        # Parcours tous les fichiers dans le répertoire
        for fichier in os.listdir(announce_folder):
            fichier_complet = os.path.join(announce_folder, fichier)
            if os.path.isfile(fichier_complet):
                os.remove(fichier_complet)
                files_count += 1
                
        message = f"{files_count} fichiers audio ont été supprimés"
        current_app.display_toast(success=True, message=message)
        current_app.logger.info(message)
        return "", 200
        
    except Exception as e:
        error_message = f"Erreur lors du nettoyage des annonces: {str(e)}"
        current_app.display_toast(success=False, message=error_message)
        current_app.logger.error(error_message)
        raise  # Relance l'exception pour le logging dans clear_announce_calls_job