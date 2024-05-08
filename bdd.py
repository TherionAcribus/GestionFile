import json
from datetime import datetime


def init_update_default_buttons_db_from_json(ConfigVersion, Button, db):
    """ Mise à jour de la BDD des boutons par defaut. Init """

    json_file='static/json/default_buttons.json'
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print("DATA", data)
    print("DATA VERSION", data['version'])

    current_version = ConfigVersion.query.filter_by(key="buttons_version").first()
    print("Current version:", current_version.version)
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
            if button:
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