import json
import os
from datetime import datetime


# Mise à jour ou initialisation des options par défaut
def init_default_options_db_from_json(app, db, ConfigVersion, ConfigOption):
    json_file='static/json/default_config.json'
    with app.app_context():        
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_version = ConfigVersion.query.filter_by(key="config_version").first()

            # si la version n'est pas à jour ou n'existe pas
            if not current_version or current_version.version != data['version']:
                # pas à jour on update la ligne
                if current_version:
                    print("Mise à jour des options par défaut")
                    current_version.version = data['version']
                # N'existe pas on l'ajoute
                else:
                    print("Création des options par défaut")
                    new_version = ConfigVersion(key="config_version", version=data['version'])
                    db.session.add(new_version)

                # Dans les 2 cas, on met à jour uniquement ce qui n'existe pas
                for key, value in data['configurations'].items():
                    config_option = ConfigOption.query.filter_by(key=key).first()
                    if not config_option:  
                        print("Création ", key)
                        new_option = ConfigOption(
                            key=key,
                            value_str=value if isinstance(value, str) and len(value) < 200 else None,
                            value_int=value if isinstance(value, int) else None,
                            value_bool=value if isinstance(value, bool) else None,
                            value_text=value if isinstance(value, str) and len(value) >= 200 else None
                        )
                        db.session.add(new_option)
                db.session.commit()
                print("Base de données des options mise à jour")



def init_update_default_buttons_db_from_json(ConfigVersion, Button, db):
    """ Mise à jour de la BDD des boutons par defaut. Init """

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
        
        # Mise à jour ou ajout de boutons
        for button_data in data['buttons']:
            print("maj boutons")
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
                new_button = Button(**button_data)
                db.session.add(new_button)
        
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


# Charge des valeurs qui ne sont pas amener à changer avant redémarrage APP
def load_configuration(app, ConfigOption):
    print("Loading configuration...")
    # Supposons que cette fonction charge la configuration depuis la base de données
    config_option = ConfigOption.query.filter_by(key="numbering_by_activity").first()
    if config_option:
        app.config['NUMBERING_BY_ACTIVITY'] = config_option.value_bool