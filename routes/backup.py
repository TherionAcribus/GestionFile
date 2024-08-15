import json
#from app import ConfigOption, ConfigVersion
from datetime import datetime
from flask import redirect, url_for, Response, current_app


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
        return redirect(url_for('activity'))