import csv
import os
import re
import time
from functools import wraps
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import func, text
from models import db, Button, Activity, Patient, JobExecutionLog, PatientHistory, AggregatedStats
from services.queue_service import (
    archive_and_purge_all_patients, archive_and_purge_old_patients,
    purge_all_patients, purge_old_patients,
)
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
            _record_job_execution(
                f'Disable_Buttons_Activity_{activity_id}', 'success')
            app.logger.info(f"Successfully disabled buttons for activity: {activity.name}")

        except Exception as e:
            _record_job_execution(
                f'Disable_Buttons_Activity_{activity_id}', 'failed', str(e))
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
            _record_job_execution(
                f'Enable_Buttons_Activity_{activity_id}', 'success')
            app.logger.info(f"Successfully enabled buttons for activity: {activity.name}")

        except Exception as e:
            _record_job_execution(
                f'Enable_Buttons_Activity_{activity_id}', 'failed', str(e))
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
    """Purge de démarrage des patients antérieurs à aujourd'hui.

    Point audit : ce chemin supprimait directement les lignes, sans
    historisation, sans audit et sans rollback explicite — après une
    interruption du serveur, des patients non encore transférés étaient
    perdus même avec ``CRON_TRANSFER_PATIENT_TO_HISTORY`` activé. Il délègue
    désormais aux mêmes services transactionnels que le job nocturne : la
    conservation dans l'historique y est respectée de la même façon.
    """
    if current_app.config.get("CRON_DELETE_PATIENT_TABLE_ACTIVATED", False):
        today = datetime.now(time_tz).date()
        if current_app.config.get("CRON_TRANSFER_PATIENT_TO_HISTORY", False):
            deleted = archive_and_purge_old_patients(today)
        else:
            deleted = purge_old_patients(today)
        if deleted:
            current_app.logger.info(
                f"Deleted {deleted} old patients not from today ({today}).")
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
    # ``app.logger`` et non ``current_app`` : le contexte applicatif n'est pas
    # encore poussé à ce stade — ``current_app`` lèverait RuntimeError dans le
    # processus scheduler (aucun contexte ambiant n'y existe).
    app.logger.debug("Clear all patients")

    with app.app_context():
        _refresh_config(app)
        try:
            # Services métier sans décorateur (point audit) : l'ancienne
            # version appelait la VUE clear_all_patients_from_db, décorée
            # par @require_permission — hors requête HTTP, current_user
            # est indisponible et la tâche échouait avant la purge.
            # Variante « avec archivage » : copie + purge dans UNE transaction
            # (point audit) — plus de fenêtre où la copie serait validée sans
            # la suppression, ni doublons d'historique à la relance.
            if app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"]:
                archive_and_purge_all_patients()
            else:
                purge_all_patients()

            _record_job_execution('Clear Patient Table', 'success')
            app.logger.info("Clear patients job completed successfully")

        except Exception as e:
            _record_job_execution('Clear Patient Table', 'failed', str(e))
            app.logger.error(f"Clear patients job failed with error: {str(e)}")

def clear_announce_calls_job():
    """Wrapper pour le nettoyage des annonces"""
    app = AppHolder.get_app()  # Récupérer l'instance de l'application

    with app.app_context():
        _refresh_config(app)
        try:
            clear_announces_call()
            _record_job_execution('Clear Announce Calls', 'success')
            app.logger.info("Clear announce calls job completed successfully")

        except Exception as e:
            _record_job_execution('Clear Announce Calls', 'failed', str(e))
            app.logger.error(f"Clear announce calls job failed with error: {str(e)}")

ANNOUNCEMENT_CACHE_RETENTION_DAYS = 31
_CACHED_ANNOUNCEMENT_NAME = re.compile(r"^[0-9a-f]{64}\.mp3$")


def clear_announces_call():
    """Purge les annonces obsoletes sans invalider le cache utile.

    Les anciens fichiers ``patient_<numero>.mp3`` sont supprimes au prochain
    passage. Les MP3 modernes, identifies par contenu, sont conserves 31 jours
    apres leur derniere utilisation afin que la numerotation quotidienne puisse
    etre rejouee sans appel TTS distant.
    """
    announce_folder = os.path.join(current_app.static_folder, 'audio', 'annonces')
    files_count = 0  # Compteur de fichiers supprimés
    
    try:
        if not os.path.exists(announce_folder):
            raise FileNotFoundError("Le répertoire d'annonces n'existe pas")
            
        cutoff = time.time() - (ANNOUNCEMENT_CACHE_RETENTION_DAYS * 24 * 60 * 60)
        for fichier in os.listdir(announce_folder):
            fichier_complet = os.path.join(announce_folder, fichier)
            is_legacy = fichier.startswith("patient_") and fichier.endswith(".mp3")
            is_expired_cache = (
                _CACHED_ANNOUNCEMENT_NAME.fullmatch(fichier)
                and os.path.getmtime(fichier_complet) < cutoff
            )
            if os.path.isfile(fichier_complet) and (is_legacy or is_expired_cache):
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

AUTO_ARCHIVE_JOB_ID = 'Auto Archive Data'


def _record_job_execution(job_id, status, error_message=None):
    """Journalise le résultat d'un job dans une transaction saine.

    Point audit : les ``except`` des jobs faisaient ``db.session.add(log)`` +
    ``commit()`` sur une session potentiellement laissée en échec par
    l'opération métier — le commit du journal levait alors un
    ``PendingRollbackError`` qui masquait l'erreur d'origine. Le ``rollback()``
    préalable rend la journalisation inconditionnellement sûre (sans effet
    sur une session déjà propre).
    """
    db.session.rollback()
    db.session.add(JobExecutionLog(
        job_id=job_id, status=status, error_message=error_message))
    db.session.commit()


def reconcile_auto_archive_job():
    """Aligne le job d'archivage sur ``DATA_AUTO_ARCHIVE_ENABLED``.

    Le jobstore est persistant et partagé entre processus : un job restant
    d'une activation précédente continuerait à tourner alors que l'interface
    affiche l'archivage désactivé (et inversement). Appelée au démarrage du
    scheduler et après chaque changement de configuration.

    Retourne ``'added'``, ``'removed'`` ou ``'unchanged'``. Lève l'exception
    du jobstore à l'appelant (démarrage : journal ; route : avertissement
    renvoyé au client).
    """
    enabled = bool(current_app.config.get('DATA_AUTO_ARCHIVE_ENABLED', False))

    if enabled and not scheduler.get_job(AUTO_ARCHIVE_JOB_ID):
        scheduler.add_job(
            id=AUTO_ARCHIVE_JOB_ID,
            func=auto_archive_job,
            trigger='cron',
            hour=3,  # Default 3 AM
            minute=30,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1
        )
        if not scheduler.get_job(AUTO_ARCHIVE_JOB_ID):
            raise RuntimeError("le job d'archivage n'a pas été planifié")
        return 'added'

    if not enabled and scheduler.get_job(AUTO_ARCHIVE_JOB_ID):
        scheduler.remove_job(AUTO_ARCHIVE_JOB_ID)
        return 'removed'

    return 'unchanged'


def auto_archive_job():
    """Tâche planifiée pour l'archivage automatique"""
    app = AppHolder.get_app()

    with app.app_context():
        _refresh_config(app)
        try:
            # Garde-fou : le job peut subsister dans le jobstore persistant
            # alors que l'option a été désactivée (retrait en échec côté
            # route, ou job antérieur à l'existence du drapeau). La base fait
            # foi — pas le jobstore.
            if not app.config.get('DATA_AUTO_ARCHIVE_ENABLED', False):
                app.logger.warning(
                    "Auto archive job skipped: DATA_AUTO_ARCHIVE_ENABLED is off")
                # Réconciliation d'exécution : on se retire soi-même pour ne
                # plus être relancé (en plus de la réconciliation du démarrage).
                try:
                    scheduler.remove_job(AUTO_ARCHIVE_JOB_ID)
                except Exception as e:
                    app.logger.error(
                        "Retrait du job '%s' impossible : %s",
                        AUTO_ARCHIVE_JOB_ID, e)
                _record_job_execution(
                    AUTO_ARCHIVE_JOB_ID, 'skipped',
                    'DATA_AUTO_ARCHIVE_ENABLED désactivé')
                return

            days = app.config.get('DATA_ARCHIVE_DAYS', 365)
            compress = app.config.get('DATA_ARCHIVE_COMPRESSED', True)

            if days is not None:
                # Deux opérations distinctes : l'agrégation (archivage, les
                # moyennes quotidiennes subsistent) et la purge définitive
                # (aucune statistique conservée). L'ancien ``archive_data``
                # compress=True/False masquait cette différence.
                if compress:
                    result = aggregate_history(days)
                else:
                    result = purge_history(days)

                _record_job_execution(AUTO_ARCHIVE_JOB_ID, 'success', result)
                app.logger.info(f"Auto archive job completed: {result}")

        except Exception as e:
            # _record_job_execution rollback d'abord : la journalisation ne
            # masque plus un échec métier (PendingRollbackError, point audit).
            _record_job_execution(AUTO_ARCHIVE_JOB_ID, 'failed', str(e))
            app.logger.error(f"Auto archive job failed: {str(e)}")

def _history_dates_before(cutoff_date):
    """Dates distinctes de PatientHistory antérieures au cutoff.

    MySQL renvoie des ``datetime.date``, SQLite des chaînes ISO : on
    normalise en ``date`` pour que ``datetime.combine`` fonctionne sur les
    deux moteurs.
    """
    rows = db.session.query(func.date(PatientHistory.timestamp)).filter(
        PatientHistory.timestamp < cutoff_date
    ).distinct().all()

    dates = []
    for (day,) in rows:
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
        elif isinstance(day, datetime):
            day = day.date()
        dates.append(day)
    return dates


def _history_days_before(cutoff_date):
    """Itère sur les journées d'historique antérieures au cutoff.

    Cède ``(date, query_du_jour)``. Traiter la suppression par journée borne
    la taille de chaque transaction : sur un gros historique, un DELETE unique
    sur plusieurs années gonflerait le journal d'annulation.
    """
    for process_date in _history_dates_before(cutoff_date):
        day_start = datetime.combine(process_date, datetime.min.time())
        day_end = datetime.combine(process_date, datetime.max.time())
        day_query = PatientHistory.query.filter(
            PatientHistory.timestamp.between(day_start, day_end)
        )
        if day_query.count():
            yield process_date, day_query


def count_history_before(older_than_days):
    """Décompte préalable, sans effet de bord, pour la modale de confirmation."""
    cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(older_than_days))
    days = _history_dates_before(cutoff_date)
    return {
        'rows': PatientHistory.query.filter(
            PatientHistory.timestamp < cutoff_date).count(),
        'days': len(days),
        'oldest': str(min(days)) if days else None,
        'newest': str(max(days)) if days else None,
    }


def count_aggregated_before(older_than_days):
    """Même décompte pour les statistiques agrégées."""
    cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(older_than_days))
    base = AggregatedStats.query.filter(AggregatedStats.date < cutoff_date)
    bounds = db.session.query(
        func.min(AggregatedStats.date), func.max(AggregatedStats.date)
    ).filter(AggregatedStats.date < cutoff_date).first()
    oldest, newest = bounds if bounds else (None, None)
    return {
        'rows': base.count(),
        'days': None,
        'oldest': str(oldest) if oldest else None,
        'newest': str(newest) if newest else None,
    }


_HISTORY_EXPORT_FIELDS = (
    'id', 'call_number', 'timestamp', 'timestamp_counter', 'timestamp_end',
    'day_of_week', 'status', 'counter_id', 'activity_id', 'language_id',
    'overtaken', 'patient_source_id',
)


def export_history_csv(cutoff_date):
    """Exporte en CSV les lignes détaillées sur le point d'être supprimées.

    Écrit ``instance/exports/patient_history_<horodatage>.csv`` et retourne
    le nom du fichier (le chemin complet reste dans les journaux serveur).
    ``yield_per`` évite de charger tout l'historique en mémoire.
    """
    export_dir = os.path.join(current_app.instance_path, 'exports')
    os.makedirs(export_dir, exist_ok=True)
    filename = f"patient_history_{datetime.now(time_tz).strftime('%Y%m%d_%H%M%S')}.csv"
    path = os.path.join(export_dir, filename)

    query = (PatientHistory.query
             .filter(PatientHistory.timestamp < cutoff_date)
             .order_by(PatientHistory.timestamp)
             .yield_per(1000))

    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(_HISTORY_EXPORT_FIELDS)
        for row in query:
            writer.writerow([getattr(row, field) for field in _HISTORY_EXPORT_FIELDS])

    current_app.logger.info("Historique exporté avant suppression : %s", path)
    return filename


class PartialHistoryError(RuntimeError):
    """Archivage/purge interrompu après des journées déjà validées.

    La suppression est volontairement commitée **par journée** (borne la
    taille des transactions sur un gros historique) : un échec en cours de
    route laisse donc un résultat partiel — les journées déjà traitées sont
    définitives. Cette exception rend le partialité explicite dans le
    journal du job, l'audit et la réponse HTTP.
    """


def _raise_partial(operation, days_processed, rows_done, failed_date,
                   backup_name, error):
    """Rollback explicite puis exception marquée « partiel ».

    Le rollback remet la session dans un état sain AVANT que l'appelant ne
    journalise l'échec (sinon : PendingRollbackError masquant l'erreur).
    """
    db.session.rollback()
    message = (
        f"{operation} PARTIEL : {days_processed} journée(s) déjà validée(s) "
        f"({rows_done} lignes) ; échec sur la journée du {failed_date} : {error}")
    if backup_name:
        message += f". Sauvegarde CSV : {backup_name}"
    raise PartialHistoryError(message) from error


def aggregate_history(older_than_days, export_csv=False):
    """Archivage : agrège les lignes détaillées en statistiques quotidiennes
    (globales, par activité, langue et comptoir) PUIS les supprime.

    Les dossiers individuels disparaissent — seules les moyennes par jour
    subsistent. ``export_csv=True`` conserve une copie CSV des détails dans
    ``instance/exports/`` avant toute suppression.

    Commit par journée : un échec intermédiaire lève :class:`PartialHistoryError`
    après rollback — les journées déjà commitées restent validées et le
    message le signale explicitement.
    """
    cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(older_than_days))

    backup_name = export_history_csv(cutoff_date) if export_csv else None

    total_archived = 0
    days_processed = 0

    for process_date, day_query in _history_days_before(cutoff_date):
        try:
            create_daily_stats(process_date, day_query)
            archived = day_query.delete(synchronize_session=False)
            db.session.commit()
        except Exception as e:
            _raise_partial("Archivage", days_processed, total_archived,
                           process_date, backup_name, e)
        total_archived += archived
        days_processed += 1

    message = f"Archived {total_archived} records from {days_processed} days."
    if backup_name:
        message += f" Sauvegarde CSV : {backup_name}."
    return message


def purge_history(older_than_days, export_csv=False):
    """Purge définitive : supprime les lignes détaillées SANS agrégation.

    Contrairement à :func:`aggregate_history`, aucune statistique n'est
    conservée — ce n'est pas un archivage. ``export_csv=True`` écrit d'abord
    une copie CSV des lignes dans ``instance/exports/``.

    Commit par journée : un échec intermédiaire lève :class:`PartialHistoryError`
    après rollback — les journées déjà commitées restent supprimées et le
    message le signale explicitement.
    """
    cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(older_than_days))

    backup_name = export_history_csv(cutoff_date) if export_csv else None

    total_deleted = 0
    days_processed = 0

    for process_date, day_query in _history_days_before(cutoff_date):
        try:
            deleted = day_query.delete(synchronize_session=False)
            db.session.commit()
        except Exception as e:
            _raise_partial("Purge", days_processed, total_deleted,
                           process_date, backup_name, e)
        total_deleted += deleted
        days_processed += 1

    message = f"Purged {total_deleted} records from {days_processed} days (no aggregation)."
    if backup_name:
        message += f" Sauvegarde CSV : {backup_name}."
    return message

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
