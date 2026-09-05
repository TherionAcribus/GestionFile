import os
from functools import wraps
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import func, text
from models import db, Button, Activity, Patient, JobExecutionLog, PatientHistory, AggregatedStats
from routes.admin_queue import clear_all_patients_from_db
from bdd import transfer_patients_to_history
from app_holder import AppHolder
from config import time_tz
from communication import communikation
import config_sync
from ui_feedback import display_toast
from extensions import scheduler


def _refresh_config(app):
    """Point 11 — recharge app.config si un autre processus l'a modifiée.

    Le processus scheduler (``APP_ROLE=scheduler``) ne sert aucune requête HTTP :
    le ``before_request`` de convergence n'y est jamais déclenché. On resynchronise
    donc explicitement au début de chaque tâche (``force=True``, les tâches sont
    peu fréquentes) afin que les options relues à l'exécution (transfert vers
    l'historique, désactivation des boutons, archivage...) reflètent l'état courant
    de la base. Ne lève jamais (config conservée en cas d'erreur base)."""
    config_sync.maybe_reload_configuration(app, force=True)


def with_app_context(f):
    """Pousse le contexte de l'application passee en PREMIER argument.

    Version unique du decorateur (point 9.5b). Il en existait deux, differentes
    et toutes deux fautives :

    * celle d'app.py fermait sur l'objet app global et oubliait ``@wraps`` (le
      nom de la fonction decoree etait perdu) -- elle est devenue morte au
      point 9.4 et a ete supprimee ;
    * celle-ci utilisait ``current_app``, qui exige qu'un contexte soit **deja**
      pousse : dans une tache de fond, elle levait donc
      ``RuntimeError: Working outside of application context`` au lieu d'en
      fournir un. Elle ne « marchait » que parce que ses deux appelants
      poussaient deja le contexte eux-memes.

    Les deux fonctions decorees recoivent l'application en premier argument :
    on s'en sert, ce qui rend le decorateur utilisable hors contexte.
    """
    @wraps(f)
    def decorated_function(app, *args, **kwargs):
        with app.app_context():
            return f(app, *args, **kwargs)
    return decorated_function

def disable_buttons_for_activity_job(activity_id):
    """Désactive les boutons pour une activité"""
    app = AppHolder.get_app()

    with app.app_context():
        _refresh_config(app)
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
        _refresh_config(app)
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
    if scheduler.get_job(job_id):
        try:
            scheduler.remove_job(job_id)
            current_app.logger.info(f"Existing job '{job_id}' removed.")
        except Exception as e:
            current_app.logger.error(f"Failed to remove job '{job_id}': {e}")

    try:
        hour = int(current_app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[0])
        minute = int(current_app.config["CRON_DELETE_PATIENT_TABLE_HOUR"].split(":")[1])

        # Ajouter la tâche avec une référence de fonction sans arguments
        scheduler.add_job(
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
        if not scheduler.get_job(job_id):
            current_app.logger.error(f"Job '{job_id}' was not properly scheduled")
            display_toast(success=False, message=f"La tâche '{job_id}' n'a pas été planifiée correctement")
            
            return False
            
        current_app.logger.info(f"Job '{job_id}' scheduled for {hour:02d}:{minute:02d}")
        display_toast(success=True, message=f"La tâche '{job_id}' à {hour:02d}:{minute:02d} a bien été planifiée")
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
        scheduler.remove_job('Clear Patient Table')
        current_app.logger.info("Job 'Clear Patient Table' successfully removed.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to remove job 'Clear Patient Table': {e}")
        return False
    

def scheduler_clear_announce_calls():
    job_id = 'Clear Announce Calls'

    # Vérifier si le job existe déjà
    if scheduler.get_job(job_id):
        current_app.logger.info(f"Job '{job_id}' already exists. No new job added.")
        return False

    try:
        hour = int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[0])
        minute = int(current_app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"].split(":")[1])
        
        scheduler.add_job(
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
        if not scheduler.get_job(job_id):
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
        scheduler.remove_job('Clear Announce Calls')
        current_app.logger.info("Job 'Clear Announce Calls' successfully removed.")
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to remove job 'Clear Announce Calls': {e}")
        return False


def clear_all_patients_job():
    """Efface tous les patients en utilisant le contexte de l'application globale"""
    app = AppHolder.get_app()
    current_app.logger.debug("Clear all patients")

    with app.app_context():
        _refresh_config(app)
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
        _refresh_config(app)
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
        display_toast(success=True, message=message)
        current_app.logger.info(message)
        return "", 200
        
    except Exception as e:
        error_message = f"Erreur lors du nettoyage des annonces: {str(e)}"
        display_toast(success=False, message=error_message)
        current_app.logger.error(error_message)
        raise  # Relance l'exception pour le logging dans clear_announce_calls_job

def auto_archive_job():
    """Tâche planifiée pour l'archivage automatique"""
    app = AppHolder.get_app()

    with app.app_context():
        _refresh_config(app)
        try:
            days = app.config.get('DATA_ARCHIVE_DAYS', 365)
            compress = app.config.get('DATA_ARCHIVE_COMPRESSED', True)
            
            if days is not None:
                result = archive_data(days, compress)
                
                log = JobExecutionLog(
                    job_id='Auto Archive Data',
                    status='success',
                    error_message=result
                )
                db.session.add(log)
                db.session.commit()
                app.logger.info(f"Auto archive job completed: {result}")
            
        except Exception as e:
            log = JobExecutionLog(
                job_id='Auto Archive Data',
                status='failed',
                error_message=str(e)
            )
            db.session.add(log)
            db.session.commit()
            app.logger.error(f"Auto archive job failed: {str(e)}")

def archive_data(older_than_days, compress=True):
    """Archive les données plus vieilles que X jours"""
    cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(older_than_days))
    
    # Récupérer les dates distinctes concernées
    dates_to_process = db.session.query(func.date(PatientHistory.timestamp)).filter(
        PatientHistory.timestamp < cutoff_date
    ).distinct().all()
    
    dates_to_process = [d[0] for d in dates_to_process]
    
    total_archived = 0
    
    for process_date in dates_to_process:
        # Filtre pour la journée en cours
        day_start = datetime.combine(process_date, datetime.min.time())
        day_end = datetime.combine(process_date, datetime.max.time())
        
        patients_query = PatientHistory.query.filter(
            PatientHistory.timestamp.between(day_start, day_end)
        )
        
        count = patients_query.count()
        if count == 0:
            continue
            
        if compress:
            create_daily_stats(process_date, patients_query)
            
        # Suppression des données
        patients_query.delete(synchronize_session=False)
        total_archived += count
        db.session.commit()
        
    return f"Archived {total_archived} records from {len(dates_to_process)} days."

def create_daily_stats(date, base_query):
    """Cree les statistiques agregees pour une journee donnee.

    Une seule requete d'agregat par dimension (globale, activite, langue,
    comptoir) grace a un GROUP BY, au lieu d'une requete DISTINCT suivie d'une
    requete par entite : sur un premier archivage rattrapant plusieurs annees,
    l'ancienne boucle emettait des dizaines de milliers de requetes.
    """
    # Idempotence : rejouer l'archivage d'une date deja traitee ne doit pas
    # doubler les comptages (la fusion detaille/compresse les additionne).
    # Couvre aussi les lignes 'global', que la contrainte d'unicite ne protege
    # pas sous MySQL (category_id NULL y est considere comme distinct).
    AggregatedStats.query.filter(AggregatedStats.date == date).delete(
        synchronize_session=False)

    waiting = func.timestampdiff(text('SECOND'),
                                 PatientHistory.timestamp, PatientHistory.timestamp_counter)
    counter = func.timestampdiff(text('SECOND'),
                                 PatientHistory.timestamp_counter, PatientHistory.timestamp_end)
    total = func.timestampdiff(text('SECOND'),
                               PatientHistory.timestamp, PatientHistory.timestamp_end)

    # COUNT(expr) ne compte que les resultats non NULL : c'est exactement
    # l'effectif ayant participe a la moyenne correspondante, seul poids correct
    # pour recombiner ces moyennes cote statistiques.
    metrics = (
        func.count(PatientHistory.id).label('count'),
        func.avg(waiting).label('avg_waiting'),
        func.avg(counter).label('avg_counter'),
        func.avg(total).label('avg_total'),
        func.count(waiting).label('count_waiting'),
        func.count(counter).label('count_counter'),
        func.count(total).label('count_total'),
    )

    def add_row(category_type, category_id, row):
        if not row or not row.count:
            return
        db.session.add(AggregatedStats(
            date=date,
            category_type=category_type,
            category_id=category_id,
            count=row.count,
            avg_waiting_time=row.avg_waiting,
            avg_counter_time=row.avg_counter,
            avg_total_time=row.avg_total,
            count_waiting_time=row.count_waiting,
            count_counter_time=row.count_counter,
            count_total_time=row.count_total,
        ))

    # 1. Global
    add_row('global', None, base_query.with_entities(*metrics).first())

    # 2. Par activite / langue / comptoir : un GROUP BY chacun.
    for category_type, column in (
        ('activity', PatientHistory.activity_id),
        ('language', PatientHistory.language_id),
        ('counter', PatientHistory.counter_id),
    ):
        rows = (base_query
                .with_entities(column.label('category_id'), *metrics)
                .filter(column.isnot(None))
                .group_by(column)
                .all())
        for row in rows:
            add_row(category_type, row.category_id, row)
