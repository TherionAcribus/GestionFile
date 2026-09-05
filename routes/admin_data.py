from flask import Blueprint, render_template, request, jsonify, current_app
from models import db, Patient, PatientHistory, AggregatedStats, ConfigOption, JobExecutionLog
from routes.admin_security import require_permission
from scheduler_functions import archive_data, auto_archive_job
from sqlalchemy import text
from datetime import datetime, timedelta
from config import time_tz
from extensions import scheduler
import config_sync
from audit_service import record_audit
from audit_log import ACTION_UPDATE, ACTION_DELETE, ACTION_CLEAR, OUTCOME_SUCCESS, OUTCOME_FAILURE

admin_data_bp = Blueprint('admin_data', __name__)

# Bornes pour la validation du paramètre `days` :
# - 1 jour minimum (archiver/supprimer des données d'aujourd'hui n'a pas de sens
#   métier et risque de supprimer des données en cours d'utilisation) ;
# - 3650 jours (10 ans) maximum : empêche une valeur absurde qui déplacerait
#   la date de cutoff dans un passé lointain ou un futur impossible.
_DAYS_MIN = 1
_DAYS_MAX = 3650


def _validate_days(raw):
    """Valide et convertit le paramètre ``days`` reçu du formulaire.

    Retourne ``(days_int, None)`` si valide, sinon ``(None, message_erreur)``.
    Le message est sûr pour l'affichage (aucun détail technique).
    """
    if not raw:
        return None, "Paramètre jours manquant."
    try:
        days = int(raw)
    except (ValueError, TypeError):
        return None, "Le nombre de jours doit être un entier."
    if days < _DAYS_MIN:
        return None, f"Le nombre de jours doit être d'au moins {_DAYS_MIN}."
    if days > _DAYS_MAX:
        return None, f"Le nombre de jours ne peut pas dépasser {_DAYS_MAX}."
    return days, None


@admin_data_bp.route('/admin/data')
@require_permission('options')  # Or a new permission 'data'? using 'options' for now or maybe I should add 'admin_data' to Role
def admin_data():

    # Stats
    stats = {
        'patient_count': Patient.query.count(),
        'history_count': PatientHistory.query.count(),
        'aggregated_count': AggregatedStats.query.count(),
        'logs_count': JobExecutionLog.query.count()
    }

    # DB Size estimation (simple row count based or specific query if MySQL)
    db_size = "N/A"
    if current_app.config.get('SQLALCHEMY_DATABASE_URI', '').startswith('mysql'):
        try:
            query = text("""
                SELECT table_schema AS "Database",
                SUM(data_length + index_length) / 1024 / 1024 AS "Size (MB)"
                FROM information_schema.TABLES
                WHERE table_schema = :db_name
                GROUP BY table_schema
            """)
            result = db.session.execute(query, {'db_name': current_app.config.get('MYSQL_DATABASE')}).first()
            if result:
                db_size = f"{round(result[1], 2)} MB"
        except Exception as e:
            current_app.logger.error(f"Error calculating DB size: {e}")

    # Configs
    config = {
        'archive_days': current_app.config.get('DATA_ARCHIVE_DAYS', 365),
        'archive_compressed': current_app.config.get('DATA_ARCHIVE_COMPRESSED', True),
        'auto_archive_enabled': current_app.config.get('DATA_AUTO_ARCHIVE_ENABLED', False)
    }

    return render_template('admin/data.html', stats=stats, db_size=db_size, config=config)

@admin_data_bp.route('/admin/data/manual', methods=['POST'])
@require_permission('options')
def manual_archive():
    days, error = _validate_days(request.form.get('days'))
    if error:
        return jsonify({'success': False, 'message': error})

    compress = request.form.get('compress') == 'true'

    try:
        result = archive_data(days, compress)
        record_audit(ACTION_CLEAR, "patient_history",
                     outcome=OUTCOME_SUCCESS,
                     details=f"archivage manuel >{days}j, compress={compress}")
        return jsonify({'success': True, 'message': result})
    except Exception as e:
        current_app.logger.error("Échec de l'archivage manuel (%dj) : %s", days, e)
        record_audit(ACTION_CLEAR, "patient_history",
                     outcome=OUTCOME_FAILURE,
                     details=f"archivage manuel >{days}j")
        return jsonify({'success': False, 'message': "L'archivage a échoué. Consultez les journaux du serveur."})

@admin_data_bp.route('/admin/data/config', methods=['POST'])
@require_permission('options')
def update_config():
    # Validation des entrées AVANT toute mutation.
    try:
        archive_days_raw = request.form.get('archive_days', '365')
        archive_days, error = _validate_days(archive_days_raw)
        if error:
            return jsonify({'success': False, 'message': f"Jours de rétention : {error}"})
    except Exception:
        return jsonify({'success': False, 'message': "Paramètres de configuration invalides."})

    archive_compressed = request.form.get('archive_compressed') == 'true'
    auto_archive_enabled = request.form.get('auto_archive_enabled') == 'true'

    keys = {
        'DATA_ARCHIVE_DAYS': ('value_int', archive_days),
        'DATA_ARCHIVE_COMPRESSED': ('value_bool', archive_compressed),
        'DATA_AUTO_ARCHIVE_ENABLED': ('value_bool', auto_archive_enabled)
    }

    try:
        # --- Mutation en base DANS une seule transaction ---
        # On ne touche PAS à current_app.config ici : la mémoire ne doit
        # refléter le changement qu'APRÈS un commit réussi (point 10), pour ne
        # pas diverger de la base si le commit échoue.
        for key, (type_, value) in keys.items():
            opt = ConfigOption.query.filter_by(config_key=key).first()
            if not opt:
                opt = ConfigOption(config_key=key)
                db.session.add(opt)
            setattr(opt, type_, value)

        # Point 11 : incrémenter la génération de configuration DANS la même
        # transaction, pour que les autres processus (répliques web, scheduler)
        # rechargent app.config après le commit.
        config_sync.bump_generation()

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Échec de la mise à jour de la config d'archivage : %s", e)
        record_audit(ACTION_UPDATE, "data_config",
                     outcome=OUTCOME_FAILURE,
                     details=f"archive_days={archive_days}")
        return jsonify({'success': False, 'message': "La mise à jour a échoué. Consultez les journaux du serveur."})

    # --- Commit réussi : refléter en mémoire, puis effets de bord ---
    for key, (type_, value) in keys.items():
        current_app.config[key] = value

    # Manage Scheduler
    job_id = 'Auto Archive Data'
    try:
        if current_app.config['DATA_AUTO_ARCHIVE_ENABLED']:
            if not scheduler.get_job(job_id):
                scheduler.add_job(
                    id=job_id,
                    func=auto_archive_job,
                    trigger='cron',
                    hour=3, # Default 3 AM
                    minute=30
                )
        else:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
    except Exception as e:
        # Le scheduler peut échouer indépendamment de la base (ex: job store
        # non disponible). La config est déjà persistée : on journalise sans
        # faire échouer la requête.
        current_app.logger.error("Gestion du scheduler d'archivage impossible : %s", e)

    record_audit(ACTION_UPDATE, "data_config",
                 outcome=OUTCOME_SUCCESS,
                 details=f"archive_days={archive_days}, auto={auto_archive_enabled}")
    return jsonify({'success': True, 'message': 'Configuration updated'})

@admin_data_bp.route('/admin/data/delete_aggregated', methods=['POST'])
@require_permission('options')
def delete_aggregated():
    days, error = _validate_days(request.form.get('days'))
    if error:
        return jsonify({'success': False, 'message': error})

    try:
        cutoff_date = datetime.now(time_tz).date() - timedelta(days=days)

        deleted_count = AggregatedStats.query.filter(
            AggregatedStats.date < cutoff_date
        ).delete(synchronize_session=False)

        db.session.commit()

        record_audit(ACTION_DELETE, "aggregated_stats",
                     outcome=OUTCOME_SUCCESS,
                     details=f">{days}j ({deleted_count} lignes)")
        return jsonify({
            'success': True,
            'message': f'{deleted_count} lignes de statistiques agrégées supprimées.'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Échec de la suppression des stats agrégées (%dj) : %s", days, e)
        record_audit(ACTION_DELETE, "aggregated_stats",
                     outcome=OUTCOME_FAILURE,
                     details=f">{days}j")
        return jsonify({'success': False, 'message': "La suppression a échoué. Consultez les journaux du serveur."})
