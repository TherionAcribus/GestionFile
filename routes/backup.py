import json
import zipfile
import os
#from app import ConfigOption, ConfigVersion
from datetime import datetime
from flask import redirect, url_for, Response, current_app, send_file
from io import BytesIO


def backup_config_all(ConfigOption, ConfigVersion):
    print("backup_config_all")
    try:
        # Récupération des options de configuration
        config_options = ConfigOption.query.all()
        config_versions = ConfigVersion.query.filter_by(key="config_version").first()

        # Transformation des options en un dictionnaire
        configurations_json = {
            option.key: option.value_bool or option.value_str or option.value_int or option.value_text
            for option in config_options
        }

        # Récupération des informations de la version
        backup_data = {
            "name": "config_backup",
            "type": "default",
            "version": config_versions.version if config_versions else "unknown",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comments": "Sauvegarde des configurations de PharmaFile",
            "configurations": configurations_json
        }

        # Nom du fichier de sauvegarde avec timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'config_backup_{timestamp}.json'

        # Retourner le fichier JSON en réponse
        return Response(
            json.dumps(backup_data, indent=4, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        print(e)
        #flash(f'An error occurred: {e}', 'danger')
        return redirect(url_for('admin'))
    
    
def backup_staff(Pharmacist, ConfigVersion):
    try:
        pharmacists = Pharmacist.query.all()
        pharmacists_json = [
            {
                "id": pharmacist.id,
                "name": pharmacist.name,
                "initials": pharmacist.initials,
                "language": pharmacist.language,
                "is_active": pharmacist.is_active,
                "activities": [activity.id for activity in pharmacist.activities]
            }
            for pharmacist in pharmacists
        ]
        
        backup_data = {
            "name": "gf_staff",
            "type": "backup",
            "version": "0.1",
            "comments": "Backup de l'équipe",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "staff": pharmacists_json
        }
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'gf_backup_staff_{timestamp}.json'
        
        return Response(
            json.dumps(backup_data),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        current_app.logger.error(f'An error occurred: {e}', exc_info=True)
        return redirect(url_for('index'))
    


def backup_counters(Counter, ConfigVersion):
    try:
        counters = Counter.query.all()
        counters_json = [
            {
                "id": counter.id,
                "name": counter.name,
                "is_active": counter.is_active,
                "non_actions": counter.non_actions,
                "priority_actions": counter.priority_actions,
                "staff_id": counter.staff_id,
                "activities": [activity.id for activity in counter.activities],
                "order": counter.order
            }
            for counter in counters
        ]
        
        backup_data = {
            "name": "gf_counters",
            "type": "backup",
            "version": "0.1",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comments": "Backup des comptoirs",
            "counters": counters_json
        }
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'gf_backup_counters_{timestamp}.json'
        
        return Response(
            json.dumps(backup_data),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        print(e)
        return redirect(url_for('index'))
    

def backup_schedules(ActivitySchedule, ConfigVersion):
    try:
        schedules = ActivitySchedule.query.all()
        schedules_json = [
            {
                "id": schedule.id,
                "name": schedule.name,
                "start_time": schedule.start_time.strftime("%H:%M:%S") if schedule.start_time else None,
                "end_time": schedule.end_time.strftime("%H:%M:%S") if schedule.end_time else None,
                "weekdays": [weekday.id for weekday in schedule.weekdays],
                "activities": [activity.id for activity in schedule.activities]
            }
            for schedule in schedules
        ]
        
        backup_data = {
            "name": "gf_activity_schedules",
            "type": "backup",
            "version": "0.1",
            "comments": "Backup des plages horaires",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "activity_schedules": schedules_json
        }
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'backup_activity_schedules_{timestamp}.json'
        
        return Response(
            json.dumps(backup_data),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        current_app.logger.error(f'An error occurred: {e}', exc_info=True)
        return redirect(url_for('activity'))
    

def backup_algorules(AlgoRule, ConfigVersion):
    try:
        # Récupération de toutes les règles d'algorithme
        algo_rules = AlgoRule.query.all()
        algo_rules_json = [
            {
                "id": rule.id,
                "name": rule.name,
                "description": rule.description,
                "activity_id": rule.activity_id,
                "min_patients": rule.min_patients,
                "max_patients": rule.max_patients,
                "max_overtaken": rule.max_overtaken,
                "start_time": rule.start_time.strftime("%H:%M:%S"),
                "end_time": rule.end_time.strftime("%H:%M:%S"),
                "days_of_week": rule.days_of_week,
                "priority_level": rule.priority_level
            }
            for rule in algo_rules
        ]
        
        # Création des données de sauvegarde
        backup_data = {
            "name": "gf_algo_rules",
            "type": "backup",
            "version": "0.1",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comments": "Backup des règles d'algorithme",
            "algo_rules": algo_rules_json
        }
        
        # Génération du nom de fichier avec timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'gf_backup_algo_rules_{timestamp}.json'
        
        # Retourner la réponse avec le fichier JSON
        return Response(
            json.dumps(backup_data, indent=4, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        print(e)
        return redirect(url_for('index'))
    

def backup_activities(Activity, ConfigVersion):
    try:
        activities = Activity.query.all()
        activities_json = [
            {
                "id": activity.id,
                "name": activity.name,
                "letter": activity.letter,
                "inactivity_message": activity.inactivity_message,
                "notification": activity.notification,
                "is_staff": activity.is_staff,
                "staff_id": activity.staff_id,
                "schedules": [schedule.id for schedule in activity.schedules]
            }
            for activity in activities
        ]
        
        backup_data = {
            "name": "gf_activities",
            "type": "backup",
            "version": "0.1",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comments": "Backup des activités",
            "activities": activities_json
        }
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'gf_backup_activities_{timestamp}.json'
        
        return Response(
            json.dumps(backup_data, indent=4, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        print(e)
        return redirect(url_for('index'))
    

def backup_buttons(Button, ConfigVersion):
    try:
        buttons = Button.query.all()
        buttons_json = [
            {
                "id": button.id,
                "by_user": button.by_user,
                "code": button.code,
                "is_parent": button.is_parent,
                "label": button.label,
                "label_en": button.label_en,
                "is_active": button.is_active,
                "is_present": button.is_present,
                "shape": button.shape,
                "image_url": button.image_url,
                "background_color": button.background_color,
                "text_color": button.text_color,
                "order": button.order,
                "activity_id": button.activity_id,
                "parent_button_id": button.parent_button_id
            }
            for button in buttons
        ]
        
        backup_data = {
            "name": "gf_buttons",
            "type": "backup",
            "version": "0.1",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comments": "Backup des boutons",
            "buttons": buttons_json
        }
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f'gf_backup_buttons_{timestamp}.json'
        
        return Response(
            json.dumps(backup_data, indent=4, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment;filename={backup_filename}'}
        )
    except Exception as e:
        print(e)
        return redirect(url_for('index'))
    

def backup_databases():
    # Préparer un fichier ZIP en mémoire
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        # Liste des bases de données
        databases = {
            'queuedatabase.db': current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''),
            'queueschedulerdatabase.db': current_app.config.get('SQLALCHEMY_DATABASE_URI_SCHEDULER', '').replace('sqlite:///', ''),
            'userdatabase.db': current_app.config['SQLALCHEMY_BINDS']['users'].replace('sqlite:///', '')
        }

        for db_name, db_path in databases.items():
            if db_path:  # Vérifier que le chemin n'est pas vide
                # Éviter la duplication de "instance" dans le chemin
                if not db_path.startswith('instance/'):
                    db_path = os.path.join(current_app.instance_path, db_path)
                
                if os.path.exists(db_path):
                    # Ajouter chaque base de données au fichier ZIP
                    zip_file.write(db_path, arcname=db_name)
                else:
                    return f"Database file {db_name} not found at {db_path}", 404

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f'backup_databases_{timestamp}.zip')
