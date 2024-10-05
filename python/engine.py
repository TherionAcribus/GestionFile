import os
import qrcode
from flask import url_for, request, session, current_app as app
from datetime import datetime, timezone, date
from cryptography.fernet import Fernet
from google.cloud import texttospeech
from utils import replace_balise_announces, replace_balise_phone, get_text_translation, get_activity_message_translation
from gtts import gTTS
from models import Patient, ConfigOption, Language, db



def add_patient(call_number, activity):
    """ CRéation d'un nouveau patient et ajout à la BDD"""
    language_code = session.get('language_code', 'fr')
    language = Language.query.filter_by(code=language_code).first()
    # Vérifier que la langue existe, sinon utiliser une langue par défaut
    if not language:
        language = Language.query.filter_by(code='fr').first()  

    # Création d'un nouvel objet Patient
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(timezone.utc),
        status='standing',
        language_id=language.id
    )    
    # Ajout à la base de données
    db.session.add(new_patient)
    db.session.commit()  # Enregistrement des changements dans la base de données

    return new_patient

def get_next_call_number(activity):
    """ Récupérer le numéro d'appel en fonction de la méthode choisie"""
    numbering_by_activity = app.config.get('NUMBERING_BY_ACTIVITY', False)
    if numbering_by_activity:
        call_number = get_next_category_number(activity)
    else:
        call_number = get_next_call_number_simple()
    print("call_number", call_number)
    return call_number

def get_next_call_number_simple():
    # Obtenir le dernier patient enregistré aujourd'hui
    # BUG Connu : Si on passe de simple à par activité puis retour à simple -> repart à 1
    last_patient_today = Patient.query.filter(db.func.date(Patient.timestamp) == date.today()).order_by(Patient.id.desc()).first()
    if last_patient_today:
        if str(last_patient_today.call_number).isdigit():
            print(last_patient_today.call_number, type(last_patient_today.call_number))
            return last_patient_today.call_number + 1
        return 1
    return 1  # Réinitialiser le compteur si aucun patient n'a été enregistré aujourd'hui




# Générer le numéro d'appel en fonction de l'activité
def get_next_category_number(activity):
    # on utilise le code prévu de l'activité. Plusieurs activités peuvent avoir la même lettre
    letter_prefix = activity.letter
    today = date.today()

    # Compter combien de patients sont déjà enregistrés aujourd'hui avec le même préfixe de lettre
    today_count = Patient.query.filter(
        db.func.date(Patient.timestamp) == today,
        db.func.substr(Patient.call_number, 1, 1) == letter_prefix
    ).count()

    # Le prochain numéro sera le nombre actuel + 1
    next_number = today_count + 1

    return f"{letter_prefix}-{next_number}"

def get_futur_patient(call_number, activity):
    """ CRéation d'un nouveau patient SANS ajout à la BDD
    Permet de simuler sa création pour pouvoir générer les infos utiles dans le QR Code"""

    language_code = session.get('language_code', 'fr')
    language = Language.query.filter_by(code=language_code).first()
    # Vérifier que la langue existe, sinon utiliser une langue par défaut
    if not language:
        language = Language.query.filter_by(code='fr').first()  

    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(timezone.utc),
        status='standing',
        language_id=language.id
    ) 
    return new_patient


def register_patient(activity):
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    
    print("before autocalling")
    app.auto_calling()

    app.communikation("update_patient")
    return new_patient


def generate_audio_calling(counter_number, next_patient, language_code="fr"):

    # Si on ne veux pas de son, on quitte
    if not app.config["ANNOUNCE_SOUND"]:
        return
    
    # Texte pour la synthèse vocale
    # patient FR ou que langue FR
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        text_template = app.config["ANNOUNCE_CALL_SOUND"]
    # si pas FR
    else:
        # si langue desactivée
        if not next_patient.language.voice_is_active:
            text_template = app.config["ANNOUNCE_CALL_SOUND"]
        # si langue activée
        else :
            translated_template = get_text_translation("announce_call_sound", next_patient.language.code)
            if translated_template["error"]:
                language_code == "fr"
            text_template = translated_template["translation"]

    text = replace_balise_announces(text_template, next_patient)

    return choose_voice_model(next_patient, text, language_code)
    
def choose_voice_model(next_patient, text, language_code):
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_model = app.config["VOICE_MODEL"]
    else:
        if not next_patient.language.voice_is_active:
            voice_model = app.config["VOICE_MODEL"]
        else:
            voice_model = next_patient.language.voice_model

    if voice_model == "gtts":
        return create_tts_sound(next_patient, text, language_code)
    elif voice_model == "google":
        return create_google_tts_sound(next_patient, text, language_code)

def create_tts_sound(next_patient, text, language_code):
    print("create_tts_sound", text, app.config["VOICE_GTTS_NAME"])

    if language_code == "fr"or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_gtts_name = app.config["VOICE_GTTS_NAME"]
    else:
        if not next_patient.language.voice_is_active:
            voice_gtts_name = app.config["VOICE_GTTS_NAME"]
        else:
            voice_gtts_name = next_patient.language.voice_gtts_name

    lang = voice_gtts_name

    tts = gTTS(text, lang=lang)  

    # Chemin de sauvegarde du fichier audio
    audiofile = f'patient_{next_patient.call_number}.mp3'
    audio_path = os.path.join(app.static_folder, 'audio/annonces', audiofile)  # Enregistrement dans le dossier 'static/audio'

    # Assurer que le répertoire existe
    if not os.path.exists(os.path.dirname(audio_path)):
        os.makedirs(os.path.dirname(audio_path))

    # Sauvegarde du fichier audio
    tts.save(audio_path)

    # Envoi du chemin relatif via SSE
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)

    print("AUDIO", audio_url)

    return audio_url


def create_google_tts_sound(next_patient, text, language_code):
    
    # Récupérer la voix sélectionnée depuis la base de données (ou config)
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_google_name = app.config["VOICE_GOOGLE_NAME"]
        voice_google_region = app.config["VOICE_GOOGLE_REGION"]
    else:
        if not next_patient.language.voice_is_active:
            voice_google_name = app.config["VOICE_GOOGLE_NAME"]
            voice_google_region = app.config["VOICE_GOOGLE_REGION"]
        else:
            voice_google_name = next_patient.language.voice_google_name
            voice_google_region = next_patient.language.voice_google_region

    # Récupérer les credentials déchiffrés
    try:
        credentials_json = get_google_credentials()
    except Exception as e:
        credentials_json = None
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

    synthesis_input = texttospeech.SynthesisInput(text=text)

    # Utiliser la voix sélectionnée
    voice = texttospeech.VoiceSelectionParams(
        name=voice_google_name,  # Voix sauvegardée dans la base de données
        language_code=voice_google_region  # Adapter selon la voix sélectionnée (ex: "fr-FR")
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    # Effectuer la requête à l'API
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )

    # Chemin de sauvegarde du fichier audio
    audiofile = f'patient_{next_patient.call_number}.mp3'
    audio_path = os.path.join(app.static_folder, 'audio/annonces', audiofile)  # Enregistrement dans le dossier 'static/audio'

    # Assurer que le répertoire existe
    if not os.path.exists(os.path.dirname(audio_path)):
        os.makedirs(os.path.dirname(audio_path))

    # Sauvegarder l'audio généré dans un fichier MP3
    with open(audio_path, 'wb') as out:
        out.write(response.audio_content)

    # Supprimer le fichier temporaire après utilisation
    os.remove(temp_credentials_path)

    # Envoi du chemin relatif via SSE
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)

    return audio_url


def create_qr_code(patient):
    print("create_qr_code")
    print(patient, patient.id, patient.call_number, patient.activity)

    language_code = session.get('language_code', "fr")

    if app.config['PAGE_PATIENT_QRCODE_WEB_PAGE']:
        if "SERVER_URL" not in app.config:
            set_server_url(app, request)
        data = f"{app.config['SERVER_URL']}/patient/phone/{language_code}/{patient.call_number}/{patient.activity.id}"
    else :
        if session.get('language_code') != "fr":
            language_code = session.get('language_code')
            template = get_text_translation("page_patient_qrcode_data", language_code)["translation"]
            if app.config["PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE"]:
                template = template + "\n" + get_activity_message_translation(patient.activity, language_code)
        else:
            template = app.config['PAGE_PATIENT_QRCODE_DATA']
            if app.config["PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE"]:
                template = template + "\n" + patient.activity.specific_message
        data = replace_balise_phone(template, patient)

    # Générer le QR Code
    img = qrcode.make(data)
    
    # Utiliser app.static_folder pour obtenir le chemin absolu vers le dossier static
    directory = os.path.join(app.static_folder, 'qr_patients')
    filename = f'qr_patient-{patient.call_number}.png'
    img_path = os.path.join(directory, filename)

    # Assurer que le répertoire existe
    if not os.path.exists(directory):
        os.makedirs(directory)  # Créer le dossier s'il n'existe pas

    # Enregistrement de l'image dans le dossier static
    img.save(img_path)

    return filename


def set_server_url(app, request):
    # Stockage de l'adresse pour la génération du QR code
    if request.host_url == "http://127.0.0.1:5000/":
        server_url = app.config.get('NETWORK_ADRESS')
    else:
        server_url = request.host_url
    app.config['SERVER_URL'] = server_url


def get_google_credentials():
    cipher_suite = Fernet(app.config["BASE32_KEY"])
    config_option = ConfigOption.query.filter_by(config_key='voice_google_key').first()
    if config_option and config_option.value_json:
        encrypted_content = config_option.value_json.encode('utf-8')
        # Déchiffrer le contenu de la clé JSON
        decrypted_content = cipher_suite.decrypt(encrypted_content)
        return decrypted_content  # Bytes du fichier JSON
    return None