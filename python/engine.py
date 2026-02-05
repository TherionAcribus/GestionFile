import os
import qrcode
from flask import Blueprint, url_for, request, session, current_app as app, jsonify
from datetime import datetime, date
from sqlalchemy import and_
from cryptography.fernet import Fernet
from google.cloud import texttospeech
from utils import replace_balise_announces, replace_balise_phone, get_text_translation, get_activity_message_translation
from gtts import gTTS
from models import Patient, Counter, AlgoRule, ConfigOption, Language, db
from communication import communikation, notify_patient_phone
from config import time_tz
from auth_utils import require_app_token_or_login

engine_bp = Blueprint('engine', __name__)


@engine_bp.route('/call_next/<int:counter_id>', methods=['GET', 'POST'])
@require_app_token_or_login
def call_next_http(counter_id):
    if request.method == "GET":
        app.logger.warning("Deprecated GET /call_next (use POST).")

    ok, result = call_next(counter_id)
    if ok:
        return jsonify(result.to_dict()), 200

    if result in {"no_patient", "no_patient_for_counter"}:
        return "", 204

    return jsonify({"error": result}), 409


def call_next(counter_id, attempts=0):
    # pour éviter de prendre deux fois le même patient, en vérifie en l'appelant qu'il est toujours en attente sinon on rappelle un patient.
    # pour éviter des boucles infinies, on considère qu'après x (5) essais on abandonne. Peut probable que 5 comptoirs appellent en même temps des patients.
    max_attempts = 5

    # Nettoyage des patients précédents en statut 'calling' pour ce comptoir
    if attempts == 0:
        previous_patients = Patient.query.filter_by(status='calling', counter_id=counter_id).all()
        for patient in previous_patients:
            patient.status = 'done'
            app.logger.info(f"Patient {patient.id} status updated to 'done' for counter {counter_id} (fallback)")
        db.session.commit()

    if attempts >= max_attempts:
        app.logger.warning(f"Max attempts reached for counter {counter_id}")
        return False, "max_loop"

    if Patient.query.count() == 0:
        app.logger.info("No patients available")
        return False, "no_patient"
    
    next_patient = algo_choice_next_patient(counter_id)

    if next_patient is None:
        app.logger.info(f"No next patient found for counter {counter_id}")
        return False, "no_patient_for_counter"

    if next_patient.status != "standing":
        app.logger.info(f"Patient {next_patient.id} not standing, retrying. Attempt {attempts + 1}")
        return call_next(counter_id, attempts=attempts+1)

    language_code = next_patient.language.code
    print("language_code_pour_audio", language_code)
    try:
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()
        app.logger.info(f"Patient {next_patient.id} status updated to 'calling' for counter {counter_id}")

        language_code = next_patient.language.code
        print("language_code_pour_audio", language_code)
        audio_url = generate_audio_calling(counter_id, next_patient, language_code=language_code)
        communikation("update_audio", event="audio", data=audio_url)

        notify_patient_phone(next_patient.call_number)

        return True, next_patient

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating patient status: {str(e)}")
        return call_next(counter_id, attempts=attempts+1)


def algo_choice_next_patient(counter_id):

    counter = Counter.query.get(counter_id)

    # activités possible par ce pharmacien
    staff_activities = set(activity.id for activity in counter.staff.activities)

    # choix parmi les patients qui attendent 
    next_possible_patient = Patient.query.filter_by(status='standing')

    # choix parmi les patient qui correspondent aux activités du pharmacien
    next_possible_patient = next_possible_patient.filter(
        Patient.activity_id.in_(staff_activities)
    )

    print("next_possible_patient", next_possible_patient)
    if next_possible_patient.count() == 0:
        return None

    # permet de voir si un patient s'est fait doubler plus que le nombre prévu
    # Si oui on bloque l'algo le temps de rétablir l'équilibre
    is_patient_waiting_too_long = Patient.query.filter(
        and_(Patient.status == 'standing',
                Patient.overtaken >= app.config["ALGO_OVERTAKEN_LIMIT"])).first()
    print("is_patient_waiting_too_long", is_patient_waiting_too_long)

    applicable_rules = None
    if app.config['ALGO_IS_ACTIVATED'] and not is_patient_waiting_too_long:
    # priorité à un type d'activité si un patient répond aux critère
    # Récupération des règles applicables
        current_day = datetime.now().weekday()
        current_time = datetime.now().time()
        print('current_time', current_time)
        print('current_day', current_day)
        number_of_patients = Patient.query.filter_by(status='standing').count()
        print('number_of_patients', number_of_patients)

        # cherche des regles applicables
        applicable_rules = AlgoRule.query.filter(
            AlgoRule.start_time <= current_time,  # L'heure actuelle doit être après l'heure de début
            AlgoRule.end_time >= current_time,    # et avant l'heure de fin
            AlgoRule.min_patients <= number_of_patients,  # Le nombre de patients doit être dans l'intervalle
            AlgoRule.max_patients >= number_of_patients,
            #AlgoRule.days_of_week.contains(current_day)  # Le jour actuel doit être inclus dans les jours valides
        )
        print("applicable_rules", applicable_rules)

        # S'il y a des regles applicables, regarde niveau par niveau les activités correspondantes
        if applicable_rules:
            for level in range(1, 6):
                rules_at_level = applicable_rules.filter(AlgoRule.priority_level == level)
                if rules_at_level:
                    print("level", level)
                    activity_ids_from_rules  = [rule.activity_id for rule in rules_at_level]
                    next_possible_patient_via_rules = next_possible_patient.filter(
                        Patient.activity_id.in_(activity_ids_from_rules)
                    )

                    print("next_possible_patient", next_possible_patient_via_rules.all())
                    # pour les patients qui rentrent dans les priorité on va regarder si on ne dépasse pas le nombre de patients à dépasser de la régle
                    if next_possible_patient_via_rules.all():
                        for patient in next_possible_patient_via_rules.all():
                            print("patient", patient)
                            patients_ahead_count = next_possible_patient.filter(
                                                                                        Patient.timestamp < patient.timestamp
                                                                                    ).count()
                            max_overtaken = min(rule.max_overtaken for rule in patient.activity.priority_rules)
                            print("max_overtaken", max_overtaken, patients_ahead_count)
                            if patients_ahead_count < max_overtaken:
                                next_possible_patient = next_possible_patient_via_rules
                                break

    print('next_possible_patient', next_possible_patient)
    
    # tri par date 
    next_patient = next_possible_patient.order_by(Patient.timestamp).first()

    if applicable_rules:
        patient_overtaken(next_patient)

    print('next_patient', next_patient)

    return next_patient

def get_global_patient_queue():
    """
    Simulates the patient selection algorithm to determine the order of all waiting patients.
    Returns an ordered list of patients.
    """
    # 1. Fetch all standing patients
    all_patients = Patient.query.filter_by(status='standing').all()
    
    # Working copy of the list to manipulate
    waiting_patients = list(all_patients)
    ordered_queue = []
    
    # Get current context for rules
    if app.config['ALGO_IS_ACTIVATED']:
        current_time = datetime.now().time()
        number_of_patients = len(waiting_patients)
        
        # Find applicable rules based on current time and total number of patients
        # Note: We don't filter by days_of_week here as it's commented out in original algo, 
        # and we check rules against the global state
        applicable_rules = AlgoRule.query.filter(
            AlgoRule.start_time <= current_time,
            AlgoRule.end_time >= current_time,
            AlgoRule.min_patients <= number_of_patients,
            AlgoRule.max_patients >= number_of_patients
        ).all()
    else:
        applicable_rules = []

    # Loop until all patients are ordered
    while waiting_patients:
        selected_patient = None
        
        # If algorithm is active and we have rules, try to find a priority patient
        if app.config['ALGO_IS_ACTIVATED'] and applicable_rules:
            
            # Check if any patient has waited too long (overtaken limit)
            # In the simulation, we use the current 'overtaken' value from DB.
            # Ideally, the simulation should track 'overtaken' dynamically as we build the queue,
            # but for a display estimation, using the snapshot is acceptable and safer/simpler.
            is_patient_waiting_too_long = any(
                p.overtaken >= app.config["ALGO_OVERTAKEN_LIMIT"] for p in waiting_patients
            )
            
            if not is_patient_waiting_too_long:
                # Iterate through priority levels
                for level in range(1, 6):
                    rules_at_level = [r for r in applicable_rules if r.priority_level == level]
                    if not rules_at_level:
                        continue
                        
                    activity_ids_from_rules = [rule.activity_id for rule in rules_at_level]
                    
                    # Candidates matching this priority level
                    priority_candidates = [
                        p for p in waiting_patients 
                        if p.activity_id in activity_ids_from_rules
                    ]
                    
                    # Sort by timestamp to find the "oldest" candidate for this priority
                    priority_candidates.sort(key=lambda p: p.timestamp)
                    
                    for candidate in priority_candidates:
                        # Count how many people would be overtaken
                        # (People in waiting_patients with older timestamp than candidate)
                        patients_ahead_count = sum(
                            1 for p in waiting_patients 
                            if p.timestamp < candidate.timestamp
                        )
                        
                        # Get max_overtaken from the rules applicable to this patient's activity
                        # We take the minimum of max_overtaken from all matching rules for this activity
                        # (mimicking the logic: max_overtaken = min(rule.max_overtaken for rule in patient.activity.priority_rules))
                        # We use the rules_at_level we already fetched which match the activity
                        relevant_rules = [r for r in rules_at_level if r.activity_id == candidate.activity_id]
                        if not relevant_rules:
                            continue # Should not happen given logic above
                            
                        max_overtaken_limit = min(r.max_overtaken for r in relevant_rules)
                        
                        if patients_ahead_count < max_overtaken_limit:
                            selected_patient = candidate
                            break
                    
                    if selected_patient:
                        break
        
        # Fallback: if no patient selected by rules (or algo disabled), pick the one with oldest timestamp
        if not selected_patient:
            # Sort remaining by timestamp
            waiting_patients.sort(key=lambda p: p.timestamp)
            selected_patient = waiting_patients[0]
            
        # Add to ordered list and remove from working set
        ordered_queue.append(selected_patient)
        waiting_patients.remove(selected_patient)
        
    return ordered_queue

def patient_overtaken(next_patient):
    """ Met a jour le nombre de fois que le patient a été doublé"""
    patients_overtaken = Patient.query.filter(
        and_(
            Patient.status == 'standing',
            Patient.timestamp < next_patient.timestamp 
        )
    ).all() 

    for patient in patients_overtaken:
        patient.overtaken = patient.overtaken + 1
        print("patient overtaken", patient)
        db.session.commit()


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
        timestamp=datetime.now(time_tz),
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
        timestamp=datetime.now(time_tz),
        status='standing',
        language_id=language.id
    ) 
    return new_patient


def register_patient(activity):
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    
    print("before autocalling")
    app.auto_calling()

    communikation("update_patient")
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


def counter_become_inactive(counter_id):
    print("counter_become_inactiv")
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    counter.is_active = False
    db.session.commit()


def counter_become_active(counter_id):
    print("counter_become_activ")
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    print(counter, counter.is_active)
    if not counter.is_active:
        print('change')
        counter.is_active = True
        db.session.commit()
