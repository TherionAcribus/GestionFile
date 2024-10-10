import os
from flask import Blueprint, render_template, request, url_for, redirect, send_from_directory, current_app as app
from cryptography.fernet import Fernet
from werkzeug.utils import secure_filename
from google.cloud import texttospeech
import gtts
from models import ConfigOption, Activity, Counter, Language, db
from python.engine import get_futur_patient, generate_audio_calling, get_google_credentials
from communication import communikation

def allowed_json_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'json'

admin_announce_bp = Blueprint('admin_announce', __name__)

@admin_announce_bp.route('/admin/announce')
@admin_announce_bp.route('/admin/announce/<tab>')
def announce_page(tab=None):
    valid_tabs = ['visual', 'audio', 'gallery', 'googleVoice']
    if tab not in valid_tabs:
        tab = 'visual'
    return render_template('/admin/announce.html', 
                            announce_sound = app.config['ANNOUNCE_SOUND'],
                            announce_alert = app.config['ANNOUNCE_ALERT'],
                            announce_player = app.config['ANNOUNCE_PLAYER'],
                            anounce_style = app.config['ANNOUNCE_STYLE'],
                            announce_call_text=app.config['ANNOUNCE_CALL_TEXT'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],
                            announce_call_text_transition=app.config['ANNOUNCE_CALL_TEXT_TRANSITION'],
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_ongoing_text=app.config['ANNOUNCE_ONGOING_TEXT'],
                            announce_title=app.config['ANNOUNCE_TITLE'],
                            announce_title_size=app.config["ANNOUNCE_TITLE_SIZE"],
                            announce_subtitle=app.config['ANNOUNCE_SUBTITLE'],
                            announce_text_up_patients=app.config['ANNOUNCE_TEXT_UP_PATIENTS'],
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_down_patients=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS'],
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            announce_infos_display=app.config['ANNOUNCE_INFOS_DISPLAY'],
                            announce_infos_display_time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION']   ,
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],
                            announce_infos_mix_folders=app.config['ANNOUNCE_INFOS_MIX_FOLDERS'],
                            announce_call_sound=app.config['ANNOUNCE_CALL_SOUND'],
                            announce_call_translation = app.config['ANNOUNCE_CALL_TRANSLATION'],
                            voice_google_key=get_google_credentials(),
                            languages = Language.query.all()
                            )


@admin_announce_bp.route('/admin/announce/gallery_audio')
def gallery_audio():    
    return render_template('/admin/announce_audio_gallery.html',
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],)


@admin_announce_bp.route("/admin/announce/audio/gallery_list", methods=["GET", "DELETE"])
def gallery_audio_list():
    # il faut garder la methode DELETE car appeler par delete/<sound_filename> pour réafficher la galerie
    # Lister tous les fichiers wav dans le répertoire SOUND_FOLDER
    sounds = [f for f in os.listdir("static/audio/signals") if f.endswith('.wav') or f.endswith('.mp3')]
    print("sounds", sounds)
    return render_template("admin/announce_audio_gallery_list.html",
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],
                            sounds=sounds)

@admin_announce_bp.route('/sounds/<filename>')
def serve_sound(filename):
    return send_from_directory("static/audio/signals", filename)

@admin_announce_bp.route("/admin/announce/audio/delete/<sound_filename>", methods=["DELETE"])
def delete_sound(sound_filename):
    sound_path = os.path.join("static/audio/signals", sound_filename)    
    try:
        # on empeche de supprimer le son en cours d'utilisation
        if sound_filename == app.config["ANNOUNCE_ALERT_FILENAME"]:
            app.display_toast(success=False, message="Impossible de supprimer le son courant. Selectionner un autre son et valider avant de supprimer celui-ci.")
            return "", 204
        # Vérifier si le fichier existe avant de le supprimer
        if os.path.exists(sound_path):
            os.remove(sound_path)
            app.logger.info(f"Son supprimé : {sound_filename}")
            return redirect (url_for('gallery_audio_list'))
        else:
            app.logger.error(f"Fichier non trouvé : {sound_filename}")
            app.display_toast(success=False, message="Fichier non trouvé")
            return "Fichier non trouvé", 404
    except Exception as e:
        app.logger.error(f"Erreur lors de la suppression du fichier : {str(e)}")
        return "Erreur lors de la suppression du fichier", 500

@admin_announce_bp.route('/admin/announce/audio/current_signal')
def current_signal():
    return render_template('/admin/announce_audio_current_signal.html',
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],)


@admin_announce_bp.route('/admin/announce/audio/save_selected_sound', methods=['POST'])
def select_signal():
    print(request.values)
    filename = request.form.get('selected_sound')
    if filename:
        app.config['ANNOUNCE_ALERT_FILENAME'] = filename
        config = ConfigOption.query.filter_by(config_key='announce_alert_filename').first()
        print(config)
        config.value_str = filename
        db.session.commit()

        communikation("admin", event="refresh_sound")

    return "", 204


def allowed_audio_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_AUDIO_EXTENSIONS"]


@admin_announce_bp.route('/admin/announce/audio/upload', methods=['POST'])
def upload_signal_file():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file and allowed_audio_file(file.filename):
        filename = file.filename
        file.save(os.path.join("static/audio/signals", filename))
    
    return redirect(url_for('gallery_audio_list'))


@admin_announce_bp.route('/admin/announce/audio/test/<string:scope>', methods=['GET'])
def announce_audio_test(scope):
    call_number = request.values.get('call_number', 'A-1')
    activity = Activity.query.get(1)

    patient = get_futur_patient(call_number, activity)

    # changement de langue si besoin
    language_code = request.values.get('language_code', 'fr')
    if language_code != "fr":
        language = Language.query.filter_by(code=language_code).first()
        patient.language = language
        patient.language_code = language_code

    # Recherche du premier comptoir occupé par un pharmacien
    counter = Counter.query.filter(Counter.staff_id.isnot(None)).first()

    if counter is None:
        counter = Counter.query.get(1)
    
    patient.counter = counter
    audio_url = generate_audio_calling("A", patient, language_code=language_code)

    if scope == "announce":        
        communikation("update_audio", event="audio", data=audio_url)
    else:
        communikation("admin", event="audio_test", data=audio_url)

    return "", 200


@admin_announce_bp.route('/admin/announce/google/add_key', methods=['POST'])
def upload_google_key():

    cipher_suite = Fernet(app.config["BASE32_KEY"])

    if 'google_key_file' not in request.files:
        return '<div class="alert alert-danger">Aucun fichier sélectionné.</div>'
    file = request.files['google_key_file']
    if file.filename == '':
        return '<div class="alert alert-danger">Le nom du fichier est vide.</div>'
    if file and allowed_json_file(file.filename):
        secure_filename(file.filename)
        file_content = file.read()
        # Chiffrer le contenu du fichier
        encrypted_content = cipher_suite.encrypt(file_content)
        # Convertir en chaîne de caractères pour le stockage
        encrypted_content_str = encrypted_content.decode('utf-8')
        # Enregistrer dans la base de données
        config_option = ConfigOption.query.filter_by(config_key='voice_google_key').first()
        if config_option:
            config_option.value_json = encrypted_content_str
        else:
            config_option = ConfigOption(config_key='voice_google_key', value_json=encrypted_content_str)
            db.session.add(config_option)
        db.session.commit()
        return '<div class="alert alert-success">Clé Google Cloud enregistrée avec succès.</div>'
    else:
        return '<div class="alert alert-danger">Format de fichier non autorisé. Veuillez télécharger un fichier JSON.</div>'



@admin_announce_bp.route('/admin/announce/google/filter_voices', methods=['POST'])
def filter_voices():
    # Récupérer les filtres depuis le formulaire
    selected_language = request.form.get('voice_google_language', '')
    selected_gender = request.form.get('voice_google_gender', '')
    selected_type = request.form.get('voice_google_type', '')

    credentials_json = get_google_credentials()

    google_voices = []
    if credentials_json:
        # Récupérer les voix filtrées
        google_voices = list_google_voices(credentials_json, language=selected_language, gender=selected_gender, voice_type=selected_type)

    # Renvoyer la liste filtrée dans le select
    return render_template('/admin/announce_google_voice_list.html', 
                            google_voices=google_voices,
                            voice_google_name=app.config['VOICE_GOOGLE_NAME'],
                            credentials_json=credentials_json)


def list_google_voices(credentials_json,language=None, gender=None, voice_type=None):
    """Récupère la liste des voix disponibles avec filtres et retourne un dictionnaire."""

    # Récupérer les credentials déchiffrés
    
    if not credentials_json:
        return "Erreur : Clé Google Cloud non configurée.", 500

    # Écrire les credentials dans un fichier temporaire
    temp_credentials_path = 'temp_google_credentials.json'
    with open(temp_credentials_path, 'wb') as temp_file:
        temp_file.write(credentials_json)

    # Configurer la variable d'environnement pour Google Cloud
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_credentials_path

    # Appel à l'API Google Text-to-Speech
    client = texttospeech.TextToSpeechClient()

    # Effectuer la requête pour lister les voix disponibles
    voices = client.list_voices()

    # Liste des voix sous forme de dictionnaire
    voice_list = []

    for voice in voices.voices:
        # Filtrer par langue si précisé
        if not any(lang_code.startswith(language) for lang_code in voice.language_codes):
            continue

        # Filtrer par genre si précisé
        ssml_gender = texttospeech.SsmlVoiceGender(voice.ssml_gender).name
        if gender and ssml_gender.lower() != gender.lower():
            continue

        # Filtrer par type de voix (ex. Wavenet, Standard) si précisé
        if voice_type and voice_type.lower() not in voice.name.lower():
            continue

        # Trouver le code de langue complet correspondant
        full_language_code = next((code for code in voice.language_codes if code.startswith(language)), voice.language_codes[0]) if language else voice.language_codes[0]

        # Créer un dictionnaire pour chaque voix filtrée
        voice_info = {
            "name": voice.name,
            "language_codes": voice.language_codes,
            "full_language_code": full_language_code,
            "ssml_gender": ssml_gender,
            "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
        }
        voice_list.append(voice_info)

    return voice_list


@admin_announce_bp.route('/admin/announce/save_google_voice', methods=['POST'])
def announce_save_google_voice():
    language_id = request.form.get('language_id')
    voice_data = request.form.get('voice_google_name', '').split('|')
    voice_google_name = voice_data[0] if len(voice_data) > 0 else ''
    voice_google_region = voice_data[1] if len(voice_data) > 1 else ''

    try:
        language = Language.query.get(language_id)

        language.voice_google_name = voice_google_name
        language.voice_google_region = voice_google_region  
        db.session.commit()

        if language.code == "fr":
            app.config["VOICE_GOOGLE_NAME"] = voice_google_name
            app.config["VOICE_GOOGLE_REGION"] = voice_google_region  # Ajoutez cette ligne

        app.display_toast(success=True, message="Voix sauvegardée")

        return "", 200

    except Exception as e:
        print(e)
        app.display_toast(success=False, message=f"Erreur : {e}")
        return f"Erreur : {e}", 400


@admin_announce_bp.route('/admin/announce/select_language_voice', methods=['POST'])
def announce_select_language_voice():
    # Récupérer la voix sélectionnée dans le formulaire
    language_code = request.form.get('language_code', 'fr')
    print("language_code", language_code)
    language = Language.query.filter_by(code=language_code).first()
    languages = Language.query.all()
    return render_template('/admin/announce_tabs_choice_voices.html',
                        gtts_languages = gtts.lang.tts_langs(),
                        announce_voice = app.config['VOICE_GTTS_NAME'],
                        voice_google_key=get_google_credentials(),
                        language=language,
                        languages=languages
                        )

@admin_announce_bp.route('/admin/announce/save_voice_model', methods=['POST'])
def announce_save_voice_model():
    # Récupérer la voix sélectionnée dans le formulaire
    language_id = request.form.get('language_id')
    voice_model = request.form.get('voice_model')
    print("voice_model", voice_model, language_id)

    try:
        language = Language.query.get(language_id)

        language.voice_model = voice_model
        db.session.commit()

        if language.code == "fr":
            app.config["VOICE_MODEL"] = voice_model

        app.display_toast(success=True, message="Modele de voix sauvegardé")

        return "", 200

    except Exception as e:
        print(e)
        app.display_toast(success=False, message=f"Erreur : {e}")
        return f"Erreur : {e}", 400


@admin_announce_bp.route('/admin/announce/save_gtts_voice', methods=['POST'])
def announce_save_gtts_voice():
    # Récupérer la voix sélectionnée dans le formulaire
    language_id = request.form.get('language_id')
    voice_gtts_name = request.form.get('gtts_voice_name')

    try:
        language = Language.query.get(language_id)

        language.voice_gtts_name = voice_gtts_name
        db.session.commit()

        if language.code == "fr":
            app.config["VOICE_GTTS_NAME"] = voice_gtts_name

        app.display_toast(success=True, message="Voix sauvegardée")

        return "", 200

    except Exception as e:
        print(e)
        app.display_toast(success=False, message=f"Erreur : {e}")
        return f"Erreur : {e}", 400
    
@admin_announce_bp.route('/admin/announce/save_voice_is_active', methods=['POST'])
def announce_save_voice_is_active():
    # Récupérer la voix sélectionnée dans le formulaire
    language_id = request.form.get('language_id')
    voice_is_active = request.form.get('voice_is_active')

    try:
        language = Language.query.get(language_id)

        language.voice_is_active = True if voice_is_active == 'true' else False 
        db.session.commit()

        app.display_toast(success=True, message="Option sauvée")

        return "", 200

    except Exception as e:
        print(e)
        app.display_toast(success=False, message=f"Erreur : {e}")
        return f"Erreur : {e}", 400
