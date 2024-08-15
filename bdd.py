import json
import os
from datetime import datetime, time
from flask import redirect, url_for, render_template, current_app

# CONFIGURATION DE L'APP

def init_default_options_db_from_json(db, ConfigVersion, ConfigOption):
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
            current_version = ConfigVersion.query.filter_by(key="config_version").first()

            if not current_version or current_version.version != data['version'] or restore:
                if current_version:
                    current_app.logger.info(f"Mise à jour de la table CONFIG : {current_version.version} vers {data['version']}")
                    current_version.version = data['version']
                else:
                    current_app.logger.info(f"Ajout de la version CONFIG : {data['version']}")
                    new_version = ConfigVersion(key="config_version", version=data['version'])
                    db.session.add(new_version)

                for key, value in data['configurations'].items():
                    config_option = ConfigOption.query.filter_by(key=key).first()
                    
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
                            key=key,
                            value_str=value if isinstance(value, str) and len(value) < 200 else None,
                            value_int=value if isinstance(value, int) else None,
                            value_bool=value if isinstance(value, bool) else None,
                            value_text=value if isinstance(value, str) and len(value) >= 200 else None
                        )
                        db.session.add(new_option)

                db.session.commit()
                current_app.logger.info("Table CONFIG mise à jour")

# TABLE EQUIPE 

def init_staff_data_from_json(ConfigVersion, Pharmacist, Activity, db):
    json_file='static/json/default_config.json'    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(key="staff_version").first()
    if not current_version or current_version.version != data['version']:
        current_app.logger.info(f"Mise à jour de la table STAFF : {current_version} vers {data['version']}")

        if not current_version:
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




def init_update_default_buttons_db_from_json(ConfigVersion, Button, db):
    """ Mise à jour de la BDD des boutons par defaut. Init """

    print("MAJ Boutons")
    json_file='static/json/default_buttons.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print("DATA", data)
    print("DATA VERSION", data['version'])

    current_version = ConfigVersion.query.filter_by(key="buttons_version").first()
    #print("Current version:", current_version.version)
    print("Data version:", data['version'])
    if not current_version or current_version.version != data['version']:
        # Mise à jour de la version
        # TODO Parcourir les éléments pour mettre à jour les boutons !!! 
        # TODO Quid de la Migration  !!!
        if current_version:
            print ("Updating version")
            current_version.version = data['version']
        else:
            print ("Adding version")
            new_version = ConfigVersion(
                key="buttons_version", 
                version=data['version'],
                comments=data['comments'],
                # TODO plutôt utiliser la date du json (à ajouter)
                date=datetime.now()
            )
            db.session.add(new_version)
            db.session.commit()
            
            for button_data in data['buttons']:
                new_button = Button(
                    code=button_data['code'],
                    is_parent=button_data['is_parent'],
                    label=button_data['label'],
                    by_user=False,
                    image_url=button_data['image_url'],
                    is_active=button_data['is_active'],
                    shape=button_data['shape'],
                    order=button_data['order']
                )
                db.session.add(new_button)

            db.session.commit()

        
        # Mise à jour ou ajout de boutons
        for button_data in data['buttons']:
            button = Button.query.filter_by(code=button_data['code']).first()
            # TODO Ne mettre à jour que si n'existe pas
            if button:
                button.is_parent = button_data['is_parent']
                button.label = button_data['label']
                button.by_user = False
                button.code = button_data['code']
                button.image_url = button_data['image_url']
                button.is_active = button_data['is_active']
                button.shape = button_data['shape']
            else:
                #new_button = Button(**button_data)
                #db.session.add(new_button)
                pass
        
        db.session.commit()
        print("Database updated to version:", data['version'])



def init_update_default_translations_db_from_json(ConfigVersion, TextTranslation, Text, Language, db):
    json_file = 'static/json/default_translations.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(key="translations_version").first()
    if not current_version or current_version.version != data['version']:
        if current_version:
            current_version.version = data['version']
        else:
            new_version = ConfigVersion(
                key="translations_version",
                version=data['version'],
                comments=data['comments'],
                date=datetime.now()
            )
            db.session.add(new_version)
        
        texts_data = data["texts"]
        for key, translations in texts_data.items():
            text = Text.query.filter_by(key=key).first()
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




def init_default_languages_db_from_json(Language, db):
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
                    traduction=language['traduction']
                )
                db.session.add(new_language)

            # Valider les changements
            db.session.commit()
            print("Langues ajoutées avec succès.")

        else:
            print(f"Fichier {json_file} introuvable.")


def init_or_update_default_texts_db_from_json(ConfigVersion, Text, db):
    json_file = 'static/json/default_texts.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print("DATA VERSION", data['version'])

    current_version = ConfigVersion.query.filter_by(key="texts_version").first()
    print("Data version:", data['version'])
    if not current_version or current_version.version != data['version']:
        # Mise à jour de la version
        if current_version:
            current_version.version = data['version']
        else:
            new_version = ConfigVersion(key="texts_version", version=data['version'], comments=data['comments'])
            db.session.add(new_version)

        db.session.commit()  # Commit early to save version information

        # Mise à jour ou ajout de textes
        for text_data in data['texts']:
            text = Text.query.filter_by(key=text_data['key']).first()
            if not text:
                new_text = Text(key=text_data['key'])
                db.session.add(new_text)
            else:
                text.text = text_data['value']

        db.session.commit()
        print("Database updated to version:", data['version'])


def init_default_algo_rules_db_from_json(ConfigVersion, AlgoRule, db):

    json_file = 'static/json/default_algo_rules.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    current_version = ConfigVersion.query.filter_by(key="algo_rules_version").first()
    if not current_version or current_version.version != data['version']:
        print(f"Mise à jour de algo_rules : {current_version} vers {data['version']}")
        
        # pour l'instant pas prévu de pouvoir modifier la structure. A voir si besoin un jour
        if not current_version:
            for rule in data['rules']:
                start_time_obj = datetime.strptime(rule['start_time'], '%H:%M').time()
                end_time_obj = datetime.strptime(rule['end_time'], '%H:%M').time()

                new_rule = AlgoRule(
                    name=rule['name'],
                    activity_id=rule['activity_id'],
                    priority_level=rule['priority_level'],
                    min_patients=rule['min_patients'],
                    max_patients=rule['max_patients'],
                    max_overtaken=rule['max_overtaken'],
                    start_time=start_time_obj,
                    end_time=end_time_obj,
                    days_of_week=rule['days_of_the_week']
                )
                db.session.add(new_rule)

            db.session.commit()
            update_version(db, ConfigVersion, 'algo_rules_version', data['version'], data['comments'])
        
        print("Algo_rules bien mis à jour !")


def init_days_of_week_db_from_json(Weekday, db, app):
    if Weekday.query.first() is None:
        app.logger.info("Initialisation des jours de la semaine...")
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


def init_activity_schedules_db_from_json(ActivitySchedule, Weekday, db, app):
    if ActivitySchedule.query.first() is None:
        app.logger.info("Initialisation des horaires d'activité...")

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


def update_version(db, ConfigVersion, key, version, comments):
    """ Enregistre la version du json modèle dans la base de données COnfigVersion """
    current_version = ConfigVersion.query.filter_by(key=key).first()
    if not current_version:
        new_version = ConfigVersion(key=key, version=version)
        new_version.comments = comments
        db.session.add(new_version)
    else:
        current_version.version = version
        new_version.comments = comments
    db.session.commit()


def clear_counter_table(db, Counter, Patient):
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