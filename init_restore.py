import json
import os
import zipfile
import mysql.connector
from datetime import datetime, time
from flask import redirect, url_for, render_template, current_app
from io import BytesIO

from models import db, ConfigVersion, ConfigOption, Weekday, ActivitySchedule, Activity, Counter, Pharmacist, Button, AlgoRule, Language, Text, TextTranslation, Patient, PatientCssVariable, AnnounceCssVariable, DashboardCard

def init_default_patient_css_variables_db_from_json():
    json_file='static/json/default_patient_css_variables.json'
    load_patient_css_variables_from_json(json_file, restore=False)

def load_patient_css_variables_from_json(json_file, restore=False):
    with current_app.app_context():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_version = ConfigVersion.query.filter_by(config_key="patient_css_variables_version").first()

            if not current_version or current_version.version != data['version'] or restore:
                if current_version:
                    current_app.logger.info(f"Mise à jour de la table CSS DATA VARIABLES : {current_version.version} vers {data['version']}")
                    current_version.version = data['version']
                else:
                    current_app.logger.info(f"Ajout de la version CSS DATA VARIABLES : {data['version']}")
                    new_version = ConfigVersion(config_key="patient_css_variables_version", version=data['version'])
                    db.session.add(new_version)

                for key, value in data['variables'].items():
                    css_variable = PatientCssVariable.query.filter_by(variable=key).first()
                    
                    if css_variable:
                        # pas encore ajouté à l'interface. A FAIRE + Gestion de la restauration / Sauvegarde
                        if restore:
                            current_app.logger.info(f"Mise à jour de {key}")
                            css_variable.value = value
                    else:
                        current_app.logger.info(f"Création de {key}")
                        new_variable = PatientCssVariable(
                            variable=key,
                            value=value)
                        db.session.add(new_variable)

                db.session.commit()
                current_app.logger.info("Table CSS DATA VARIABLES mise à jour")


def init_default_announce_css_variables_db_from_json():
    json_file='static/json/default_announce_css_variables.json'
    load_announce_css_variables_from_json(json_file, restore=False)


def load_announce_css_variables_from_json(json_file, restore=False):
    with current_app.app_context():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_version = ConfigVersion.query.filter_by(config_key="announce_css_variables_version").first()

            if not current_version or current_version.version != data['version'] or restore:
                if current_version:
                    current_app.logger.info(f"Mise à jour de la table CSS DATA VARIABLES : {current_version.version} vers {data['version']}")
                    current_version.version = data['version']
                else:
                    current_app.logger.info(f"Ajout de la version CSS DATA VARIABLES : {data['version']}")
                    new_version = ConfigVersion(config_key="announce_css_variables_version", version=data['version'])
                    db.session.add(new_version)

                for key, value in data['variables'].items():
                    css_variable = AnnounceCssVariable.query.filter_by(variable=key).first()
                    
                    if css_variable:
                        # pas encore ajouté à l'interface. A FAIRE + Gestion de la restauration / Sauvegarde
                        if restore:
                            current_app.logger.info(f"Mise à jour de {key}")
                            css_variable.value = value
                    else:
                        current_app.logger.info(f"Création de {key}")
                        new_variable = AnnounceCssVariable(
                            variable=key,
                            value=value)
                        db.session.add(new_variable)

                db.session.commit()
                current_app.logger.info("Table CSS DATA VARIABLES mise à jour")


# CONFIGURATION DE L'APP       
def init_default_options_db_from_json():
    json_file='static/json/default_config.json'
    load_config_table_from_json(json_file, db, ConfigVersion, ConfigOption, restore=False)
    
def restore_config_table_from_json(db, ConfigVersion, ConfigOption, request):
    current_app.logger.info("Restauration de la table CONFIG")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                json_file = os.path.join('static/tmp', file.filename)
                file.save(json_file)
                load_config_table_from_json(json_file, db, ConfigVersion, ConfigOption, restore=True)
                os.remove(json_file)
                # rechargement du "cache"
                current_app.load_configuration()
                current_app.display_toast(success=True, message="Restauration reussie")
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            os.remove(json_file)
            current_app.logger.info(f'An error occurred: {e}')
            current_app.display_toast(success=False, message=e)
        return redirect(url_for('admin_app'))
    return redirect(url_for('admin_app'))  

# Mise à jour ou initialisation des options par défaut
def load_config_table_from_json(json_file, db, ConfigVersion, ConfigOption, restore=False):
    with current_app.app_context():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_version = ConfigVersion.query.filter_by(config_key="config_version").first()

            if not current_version or current_version.version != data['version'] or restore:
                if current_version:
                    current_app.logger.info(f"Mise à jour de la table CONFIG : {current_version.version} vers {data['version']}")
                    current_version.version = data['version']
                else:
                    current_app.logger.info(f"Ajout de la version CONFIG : {data['version']}")
                    new_version = ConfigVersion(config_key="config_version", version=data['version'])
                    db.session.add(new_version)

                for key, value in data['configurations'].items():
                    config_option = ConfigOption.query.filter_by(config_key=key).first()
                    
                    if config_option:
                        if restore:
                            current_app.logger.info(f"Mise à jour de {key}")
                            config_option.value_str = value if isinstance(value, str) and len(value) < 200 else None
                            config_option.value_int = value if isinstance(value, int) else None
                            config_option.value_bool = value if isinstance(value, bool) else None
                            config_option.value_text = value if isinstance(value, str) and len(value) >= 200 else None
                    else:
                        current_app.logger.info(f"Création de {key}")
                        new_option = ConfigOption(
                            config_key=key,
                            value_str=value if isinstance(value, str) and len(value) < 200 else None,
                            value_int=value if isinstance(value, int) else None,
                            value_bool=value if isinstance(value, bool) else None,
                            value_text=value if isinstance(value, str) and len(value) >= 200 else None
                        )
                        db.session.add(new_option)

                db.session.commit()
                current_app.logger.info("Table CONFIG mise à jour")

# TABLE EQUIPE 

def init_staff_data_from_json():
    json_file='static/json/default_staff.json'    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="staff_version").first()
    if not current_version or current_version.version != data['version']:
        current_app.logger.info(f"Mise à jour de la table STAFF : {current_version} vers {data['version']}")

        if not current_version:
            create_version_number(ConfigVersion, data, db, key="staff_version")
            staff_restore_init(Pharmacist, Activity, db, restore=False, file_path="static/json/default_staff.json")


def restore_staff(db, ConfigVersion, Pharmacist, Activity, request):    
    current_app.logger.info("staff_restore")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)
                
                # on s'assure que le fichier est bien un fichier gérant le staff
                with open(file_path, 'r', encoding='utf-8') as file:
                    backup_data = json.load(file)
                    if backup_data.get("name") != "gf_staff" or backup_data.get("type") not in ["backup", "default"]:
                        current_app.logger.error('Invalid backup file.')
                        return "", 200
                
                # on efface la table puis on l'initialise avec le nouveau fichier
                db.session.query(Pharmacist).delete()
                db.session.commit()
                print("effacement de la table")
                
                staff_restore_init(Pharmacist, Activity, db, restore=True, file_path=file_path)
                os.remove(file_path)  # Optionally remove the file after processing
                
                print("rechargement du cache")
                current_app.display_toast(success=True, message="Restauration reussie")
                
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
        return redirect(url_for('staff'))
    return render_template('restore.html')


def staff_restore_init(Pharmacist, Activity, db, restore, file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)

        pharmacists_json = backup_data.get("staff", [])

        for pharmacist_json in pharmacists_json:
            activities_ids = pharmacist_json.pop('activities', [])
            pharmacist = Pharmacist.query.get(pharmacist_json['id'])

            if not pharmacist:
                pharmacist = Pharmacist(**pharmacist_json)
                db.session.add(pharmacist)  # Ajouter le nouveau pharmacien à la session
                db.session.flush()  # Flush pour obtenir l'ID avant d'ajouter des relations
            else:
                pharmacist.from_dict(pharmacist_json)

            pharmacist.activities = []  # Vider les activités actuelles

            for activity_id in activities_ids:
                activity = Activity.query.get(activity_id)
                if activity:
                    if activity not in pharmacist.activities:  # Vérifier si la relation existe déjà
                        pharmacist.activities.append(activity)

        try:
            db.session.commit()
            current_app.logger.info('Restoration successful!')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
            raise


# COMPTOIRS

def init_counters_data_from_json():
    json_file='static/json/default_counters.json'    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="counters_version").first()
    if not current_version or current_version.version != data['version']:
        current_app.logger.info(f"Mise à jour de la table STAFF : {current_version} vers {data['version']}")
        create_version_number(ConfigVersion, data, db, key="counters_version")
        if not current_version:
            counter_restore_init(Counter, Activity, ConfigVersion, db, restore=False, file_path=json_file)


def restore_counters(db, ConfigVersion, Counter, Activity, request):
    current_app.logger.info("counter_restore")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)

                with open(file_path, 'r', encoding='utf-8') as file:
                    backup_data = json.load(file)
                    if backup_data.get("name") != "gf_counters" or backup_data.get("type") not in ["backup", "default"]:
                        current_app.logger.error('Invalid backup file.')
                        return "", 200
                    
                # on efface la table puis on l'initialise avec le nouveau fichier
                db.session.query(Counter).delete()
                db.session.commit()
                print("effacement de la table")

                counter_restore_init(Counter, Activity, ConfigVersion, db, restore=True, file_path=file_path)
                os.remove(file_path)  

                current_app.display_toast(success=True, message="Restauration reussie")
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            os.remove(file_path)  
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
        return redirect(url_for('admin_counter'))
    return render_template('restore.html')


def counter_restore_init(Counter, Activity, ConfigVersion, db, restore, file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)

        counters_json = backup_data.get("counters", [])

        with db.session.no_autoflush:  # Empêche l'autoflush pendant que nous travaillons sur les objets
            for counter_json in counters_json:
                activities_ids = counter_json.pop('activities', [])
                counter = Counter.query.get(counter_json['id'])

                if not counter:
                    counter = Counter(**counter_json)
                    db.session.add(counter)  # Ajouter le nouveau comptoir à la session
                    db.session.flush()  # Flush pour obtenir l'ID avant d'ajouter des relations
                else:
                    counter.from_dict(counter_json)

                # Effacer les activités existantes
                counter.activities = []

                # Ajouter les nouvelles activités, en vérifiant les doublons
                for activity_id in activities_ids:
                    activity = Activity.query.get(activity_id)
                    if activity and activity not in counter.activities:
                        counter.activities.append(activity)

        try:
            db.session.commit()
            current_app.logger.info('Counter restoration successful!')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
            raise


# ACTIVITIES SCHEDULES

def restore_schedules(db, ConfigVersion, ActivitySchedule, Activity, Weekday, request):
    current_app.logger.info("staff_restore" + request.method)
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)
                print("RESTORE ACTIVITY SCHEDULES", file_path)
                schedules_restore_init(ActivitySchedule, Activity, Weekday, ConfigVersion, db, file_path, is_restore=True)
                os.remove(file_path)  # Optionally remove the file after processing
            else:
                print('Invalid file format. Please upload a JSON file.', 'danger')
        except Exception as e:
            db.session.rollback()
            print(f'An error occurred: {e}', 'danger')
        return redirect(url_for('activity'))
    return render_template('restore.html')


def schedules_restore_init(ActivitySchedule, Activity, Weekday, ConfigVersion, db, file_path, is_restore=False):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)
        
        # Vérification des métadonnées
        if backup_data.get("name") != "gf_activity_schedules" or backup_data.get("type") not in ["backup", "default"]:
            current_app.logger.error('Invalid backup file.')
            if is_restore:
                return redirect(url_for('index'))

        # Mise à jour de la version si ce n'est pas une restauration
        if not is_restore:
            current_version = ConfigVersion.query.filter_by(config_key="activity_schedule_version").first()
            if not current_version or current_version.version != backup_data['version']:
                if not current_version:
                    new_version = ConfigVersion(key="activity_schedule_version", version=backup_data['version'])
                    db.session.add(new_version)
                else:
                    current_version.version = backup_data['version']

        # Mise à jour des données ActivitySchedule
        schedules_json = backup_data.get("activity_schedules", [])
        for schedule_json in schedules_json:
            print("schedule_json", schedule_json)
            weekdays_ids = schedule_json.pop('weekdays', [])
            activities_ids = schedule_json.pop('activities', [])
            schedule = db.session.get(ActivitySchedule, schedule_json['id'])
            if not schedule:
                schedule = ActivitySchedule(**schedule_json)
            else:
                schedule.from_dict(schedule_json)

            schedule.weekdays = []
            for weekday_id in weekdays_ids:
                weekday = db.session.get(Weekday, weekday_id)
                if weekday:
                    schedule.weekdays.append(weekday)

            schedule.activities = []
            for activity_id in activities_ids:
                activity = db.session.get(Activity, activity_id)
                if activity:
                    schedule.activities.append(activity)

            db.session.add(schedule)
        db.session.commit()
        current_app.logger.info('Restoration successful!')


# ACTIVITIES 

def init_default_activities_db_from_json():
    json_file = 'static/json/default_activities.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="activities_version").first()
    if not current_version or current_version.version != data['version']:
        if not current_version:
            create_version_number(ConfigVersion, data, db, key="activities_version")
        else:
            current_version.version = data['version']
        current_app.logger.info(f"Mise à jour de la table ACTIVITIES : {current_version} vers {data['version']}")

        if not current_version:
            activity_restore_init(Activity, ActivitySchedule, db, restore=False, file_path=json_file)


def restore_activities(db, ConfigVersion, Activity, ActivitySchedule, request):
    current_app.logger.info("activity_restore")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)

                with open(file_path, 'r', encoding='utf-8') as file:
                    backup_data = json.load(file)
                    if backup_data.get("name") != "gf_activities" or backup_data.get("type") not in ["backup", "default"]:
                        current_app.logger.error('Invalid backup file.')
                        return "", 200

                # Effacer la table avant de la réinitialiser avec le nouveau fichier
                db.session.query(Activity).delete()
                db.session.commit()
                print("Effacement de la table ACTIVITIES")

                activity_restore_init(Activity, ActivitySchedule, db, restore=True, file_path=file_path)
                os.remove(file_path)

                current_app.display_toast(success=True, message="Restauration réussie")
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            os.remove(file_path)
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
        return redirect(url_for('admin_activity'))
    return render_template('restore.html')


def activity_restore_init(Activity, ActivitySchedule, db, restore, file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)

        activities_json = backup_data.get("activities", [])

        with db.session.no_autoflush:  # Empêche l'autoflush pendant que nous travaillons sur les objets
            for activity_json in activities_json:
                schedules_ids = activity_json.pop('schedules', [])
                activity = Activity.query.get(activity_json['id'])

                if not activity:
                    activity = Activity()
                    activity.from_dict(activity_json)
                    db.session.add(activity)
                else:
                    activity.from_dict(activity_json)

                    # Effacer les horaires existants
                    activity.schedules = []

                # Ajouter les nouveaux horaires
                for schedule_id in schedules_ids:
                    schedule = ActivitySchedule.query.get(schedule_id)
                    if schedule and schedule not in activity.schedules:
                        activity.schedules.append(schedule)

        try:
            db.session.commit()
            current_app.logger.info('Activity restoration successful!')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
            raise



# REGLES DE L'ALGORITHME

def init_default_algo_rules_db_from_json():
    json_file = 'static/json/default_algo_rules.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="algo_rules_version").first()
    if not current_version or current_version.version != data['version']:
        current_app.logger.info(f"Mise à jour de la table ALGO_RULES : {current_version} vers {data['version']}")

        if not current_version:
            create_version_number(ConfigVersion, data, db, key="algo_rules_version")
            algo_rule_restore_init(AlgoRule, db, restore=False, file_path=json_file)


def restore_algorules(db, ConfigVersion, AlgoRule, request):
    current_app.logger.info("algo_rule_restore")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)

                with open(file_path, 'r', encoding='utf-8') as file:
                    backup_data = json.load(file)
                    if backup_data.get("name") != "gf_algo_rules" or backup_data.get("type") not in ["backup", "default"]:
                        current_app.logger.error('Invalid backup file.')
                        return "", 200

                # Effacer la table avant de la réinitialiser avec le nouveau fichier
                db.session.query(AlgoRule).delete()
                db.session.commit()
                print("Effacement de la table ALGO_RULES")

                algo_rule_restore_init(AlgoRule, db, restore=True, file_path=file_path)
                os.remove(file_path)

                current_app.display_toast(success=True, message="Restauration réussie")
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            os.remove(file_path)
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
        return redirect(url_for('admin_algo'))
    return render_template('restore.html')


def algo_rule_restore_init(AlgoRule, db, restore, file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)

        algo_rules_json = backup_data.get("algo_rules", [])

        with db.session.no_autoflush:  # Empêche l'autoflush pendant que nous travaillons sur les objets
            for rule_json in algo_rules_json:
                start_time_obj = datetime.strptime(rule_json['start_time'], '%H:%M:%S').time()
                end_time_obj = datetime.strptime(rule_json['end_time'], '%H:%M:%S').time()

                rule = AlgoRule.query.get(rule_json['id'])

                if not rule:
                    rule = AlgoRule(
                        name=rule_json['name'],
                        activity_id=rule_json['activity_id'],
                        priority_level=rule_json['priority_level'],
                        min_patients=rule_json['min_patients'],
                        max_patients=rule_json['max_patients'],
                        max_overtaken=rule_json['max_overtaken'],
                        start_time=start_time_obj,
                        end_time=end_time_obj,
                        days_of_week=rule_json['days_of_week']
                    )
                    db.session.add(rule)
                else:
                    rule.name = rule_json['name']
                    rule.activity_id = rule_json['activity_id']
                    rule.priority_level = rule_json['priority_level']
                    rule.min_patients = rule_json['min_patients']
                    rule.max_patients = rule_json['max_patients']
                    rule.max_overtaken = rule_json['max_overtaken']
                    rule.start_time = start_time_obj
                    rule.end_time = end_time_obj
                    rule.days_of_week = rule_json['days_of_week']

        try:
            db.session.commit()
            current_app.logger.info('AlgoRule restoration successful!')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
            raise


# BOUTONS 

def init_default_buttons_db_from_json():
    json_file = 'static/json/default_buttons.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="buttons_version").first()
    if not current_version or current_version.version != data['version']:
        current_app.logger.info(f"Mise à jour de la table BUTTONS : {current_version} vers {data['version']}")

        if not current_version:
            create_version_number(ConfigVersion, data, db, key="buttons_version")
            button_restore_init(Button, Activity, db, restore=False, file_path=json_file)


def restore_buttons(db, ConfigVersion, Button, Activity, request):
    current_app.logger.info("button_restore")
    if request.method == 'POST':
        try:
            file = request.files['file']
            if file and file.filename.endswith('.json'):
                file_path = os.path.join('static/json', file.filename)
                file.save(file_path)

                with open(file_path, 'r', encoding='utf-8') as file:
                    backup_data = json.load(file)
                    if backup_data.get("name") != "gf_buttons" or backup_data.get("type") not in ["backup", "default"]:
                        current_app.logger.error('Invalid backup file.')
                        return "", 200

                # Effacer la table avant de la réinitialiser avec le nouveau fichier
                # Supprimer d'abord les boutons enfants
                db.session.query(Button).filter(Button.parent_button_id.isnot(None)).delete(synchronize_session=False)

                # Ensuite, supprimer les boutons parents
                db.session.query(Button).filter(Button.parent_button_id.is_(None)).delete(synchronize_session=False)
                db.session.commit()
                print("Effacement de la table BUTTONS")

                button_restore_init(Button, Activity, db, restore=True, file_path=file_path)
                os.remove(file_path)

                current_app.display_toast(success=True, message="Restauration réussie")
            else:
                current_app.logger.error('Invalid file format. Please upload a JSON file.')
        except Exception as e:
            db.session.rollback()
            os.remove(file_path)
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
        return redirect(url_for('admin_patient'))
    return render_template('restore.html')


def button_restore_init(Button, Activity, db, restore, file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        backup_data = json.load(file)

        buttons_json = backup_data.get("buttons", [])

        # Restaurer d'abord les boutons parents
        for button_json in buttons_json:
            if button_json.get('parent_button_id') is None:  # Si le bouton n'a pas de parent, c'est un parent lui-même
                restore_button(button_json, Button, Activity, db)

        # Restaurer ensuite les boutons enfants
        for button_json in buttons_json:
            if button_json.get('parent_button_id') is not None:  # Si le bouton a un parent
                restore_button(button_json, Button, Activity, db)

        try:
            db.session.commit()
            current_app.logger.info('Button restoration successful!')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'An error occurred during commit: {e}', exc_info=True)
            raise

def restore_button(button_json, Button, Activity, db):
    button = db.session.get(Button, button_json['id'])

    if not button:
        button = Button()
        button.from_dict(button_json, db.session)
        db.session.add(button)
    else:
        button.from_dict(button_json, db.session)

# DATABASE

def restore_databases(request, database):
    
    if database == "sqlite":
        restore_sqlite(request)
        return "", 200
    elif database == "mysql":
        restore_mysql(request)
        return "", 200


def restore_sqlite(request):
    file = request.files.get('file')
    if file:
        # Charger le fichier ZIP en mémoire
        zip_buffer = BytesIO(file.read())

        with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
            # Liste des bases de données avec leurs chemins
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

                    # Vérifier que le fichier ZIP contient bien la base de données
                    if db_name in zip_file.namelist():
                        # Extraire et remplacer la base de données existante
                        with zip_file.open(db_name) as source, open(db_path, 'wb') as target:
                            target.write(source.read())
                    else:
                        return f"Database file {db_name} not found in the ZIP", 400
                    
            current_app.load_configuration()

        return "All databases restored successfully"
    else:
        return "No file uploaded", 400


def restore_mysql(request):
    file = request.files.get('file')

    current_app.logout_all()

    if file and file.filename.endswith('.zip'):
        zip_buffer = BytesIO(file.read())

        connection = mysql.connector.connect(
            host='localhost',
            user=current_app.config["MYSQL_USER"],
            password=current_app.config["MYSQL_PASSWORD"],
        )
        cursor = connection.cursor()

        with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
            for sql_file in zip_file.namelist():
                db_name = sql_file.replace('.sql', '')                    
                sql_content = zip_file.read(sql_file)
                restore_mysql_database(db_name, sql_content, cursor, connection)
        
        connection.commit()
        cursor.close()
        connection.close()

        return "All databases restored successfully", 200
    else:
        return "No file uploaded", 400
    

def restore_mysql_database(db_name, sql_content, cursor, connection):
    print(f"Starting restoration of database {db_name}")
    
    # Sélectionner la base de données avant de restaurer
    cursor.execute(f"USE {db_name}")
    
    # Désactiver les vérifications des clés étrangères
    cursor.execute("SET FOREIGN_KEY_CHECKS=0;")
    connection.commit()

    # Supprimer les tables existantes
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    for table in tables:
        print(f"TABLE TO DROP {table}")
        cursor.execute(f"DROP TABLE IF EXISTS {table[0]}")
        connection.commit()  # Commit after each table drop
    
    # Diviser le contenu du fichier SQL en statements individuels
    statements = sql_content.decode('utf-8').split(';')
    for statement in statements:
        statement = statement.strip()
        if statement:
            print(f"STATEMENT {statement[:50]}...")  # Print first 50 chars to avoid long output
            try:
                cursor.execute(statement)
                connection.commit()  # Commit after each statement
            except Exception as e:
                print(f"Error executing statement: {e}")
    
    # Réactiver les vérifications des clés étrangères
    cursor.execute("SET FOREIGN_KEY_CHECKS=1;")
    connection.commit()

    print(f"Database {db_name} restored successfully")

    return True


# A TRIER 


def init_update_default_translations_db_from_json():
    json_file = 'static/json/default_translations.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(config_key="translations_version").first()
    if not current_version or current_version.version != data['version']:
        if current_version:
            current_version.version = data['version']
        else:
            create_version_number(ConfigVersion, data, db, key="translations_version")
        
        texts_data = data["texts"]
        for key, translations in texts_data.items():
            text = Text.query.filter_by(text_key=key).first()
            if text:
                for lang_code, translation in translations.items():
                    language = Language.query.filter_by(code=lang_code).first()
                    if language:
                        text_trans = TextTranslation.query.filter_by(text_id=text.id, language_id=language.id).first()
                        if text_trans:
                            text_trans.translation = translation
                        else:
                            new_text_trans = TextTranslation(
                                text_id=text.id,
                                language_id=language.id,
                                translation=translation
                            )
                            db.session.add(new_text_trans)
        
        db.session.commit()
        print("Database updated to version:", data['version'])


def init_default_languages_db_from_json():
    """ Remplit la BDD des langues par defaut. Uniquement au 1er lancement.
    Permet de ne pas avoir à créer les langues de base : FR, EN """
    json_file = 'static/json/default_languages.json'
    # Vérifier si la table est vide
    if Language.query.first() is None:
        print("Initialisation des langues...")
        # Charger les activités depuis le fichier JSON
        if os.path.exists(json_file):
            with open(json_file, 'r', encoding='utf-8') as f:
                languages = json.load(f)

            # Ajouter chaque activité à la base de données
            for language in languages:
                new_language = Language(                    
                    code=language['code'],
                    name=language['name'],
                    translation=language['translation'],
                    is_active=language['is_active'],
                    sort_order=language['sort_order'],
                    voice_model=language['voice_model'],
                    voice_gtts_name=language['voice_gtts_name'],
                )
                db.session.add(new_language)

            # Valider les changements
            db.session.commit()
            print("Langues ajoutées avec succès.")

        else:
            print(f"Fichier {json_file} introuvable.")


def init_or_update_default_texts_db_from_json():
    json_file = 'static/json/default_texts.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print("DATA VERSION", data['version'])

    current_version = ConfigVersion.query.filter_by(config_key="texts_version").first()
    print("Data version:", data['version'])
    if not current_version or current_version.version != data['version']:
        # Mise à jour de la version
        if current_version:
            current_version.version = data['version']
        else:
            create_version_number(ConfigVersion, data, db, key="texts_version")

        # Mise à jour ou ajout de textes
        for text_data in data['texts']:
            text = Text.query.filter_by(text_key=text_data['key']).first()
            if not text:
                new_text = Text(text_key=text_data['key'])
                db.session.add(new_text)
            else:
                text.text = text_data['value']

        db.session.commit()
        print("Database updated to version:", data['version'])


def init_default_dashboard_db_from_json():
    json_file = 'static/json/default_dashboard.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print("DATA VERSION", data['version'])

    current_version = ConfigVersion.query.filter_by(config_key="dashboard_version").first()
    print("Data version:", data['version'])
    if not current_version or current_version.version != data['version']:
        # Mise à jour de la version
        if current_version:
            current_version.version = data['version']
        else:
            create_version_number(ConfigVersion, data, db, key="dashboard_version")

        # Mise à jour ou ajout de textes
        for dashboard_data in data['dashboard']:
            dashboard_card = DashboardCard.query.filter_by(id=dashboard_data['id']).first()
            if not dashboard_card:
                new_dashboard_card = DashboardCard(name=dashboard_data['name'],
                                                    visible=dashboard_data['visible'],
                                                    position=dashboard_data['position'],
                                                    size=dashboard_data['size'],
                                                    color=dashboard_data['color'])
                db.session.add(new_dashboard_card)

        db.session.commit()
        print("Database updated to version:", data['version'])


def init_days_of_week_db_from_json():
    if Weekday.query.first() is None:
        current_app.logger.info("Initialisation des jours de la semaine...")
        days = [
            {'french': 'Lundi', 'english': 'monday', 'abbreviation': 'mon'},
            {'french': 'Mardi', 'english': 'tuesday', 'abbreviation': 'tue'},
            {'french': 'Mercredi', 'english': 'wednesday', 'abbreviation': 'wed'},
            {'french': 'Jeudi', 'english': 'thursday', 'abbreviation': 'thu'},
            {'french': 'Vendredi', 'english': 'friday', 'abbreviation': 'fri'},
            {'french': 'Samedi', 'english': 'saturday', 'abbreviation': 'sat'},
            {'french': 'Dimanche', 'english': 'sunday', 'abbreviation': 'sun'},
        ]
        for day in days:
            new_day = Weekday(name=day['french'], english_name=day['english'], abbreviation=day['abbreviation'])
            db.session.add(new_day)
        db.session.commit()


def init_activity_schedules_db_from_json():
    if ActivitySchedule.query.first() is None:
        current_app.logger.info("Initialisation des horaires d'activité...")

        with open('static/json/default_schedules.json', 'r', encoding='utf-8') as file:
            schedules = json.load(file)

        for schedule in schedules:
            # Convertir les heures de début et de fin en objets time
            start_time_obj = time.fromisoformat(schedule['start'])
            end_time_obj = time.fromisoformat(schedule['end'])
            new_schedule = ActivitySchedule(
                name=schedule['name'],
                start_time=start_time_obj,
                end_time=end_time_obj
            )
        
            # Associer les jours de la semaine
            day_ids = [int(day.strip()) for day in schedule['days'].split(',')]
            weekdays = Weekday.query.filter(Weekday.id.in_(day_ids)).all()
            new_schedule.weekdays.extend(weekdays)
            db.session.add(new_schedule)

        db.session.commit()



def clear_counter_table():
    """ 
    Repasse tous les comptoirs qui n'ont pas de patients en inactif 
    Utile au rédémarrage du serveur pour nettoyé la base de données si des patients ont été supprime
    Egalement lors de la suppression / mise à jour manuelle d'un patient via l'interface admin
    """
    # Identifie les comptoirs sans patients
    counters_without_patients = (
        Counter.query.outerjoin(Patient)
        .group_by(Counter.id)
        .having(db.func.count(db.case((Patient.status != 'done', 1))) == 0)
        .all()
    )

    # Mets à jour le champ is_active pour ces comptoirs
    for counter in counters_without_patients:
        counter.is_active = False

    # Enregistre les modifications dans la base de données
    db.session.commit()


def create_version_number(ConfigVersion, data, db, key):
    """ Ajoute une entrée dans Confiversion lors de la création de la table """
    new_version = ConfigVersion(
    config_key=key,
    version=data['version'],
    comments=data['comments'],
    date=datetime.now())
    db.session.add(new_version)
    db.session.commit()