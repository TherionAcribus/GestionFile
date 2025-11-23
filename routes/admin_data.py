import os
from flask import Blueprint, render_template, request, jsonify, current_app
from models import db, Patient, PatientHistory, AggregatedStats, ConfigOption, JobExecutionLog
from routes.admin_security import require_permission
from scheduler_functions import archive_data, auto_archive_job, add_scheduler_clear_all_patients
from sqlalchemy import text
from datetime import datetime, timedelta
from config import time_tz

admin_data_bp = Blueprint('admin_data', __name__)

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
    days = request.form.get('days')
    compress = request.form.get('compress') == 'true'
    
    if not days:
        return jsonify({'success': False, 'message': 'Days parameter missing'})
        
    try:
        result = archive_data(int(days), compress)
        return jsonify({'success': True, 'message': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@admin_data_bp.route('/admin/data/config', methods=['POST'])
@require_permission('options')
def update_config():
    try:
        # Update ConfigOptions
        # We use the existing update_input/switch logic or do it manually here
        # Let's do it manually for specific keys
        
        keys = {
            'DATA_ARCHIVE_DAYS': ('value_int', int(request.form.get('archive_days', 365))),
            'DATA_ARCHIVE_COMPRESSED': ('value_bool', request.form.get('archive_compressed') == 'true'),
            'DATA_AUTO_ARCHIVE_ENABLED': ('value_bool', request.form.get('auto_archive_enabled') == 'true')
        }
        
        for key, (type_, value) in keys.items():
            current_app.config[key] = value
            opt = ConfigOption.query.filter_by(config_key=key).first()
            if not opt:
                opt = ConfigOption(config_key=key)
                db.session.add(opt)
            
            setattr(opt, type_, value)
            
        db.session.commit()
        
        # Manage Scheduler
        job_id = 'Auto Archive Data'
        if current_app.config['DATA_AUTO_ARCHIVE_ENABLED']:
            if not current_app.scheduler.get_job(job_id):
                current_app.scheduler.add_job(
                    id=job_id,
                    func=auto_archive_job,
                    trigger='cron',
                    hour=3, # Default 3 AM
                    minute=30
                )
        else:
            if current_app.scheduler.get_job(job_id):
                current_app.scheduler.remove_job(job_id)
        
        return jsonify({'success': True, 'message': 'Configuration updated'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@admin_data_bp.route('/admin/data/delete_aggregated', methods=['POST'])
@require_permission('options')
def delete_aggregated():
    days = request.form.get('days')
    
    if not days:
        return jsonify({'success': False, 'message': 'Paramètre jours manquant'})
        
    try:
        cutoff_date = datetime.now(time_tz).date() - timedelta(days=int(days))
        
        deleted_count = AggregatedStats.query.filter(
            AggregatedStats.date < cutoff_date
        ).delete(synchronize_session=False)
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'{deleted_count} lignes de statistiques agrégées supprimées.'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})
