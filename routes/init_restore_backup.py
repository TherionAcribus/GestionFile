import json
#from app import ConfigOption, ConfigVersion
from datetime import datetime
from flask import redirect, url_for, Response, current_app


def backup_config_all(ConfigOption, ConfigVersion):
    print("backup_config_all")
    with current_app.app_context():
        # Récupération des options de configuration
        config_options = ConfigOption.query.all()
        config_versions = ConfigVersion.query.all()
        print(config_options)
        print(config_versions)

    # Conversion en JSON
    options_json = [
        {
            "id": option.id,
            "key": option.key,
            "value_str": option.value_str,
            "value_int": option.value_int,
            "value_bool": option.value_bool,
            "value_text": option.value_text
        }
        for option in config_options
    ]

    versions_json = [
        {
            "id": version.id,
            "key": version.key,
            "version": version.version,
            "comments": version.comments,
            "date": version.date.strftime("%Y-%m-%d %H:%M:%S") if version.date else None
        }
        for version in config_versions
    ]

    # Création du dictionnaire final
    backup_data = {
        "name": "config_backup",
        "type": "backup",
        "version": "1.0",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "comments": "Backup des configurations",
        "configurations": options_json,
        "versions": versions_json
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
