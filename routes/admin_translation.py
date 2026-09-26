import json
import os
from flask import Blueprint, render_template, request, jsonify, url_for, current_app as app
from models import ConfigOption, Button, Activity, Language, Translation, db
from communication import communikation
from routes.admin_security import require_permission, require_permission_api
from pagination import parse_page_params, paginate_query
from ui_feedback import display_toast
from utils import validate_config_text
from image_storage import accept_image_upload
from path_security import safe_path_under
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from params_registry import get_spec

admin_translation_bp = Blueprint('admin_translation', __name__)

REFERENCE_LANGUAGE_CODE = 'fr'

# Colonnes de tri autorisées (liste blanche) — cf. pagination.parse_page_params.
LANGUAGE_SORT_COLUMNS = {
    'code': Language.code,
    'name': Language.name,
    'translation': Language.translation,
}


@admin_translation_bp.route('/admin/translations')
@require_permission('translation')
def admin_translation():
    valid_tabs = ['languages', 'texts']
    tab = request.args.get('tab', 'languages')
    if tab not in valid_tabs:
        tab = 'languages'

    languages = Language.query.all()
    return render_template('/admin/translations.html',
                            languages=languages)


@admin_translation_bp.route('/admin/languages/table')
@require_permission('translation')
def display_languages_table():
    params = parse_page_params(
        request.values,
        allowed_sort=tuple(LANGUAGE_SORT_COLUMNS),
        default_sort='code',
    )
    pager = paginate_query(
        Language.query,
        params,
        sort_columns=LANGUAGE_SORT_COLUMNS,
        search_columns=[Language.code, Language.name, Language.translation],
    )
    return render_template('admin/translations_languages_htmx_table.html',
                            languages=pager.items, pager=pager, params=params)


@admin_translation_bp.route('/admin/languages/language_update/<int:language_id>', methods=['POST'])
@require_permission('translation')
def update_language(language_id):
    try:
        language = Language.query.get(language_id)
        app.logger.debug('language %s', language)
        app.logger.debug('request.form %s', request.form)
        if language:
            code =  request.form.get('code', language.code)
            name = request.form.get('name', language.name)
            translation = request.form.get('translation', language.translation)
            is_active = True if request.form.get('is_active', language.is_active) == "true" else False
            voice_is_active = True if request.form.get('voice_is_active', language.voice_is_active) == "true" else False
            if code == '':
                display_toast(success=False, message="Le code est obligatoire")
                return "", 204
            if name == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return "", 204
            if translation == '':
                display_toast(success=False, message="La traduction est obligatoire")
                return "", 204

            # Vérifie que le code ne sont pas déjà enregistrées par une autre langue
            existing_language = db.session.query(Language).filter(
                Language.code == code,
                Language.id != language_id  # Exclure la langue actuelle
            ).first()

            if existing_language:
                display_toast(success=False, message="Le code est déjà utilisé par une autre langue")
                return "", 204

            language.code = code
            language.name = name
            language.translation = translation
            language.is_active = is_active
            language.voice_is_active = voice_is_active

            # Gestion du téléchargement de l'image
            app.logger.debug('request.files %s', request.files)
            # Mise à jour de l'URL de l'image si elle a été changée
            image_url = request.form.get('image_url')
            app.logger.debug('image_url %s', image_url)
            if image_url:
                extracted = image_url.split('/')[-1]
                if extracted and extracted != 'None':
                    language.flag_url = extracted

            db.session.commit()
            record_audit(ACTION_UPDATE, "language", target_id=language_id,
                         outcome=OUTCOME_SUCCESS, details=f"code={code}")
            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Langue introuvable")
            return ""

    except Exception as e:
        # Rollback avant l'audit : le commit interne de record_audit ne doit
        # pas persister de mutations métier restées en attente.
        db.session.rollback()
        record_audit(ACTION_UPDATE, "language", target_id=language_id,
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La mise à jour a échoué.")
        return jsonify(status="error", message="La mise à jour a échoué."), 500


@admin_translation_bp.route('/admin/languages/confirm_delete/<int:language_id>', methods=['GET'])
@require_permission('translation')
def confirm_delete_language(language_id):
    language = Language.query.get(language_id)
    return render_template('/admin/translations_languages_modal_confirm_delete.html', language=language)


# supprime un membre de l'equipe
@admin_translation_bp.route('/admin/languages/delete/<int:language_id>', methods=['DELETE'])
@require_permission('translation')
def delete_language(language_id):
    try:
        language = Language.query.get(language_id)
        if not language:
            display_toast(success=False, message="Langue non trouvée")
            return display_languages_table()

        db.session.delete(language)
        db.session.commit()
        record_audit(ACTION_DELETE, "language", target_id=language_id,
                     outcome=OUTCOME_SUCCESS)
        display_toast(success=True, message="Suppression réussie")

        communikation("admin", event="refresh_languages_order")

        return display_languages_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "language", target_id=language_id,
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        app.logger.exception("Echec de la suppression d'une langue")
        return display_languages_table()
    

# affiche le formulaire pour ajouter un membre
@admin_translation_bp.route('/admin/languages/add_form')
@require_permission('translation')
def add_language_form():
    return render_template('/admin/translations_language_add_form.html')

# enregistre le membre dans la Bdd
@admin_translation_bp.route('/admin/languages/add_new_language', methods=['POST'])
@require_permission('translation')
def add_new_language():
    try:
        code = request.form.get('code')
        name = request.form.get('name')
        translation = request.form.get('translation')
        is_active = True if request.form.get('is_active') == "true" else False
        voice_is_active = True if request.form.get('voice_is_active') == "true" else False
        image_url = request.form.get('image_url')
        if image_url:
            extracted = image_url.split('/')[-1]
            flag_url = extracted if extracted and extracted != 'None' else None
        else:
            flag_url = None

        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order = Language.query.order_by(Language.sort_order.desc()).first()
        sort_order = max_order.sort_order + 1 if max_order else 0

        if not code:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Code obligatoire")
            return display_languages_table()
        if code in [code[0] for code in db.session.query(Language.code).all()]:
            display_toast(success=False, message="Le code est déjà utilisées")
            return "", 204
        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoires")
            return display_languages_table()
        if not translation:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Traduction obligatoire")
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

        record_audit(ACTION_CREATE, "language", target_id=new_language.id,
                     outcome=OUTCOME_SUCCESS, details=f"code={code}")
        communikation("admin", event="refresh_languages_order")

        display_toast(success=True, message="Langue ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_language_form"></div>"""

        return f"{display_languages_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "language", target_id=request.form.get('code'),
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="L'ajout a échoué.")
        app.logger.exception("Echec de l'ajout d'une langue")
        return display_languages_table()
    
@admin_translation_bp.route('/admin/languages/upload_flag_image', methods=['POST'])
@require_permission_api('translation')
def upload_flag_image():
    if 'file' not in request.files:
        return {"error": "No file part"}, 400

    file = request.files['file']
    if file.filename == '':
        return {"error": "No selected file"}, 400

    ok, err, result = accept_image_upload(file)
    if not ok:
        return {"error": err or "Fichier invalide"}, 400

    flag_folder = os.path.join(app.static_folder, 'images', 'flags')
    os.makedirs(flag_folder, exist_ok=True)
    target_path = safe_path_under(flag_folder, result["filename"])
    target_path.write_bytes(result["data"])
    record_audit(ACTION_CREATE, "language_flag",
                 target_id=result["filename"], outcome=OUTCOME_SUCCESS)
    return {"url": url_for('static', filename='images/flags/' + result["filename"])}


@admin_translation_bp.route('/admin/languages/order_languages')
@require_permission('translation')
def order_languages_table():
    languages = Language.query.order_by(Language.sort_order).all()
    return render_template('admin/translations_languages_order.html', languages=languages)


@admin_translation_bp.route('/admin/languages/update_languages_order', methods=['POST'])
@require_permission('translation')
def update_languages_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, counter_id in enumerate(order_data):
            languages = Language.query.order_by(Language.sort_order).get(counter_id)
            languages.sort_order = index
        db.session.commit()
        record_audit(ACTION_UPDATE, "language", outcome=OUTCOME_SUCCESS,
                     details="réordonnancement")
        display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "language", outcome=OUTCOME_FAILURE,
                     details="réordonnancement")
        display_toast(success=False, message="La mise à jour a échoué.")
        app.logger.exception("Echec du reordonnancement des langues")


def sync_reference_translation(
    table_name, column_name, key_name, row_id, language_code, text
):
    """Synchronise le texte de référence et indique l'action effectuée."""
    translation = Translation.query.filter_by(
        table_name=table_name,
        column_name=column_name,
        key_name=key_name,
        row_id=row_id,
        language_code=language_code
    ).first()

    if not translation:
        db.session.add(Translation(
            table_name=table_name,
            column_name=column_name,
            key_name=key_name,
            row_id=row_id,
            language_code=language_code,
            translated_text=text
        ))
        return "created"

    if translation.translated_text != text:
        translation.translated_text = text
        return "updated"

    return "unchanged"


def collect_translation_sources(config_keys_to_translate):
    """Énumère les textes sources actuellement traduisibles."""
    if config_keys_to_translate:
        config_texts = db.session.query(
            ConfigOption.id,
            ConfigOption.config_key,
            ConfigOption.value_str,
            ConfigOption.value_text,
        ).filter(ConfigOption.config_key.in_(config_keys_to_translate)).all()
        for row in config_texts:
            spec = get_spec(row.config_key)
            if spec is None or spec.value_type not in ("value_str", "value_text"):
                app.logger.warning(
                    "Clé à traduire ignorée ou non textuelle : %s",
                    row.config_key,
                )
                continue
            text = getattr(row, spec.value_type) or ""
            yield ('ConfigOption', spec.value_type, row.config_key, row.id, text)

    button_texts = db.session.query(Button.id, Button.label).all()
    for row in button_texts:
        yield ('Button', 'label', '', row.id, row.label or "")

    activity_texts = db.session.query(
        Activity.id,
        Activity.inactivity_message,
        Activity.specific_message,
    ).all()
    for row in activity_texts:
        yield (
            'Activity', 'inactivity_message', '', row.id,
            row.inactivity_message or "")
        yield (
            'Activity', 'specific_message', '', row.id,
            row.specific_message or "")


@admin_translation_bp.route('/admin/translations/collect', methods=['POST'])
@require_permission('translation')
def translations_collect():
    try:
        config_keys_to_translate = load_config_keys_to_translate()
        app.logger.debug("%s", config_keys_to_translate)
        counters = {"created": 0, "updated": 0, "unchanged": 0}

        for table_name, column_name, key_name, row_id, text in collect_translation_sources(
            config_keys_to_translate
        ):
            result = sync_reference_translation(
                table_name,
                column_name,
                key_name,
                row_id,
                REFERENCE_LANGUAGE_CODE,
                text,
            )
            counters[result] += 1

        db.session.commit()
        record_audit(
            ACTION_UPDATE,
            "translation",
            outcome=OUTCOME_SUCCESS,
            details=(
                f"collecte : {counters['created']} nouveau(x), "
                f"{counters['updated']} référence(s) actualisée(s)"
            ),
        )
        display_toast(
            success=True,
            message=(
                f"{counters['created']} nouveau(x) texte(s), "
                f"{counters['updated']} référence(s) française(s) actualisée(s)"
            ),
        )
        language_code = request.form.get("language_code")
        if language_code and language_code != REFERENCE_LANGUAGE_CODE:
            language_exists = Language.query.filter_by(code=language_code).first()
            if language_exists:
                try:
                    content = _render_translations_list(language_code)
                except Exception:
                    app.logger.exception(
                        "Echec du rafraichissement de la liste des traductions")
                else:
                    return (
                        f'<div id="translations_list" hx-swap-oob="innerHTML">'
                        f'{content}</div>'
                    ), 200
        return "", 200

    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "translation", outcome=OUTCOME_FAILURE,
                     details="collecte des textes")
        display_toast(success=False, message="La synchronisation des textes a échoué.")
        app.logger.exception("Echec de la collecte des textes à traduire")
        return "", 200

def load_config_keys_to_translate():
    json_file = os.path.join(
        app.static_folder, 'json', 'config_keys_to_translate.json')
    with open(json_file, 'r', encoding='utf-8') as file:
        data = json.load(file)
        return data.get('config_keys_to_translate', [])
    

def _render_translations_list(language_code):
    references = db.session.query(Translation).filter(
        Translation.language_code == REFERENCE_LANGUAGE_CODE
    ).all()
    translations = db.session.query(Translation).filter(
        Translation.language_code == language_code).all()

    button_translations = {}
    activity_translations = {}
    config_option_translations = {}

    for ref in references:
        key = (ref.table_name, ref.column_name, ref.row_id, ref.key_name)
        if ref.table_name == 'Button':
            button_translations[key] = {'fr': ref.translated_text, 'target': None}
        elif ref.table_name == 'Activity':
            activity_translations[key] = {'fr': ref.translated_text, 'target': None}
        elif ref.table_name == 'ConfigOption':
            config_option_translations[key] = {'fr': ref.translated_text, 'target': None}

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


@admin_translation_bp.route('/admin/translations/change_language_target', methods=['POST'])
@require_permission('translation')
def change_language_target():
    language_code = request.form.get("language_code")

    if language_code == REFERENCE_LANGUAGE_CODE:
        display_toast(success=False, message="Le français est la langue de référence.")
        return "", 200

    return _render_translations_list(language_code)


@admin_translation_bp.route('/admin/translations/save_translations', methods=['POST'])
@require_permission('translation')
def save_translations():
    app.logger.debug("%s", request.form.get("language_code"))
    app.logger.debug('items %s', request.form.items())
    language_code = request.form.get("language_code")
    updated_count = 0

    for key, value in request.form.items():
        app.logger.debug('key %s', key)
        if key.startswith("translation|"):
            _, table_name, column_name, row_id, key_name = key.split('|')
            row_id = int(row_id)

            # Même validation que le champ d'administration source : un texte
            # de ConfigOption traduit avec une balise inconnue ou un balisage
            # non fermé s'afficherait / s'imprimerait littéralement. Échec =>
            # tout est annulé (le commit est unique, en fin de route).
            if table_name == "ConfigOption":
                text_check = validate_config_text(key_name, value)
                if not text_check["success"]:
                    db.session.rollback()
                    record_audit(ACTION_UPDATE, "translation",
                                 outcome=OUTCOME_FAILURE,
                                 details=f"langue={language_code}, clé={key_name}")
                    display_toast(success=False,
                                  message=f"{key_name} : {text_check['value']}")
                    return "", 200
                value = text_check["value"]

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
    record_audit(ACTION_UPDATE, "translation", outcome=OUTCOME_SUCCESS,
                 details=f"langue={language_code}, {updated_count} mise(s) à jour")

    display_toast(success=True, message=f"{updated_count} traduction(s) sauvegardée(s)")
    # Retourner une réponse simple indiquant le nombre de traductions mises à jour
    return "", 200