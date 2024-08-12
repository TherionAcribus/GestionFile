import json
#from app import ConfigOption, ConfigVersion
from datetime import datetime
from flask import redirect, url_for, Response, current_app


def backup_config_all(ConfigOption, ConfigVersion):
    try:
        # Récupération des options de configuration
        config_options = ConfigOption.query.all()
        config_versions = ConfigVersion.query.filter_by(key="config_version").first()

        # Transformation des options en un dictionnaire
        configurations_json = {
            option.key: option.value_str or option.value_int or option.value_bool or option.value_text
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
        #flash(f'An error occurred: {e}', 'danger')
        return redirect(url_for('admin'))