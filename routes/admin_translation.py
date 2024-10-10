import json
import os
from flask import Blueprint, render_template, request, jsonify, url_for, current_app as app
from models import ConfigOption, Button, Activity, Language, Translation, db
from werkzeug.utils import secure_filename
from communication import communikation

admin_translation_bp = Blueprint('admin_translation', __name__)


@admin_translation_bp.route('/admin/translations')
def admin_translation():
    valid_tabs = ['languages', 'texts']
    tab = request.args.get('tab', 'languages')
    if tab not in valid_tabs:
        tab = 'languages'

    languages = Language.query.all()
    return render_template('/admin/translations.html',
                            languages=languages)


@admin_translation_bp.route('/admin/languages/table')
def display_languages_table():
    languages = Language.query.all()
    print("Llanguages", languages)
    return render_template('admin/translations_languages_htmx_table.html', 
                            languages=languages)


@admin_translation_bp.route('/admin/languages/language_update/<int:language_id>', methods=['POST'])
def update_language(language_id):
    try:
        language = Language.query.get(language_id)
        print("language", language)
        print("request.form", request.form)
        if language:
            code =  request.form.get('code', language.code)
            name = request.form.get('name', language.name)
            translation = request.form.get('translation', language.translation)
            is_active = True if request.form.get('is_active', language.is_active) == "true" else False
            voice_is_active = True if request.form.get('voice_is_active', language.voice_is_active) == "true" else False
            if code == '':
                app.display_toast(success=False, message="Le code est obligatoire")
                return "", 204
            if name == '':
                app.display_toast(success=False, message="Le nom est obligatoire")
                return "", 204
            if translation == '':
                app.display_toast(success=False, message="La traduction est obligatoire")
                return "", 204

            # Vérifie que le code ne sont pas déjà enregistrées par une autre langue
            existing_language = db.session.query(Language).filter(
                Language.code == code,
                Language.id != language_id  # Exclure la langue actuelle
            ).first()

            if existing_language:
                app.display_toast(success=False, message="Le code est déjà utilisé par une autre langue")
                return "", 204

            language.code = code
            language.name = name
            language.translation = translation
            language.is_active = is_active
            language.voice_is_active = voice_is_active

            # Gestion du téléchargement de l'image
            print("request.files", request.files)
            # Mise à jour de l'URL de l'image si elle a été changée
            image_url = request.form.get('image_url')
            print("image_url", image_url)
            if image_url:
                language.flag_url = image_url.split('/')[-1]  # Extraire le nom du fichier depuis l'URL


            db.session.commit()
            app.display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            app.display_toast(success=False, message="Langue introuvable")
            return ""

    except Exception as e:
        app.display_toast(success=False, message="Erreur : " + str(e))
        return jsonify(status="error", message=str(e)), 500


@admin_translation_bp.route('/admin/languages/confirm_delete/<int:language_id>', methods=['GET'])
def confirm_delete_language(language_id):
    language = Language.query.get(language_id)
    return render_template('/admin/translations_languages_modal_confirm_delete copy.html', language=language)


# supprime un membre de l'equipe
@admin_translation_bp.route('/admin/languages/delete/<int:language_id>', methods=['GET'])
def delete_language(language_id):
    try:
        language = Language.query.get(language_id)
        if not language:
            app.display_toast(success=False, message="Langue non trouvée")
            return display_languages_table()

        db.session.delete(language)
        db.session.commit()
        app.display_toast(success=True, message="Suppression réussie")

        communikation("admin", event="refresh_languages_order")

        return display_languages_table()

    except Exception as e:
        app.display_toast(success=False, message="Erreur : " + str(e))
        return display_languages_table()
    

# affiche le formulaire pour ajouter un membre
@admin_translation_bp.route('/admin/languages/add_form')
def add_language_form():
    return render_template('/admin/translations_language_add_form.html')

# enregistre le membre dans la Bdd
@admin_translation_bp.route('/admin/languages/add_new_language', methods=['POST'])
def add_new_language():
    try:
        code = request.form.get('code')
        name = request.form.get('name')
        translation = request.form.get('translation')
        is_active = True if request.form.get('is_active') == "true" else False
        voice_is_active = True if request.form.get('voice_is_active') == "true" else False
        image_url = request.form.get('image_url')
        if image_url:
            flag_url = image_url.split('/')[-1]  # Extraire le nom du fichier depuis l'URL
        else:
            flag_url = None

        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order = Language.query.order_by(Language.sort_order.desc()).first()
        sort_order = max_order.sort_order + 1 if max_order else 0

        if not code:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Code obligatoire")
            return display_languages_table()
        if code in [code[0] for code in db.session.query(Language.code).all()]:
            app.display_toast(success=False, message="Le code est déjà utilisées")
            return "", 204
        if not name:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Nom obligatoires")
            return display_languages_table()
        if not translation:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Traduction obligatoire")
            return display_languages_table()
        

        new_language = Language(
            code=code,
            translation=translation,
            name=name,
            is_active=is_active,
            voice_is_active=voice_is_active,
            flag_url=flag_url,
            sort_order=sort_order
        )
        db.session.add(new_language)
        db.session.commit()

        communikation("admin", event="refresh_languages_order")

        app.display_toast(success=True, message="Langue ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_language_form"></div>"""

        return f"{display_languages_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message= "Erreur : " + str(e))
        return display_languages_table()
    
@admin_translation_bp.route('/admin/languages/upload_flag_image', methods=['POST'])
def upload_flag_image():
    if 'file' not in request.files:
        return {"error": "No file part"}, 400
    
    file = request.files['file']
    if file.filename == '':
        return {"error": "No selected file"}, 400
    
    if file and app.allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['FLAG_FOLDER'], filename))
        return {"url": url_for('static', filename='images/pays/' + filename)}
    
    return {"error": "Invalid file type"}, 400  


@admin_translation_bp.route('/admin/languages/order_languages')
def order_languages_table():
    languages = Language.query.order_by(Language.sort_order).all()
    return render_template('admin/translations_languages_order.html', languages=languages)


@admin_translation_bp.route('/admin/languages/update_languages_order', methods=['POST'])
def update_languages_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, counter_id in enumerate(order_data):
            languages = Language.query.order_by(Language.sort_order).get(counter_id)
            languages.sort_order = index
        db.session.commit()
        app.display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        app.display_toast(success=False, message=f"Erreur: {e}")


def insert_translation_if_not_exists(table_name, column_name, key_name, row_id, language_code, text):
    """Insert a translation if it doesn't already exist in the Translation table."""
    existing_translation = Translation.query.filter_by(
        table_name=table_name,
        column_name=column_name,
        key_name=key_name,
        row_id=row_id,
        language_code=language_code
    ).first()

    if not existing_translation:
        translation = Translation(
            table_name=table_name,
            column_name=column_name,
            key_name=key_name,
            row_id=row_id,
            language_code=language_code,
            translated_text=text
        )
        db.session.add(translation)
        return True  # Indique qu'une nouvelle traduction a été insérée

    return False  # Aucune nouvelle traduction insérée

@admin_translation_bp.route('/admin/translations/collect', methods=['GET'])
def translations_collect():
    # Définir la langue par défaut (vous pouvez adapter selon vos besoins)
    default_language_code = 'fr'

    # Charger les clés de configuration à traduire depuis le fichier JSON
    config_keys_to_translate = load_config_keys_to_translate()
    print(config_keys_to_translate)

    # Compteur pour le nombre de nouvelles traductions
    new_translations_count = 0

    # Extraire les textes de ConfigOption seulement pour les clés spécifiées
    if config_keys_to_translate:
        config_texts = db.session.query(ConfigOption.id, ConfigOption.config_key, ConfigOption.value_str, ConfigOption.value_text).filter(ConfigOption.config_key.in_(config_keys_to_translate)).all()
        for row in config_texts:
            if row.value_str:
                if insert_translation_if_not_exists(
                    table_name='ConfigOption',
                    column_name='value_str',
                    key_name=row.config_key,
                    row_id=row.id,
                    language_code=default_language_code,
                    text=row.value_str
                ):
                    new_translations_count += 1

            if row.value_text:
                if insert_translation_if_not_exists(
                    table_name='ConfigOption',
                    column_name='value_text',
                    key_name=row.config_key,
                    row_id=row.id,
                    language_code=default_language_code,
                    text=row.value_text
                ):
                    new_translations_count += 1

    # Extraire les textes de Button
    button_texts = db.session.query(Button.id, Button.label).all()
    for row in button_texts:
        if insert_translation_if_not_exists(
            table_name='Button',
            column_name='label',
            key_name="",
            row_id=row.id,
            language_code=default_language_code,
            text=row.label
        ):
            new_translations_count += 1

    # Extraire les textes d'Activity
    activity_texts = db.session.query(Activity.id, Activity.inactivity_message, Activity.specific_message).all()
    for row in activity_texts:
        if row.inactivity_message:
            if insert_translation_if_not_exists(
                table_name='Activity',
                column_name='inactivity_message',
                row_id=row.id,
                key_name="",
                language_code=default_language_code,
                text=row.inactivity_message
            ):
                new_translations_count += 1

        if row.specific_message:
            if insert_translation_if_not_exists(
                table_name='Activity',
                column_name='specific_message',
                key_name="",
                row_id=row.id,
                language_code=default_language_code,
                text=row.specific_message
            ):
                new_translations_count += 1

    # Confirmer les changements dans la base de données
    db.session.commit()

    # Afficher le nombre de nouveaux textes mis à jour dans display_toast
    app.display_toast(success=True, message=f"{new_translations_count} nouveaux textes mis à jour")

    return "", 200

def load_config_keys_to_translate():
    json_file = 'static/json/config_keys_to_translate.json'
    with open(json_file, 'r', encoding='utf-8') as file:
        data = json.load(file)
        return data.get('config_keys_to_translate', [])
    

@admin_translation_bp.route('/admin/translations/change_language_target', methods=['POST'])
def change_language_target():
    language_code = request.form.get("language_code")
    
    # Récupérer les textes en français
    references = db.session.query(Translation).filter(Translation.language_code == "fr").all()
    # Récupérer les textes dans la langue cible
    translations = db.session.query(Translation).filter(Translation.language_code == language_code).all()

    # Dictionnaires pour stocker les traductions associées par clé unique
    button_translations = {}
    activity_translations = {}
    config_option_translations = {}

    # Créer des dictionnaires séparés avec les textes en français
    for ref in references:
        key = (ref.table_name, ref.column_name, ref.row_id, ref.key_name)
        if ref.table_name == 'Button':
            button_translations[key] = {'fr': ref.translated_text, 'target': None}
        elif ref.table_name == 'Activity':
            activity_translations[key] = {'fr': ref.translated_text, 'target': None}
        elif ref.table_name == 'ConfigOption':
            config_option_translations[key] = {'fr': ref.translated_text, 'target': None}

    # Ajouter les traductions de la langue cible
    for trans in translations:
        key = (trans.table_name, trans.column_name, trans.row_id, trans.key_name)
        if trans.table_name == 'Button':
            if key in button_translations:
                button_translations[key]['target'] = trans.translated_text
            else:
                button_translations[key] = {'fr': None, 'target': trans.translated_text}
        elif trans.table_name == 'Activity':
            if key in activity_translations:
                activity_translations[key]['target'] = trans.translated_text
            else:
                activity_translations[key] = {'fr': None, 'target': trans.translated_text}
        elif trans.table_name == 'ConfigOption':
            if key in config_option_translations:
                config_option_translations[key]['target'] = trans.translated_text
            else:
                config_option_translations[key] = {'fr': None, 'target': trans.translated_text}

    return render_template("admin/translations_texts_list.html",
                            language_code=language_code,
                            button_translations=button_translations,
                            activity_translations=activity_translations,
                            config_option_translations=config_option_translations)


@admin_translation_bp.route('/admin/translations/save_translations', methods=['POST'])
def save_translations():
    print(request.form.get("language_code"))
    print("items", request.form.items())
    language_code = request.form.get("language_code")
    updated_count = 0

    for key, value in request.form.items():
        print("key", key)
        if key.startswith("translation|"):
            _, table_name, column_name, row_id, key_name = key.split('|')
            row_id = int(row_id)
            
            # Rechercher la traduction existante ou en créer une nouvelle
            translation = Translation.query.filter_by(
                table_name=table_name,
                column_name=column_name,
                row_id=row_id,
                key_name=key_name,
                language_code=language_code
            ).first()

            if translation:
                # Mise à jour de la traduction existante
                translation.translated_text = value
            else:
                # Création d'une nouvelle traduction
                translation = Translation(
                    table_name=table_name,
                    column_name=column_name,
                    row_id=row_id,
                    key_name=key_name,
                    language_code=language_code,
                    translated_text=value
                )
                db.session.add(translation)

            updated_count += 1

    db.session.commit()

    app.display_toast(success=True, message=f"{updated_count} traduction(s) sauvegardée(s)")
    # Retourner une réponse simple indiquant le nombre de traductions mises à jour
    return "", 200