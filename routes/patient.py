import os
import qrcode
from flask import Blueprint, render_template, request, session, url_for, redirect, current_app as app
from models import Language, Button, Activity, Patient, db
from utils import choose_text_translation, get_buttons_translation, get_text_translation, replace_balise_phone, format_ticket_text
from python.engine import get_next_call_number, get_futur_patient, register_patient

patient_bp = Blueprint('patient', __name__)

@patient_bp.route('/patient')
def patients_front_page():
    language_code = request.args.get('language_code')

    print("language_code", language_code)

    languages = db.session.query(Language).filter_by(is_active = True).order_by(Language.sort_order).all()
    print("languages_list", languages)

    print("language_code START", language_code)
    # définition de la langue
    if language_code is None or language_code == "fr":
        session['language_code'] = "fr"
        language_code = "fr"
    else:
        session['language_code'] = language_code

    return render_template('patient/patient_front_page.html',
                            languages=languages,
                            page_patient_display_translations=app.config["PAGE_PATIENT_DISPLAY_TRANSLATIONS"],
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"])



@patient_bp.route('/patient/change_language/<language_code>')
def change_language(language_code):
    # Enregistrer le code de la langue dans la session
    return redirect(url_for('patients_front_page', language_code=language_code))

@patient_bp.route('/patient/patient_title')
def patient_display_title():
    return f'<p>{choose_text_translation("page_patient_title")}</p>'

@patient_bp.route('/patient/patient_cancel')
def patient_cancel():
    session["language_code"] = "fr"
    return patient_right_page()

# affiche les boutons
@patient_bp.route('/patient/patient_buttons')
def patient_right_page():
    buttons = Button.query.order_by(Button.sort_order).filter_by(is_present = True, parent_button_id = None).all()
    
    language_code = session.get('language_code', 'fr')
    if language_code != "fr":
        buttons = get_buttons_translation(buttons, language_code)
        page_patient_subtitle = get_text_translation("page_patient_subtitle", language_code)
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)
    else:
        page_patient_subtitle = app.config['PAGE_PATIENT_SUBTITLE']
        page_patient_interface_validate_cancel = app.config['PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL']

    max_length = 2 if buttons[0].shape == "square" else 4

    buttons_content = render_template('patient/patient_buttons_left.html', 
                            buttons=buttons,
                            max_length=max_length,
                            page_patient_interface_validate_cancel=page_patient_interface_validate_cancel,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"])
    
    subtitle_content = render_template(
        'patient/patient_default_subtitle.html', 
        page_patient_subtitle=page_patient_subtitle,
    )
    
    app.communikation("patient", event="refresh_title")
    
    return f"{buttons_content}{subtitle_content}"



@patient_bp.route('/patients_submit', methods=['POST'])
def patients_submit():
    print("patients_submit")
    # Récupération des données du formulaire
    print('SUBMIT', request.form)
    if request.form.get('is_active')  == 'False':
        return display_activity_inactive(request)
    if request.form.get('is_parent')  == 'True':
        return display_children_buttons_for_right_page(request)
    else:
        return display_validation_after_choice(request)


def display_activity_inactive(request):
    activity = Activity.query.get(request.form.get('activity_id'))
    
    language_code = session.get('language_code', 'fr')
    if language_code != "fr":
        default_subtitle = get_text_translation("page_patient_subtitle", language_code)
    else:
        default_subtitle = app.config['PAGE_PATIENT_SUBTITLE']

        message = app.config['PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE']
        if activity.inactivity_message != "":
            message = activity.inactivity_message

    return render_template('patient/activity_inactive.html',
                            page_patient_disable_default_message=message,
                            default_subtitle=default_subtitle,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"],
                            page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"])


@patient_bp.route("/patient/default_subtitle")
def display_default_children_text():   

    return render_template('patient/patient_default_subtitle.html',
                            page_patient_subtitle=choose_text_translation("page_patient_subtitle"))

""""@app.route("/patients/cancel_children")
def cancel_children():
    print("cancel_children")
    return redirect(url_for('patients_front_page', language_code="fr"))  """

# affiche les boutons "enfants" de droite
def display_children_buttons_for_right_page(request):
    children_buttons = Button.query.order_by(Button.sort_order).filter_by(is_present = True, parent_button_id = request.form.get('button_id')).all()
    
    language_code = session.get('language_code', 'fr')
    if language_code != "fr":
        children_buttons = get_buttons_translation(children_buttons, language_code)
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)
    else:
        page_patient_interface_validate_cancel = app.config["PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL"]

    max_length = 2 if children_buttons[0].shape == "square" else 4
    return render_template('patient/patient_buttons_left.html', 
                            buttons=children_buttons,
                            page_patient_interface_validate_cancel=page_patient_interface_validate_cancel,
                            max_length=max_length,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"],
                            children=True)

# affiche la page de validation pour page gauche et droite
def display_validation_after_choice(request):
    activity_id = request.form.get('activity_id')
    print("reason", activity_id)

    # Si le bouton contient bien une activité
    if activity_id != "":
        activity = Activity.query.get(activity_id)
        #socketio.emit('trigger_valide_activity', {'activity': activity.id})
        return left_page_validate_patient(activity)
    
    app.logger.error("Le bouton ne possède pas d'activité")
    

# page de validation (QR Code, Impression, Validation, Annulation)
def left_page_validate_patient(activity):
    call_number = get_next_call_number(activity)
    #new_patient = add_patient(call_number, activity)
    futur_patient = get_futur_patient(call_number, activity)
    print("futur_patient", futur_patient.id)
    image_name_qr = create_qr_code(futur_patient)
    text = f"{activity.name}"
    # rafraichissement des pages display et counter
    # envoye de data pour être récupéré sous forme de liste par PySide
    
    app.communikation("update_patient")

    page_patient_validation_message = choose_text_translation("page_patient_validation_message")
    page_patient_validation_message = replace_balise_phone(page_patient_validation_message, futur_patient)
    print(page_patient_validation_message)

    main_content = render_template('patient/patient_qr_right_page.html', 
                            image_name_qr=image_name_qr, 
                            text=text,
                            activity=activity,
                            futur_patient=futur_patient,
                            page_patient_validation_message=page_patient_validation_message,
                            page_patient_structure=app.config["PAGE_PATIENT_STRUCTURE"],
                            page_patient_interface_validate_print=choose_text_translation("page_patient_interface_validate_print"),
                            page_patient_interface_validate_scan=choose_text_translation("page_patient_interface_validate_scan"),
                            page_patient_interface_validate_cancel=choose_text_translation("page_patient_interface_validate_cancel"),
                            )
    
    # si on veut afficher un message specifique (et qu'il existe). Retourné via oob-swap
    if app.config["PAGE_PATIENT_DISPLAY_SPECIFIC_MESSAGE"] and activity.specific_message != "":
        subtitle_content = render_template(
        'patient/patient_default_subtitle.html', 
        page_patient_subtitle=activity.specific_message
        )

        return f"{main_content}{subtitle_content}"
    
    return main_content


@patient_bp.route('/patient/print_and_validate', methods=['POST'])
def print_and_validate():
    activity = Activity.query.get(request.form.get('activity_id'))
    new_patient = register_patient(activity)
    print("new_patient", new_patient)
    text = format_ticket_text(new_patient, activity)
    print("text", text)

    if activity.notification:
        app.communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    app.communikation("app_patient", flag="print", data=text)
    app.communication("update_patient_app", data={"type": "print", "message": text})
    return patient_conclusion_page(new_patient.call_number)

def patient_validate_scan(activity_id):
    """ Fct appelée lors du scan du QRCode (validation) """
    activity = Activity.query.get(activity_id)
    new_patient = register_patient(activity)
    if activity.notification:
        app.communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    return new_patient

@patient_bp.route('/patient/scan_already_validate', methods=['POST'])
def patient_scan_already_validate():
    """ Fct appelée une fois la scan fait pour retourner la page de confirmation sur l'interface patient"""
    patient_call_number = request.form.get('patient_call_number')
    print("already scanned", patient_call_number)
    return patient_conclusion_page(patient_call_number)

@patient_bp.route('/patient/scan_and_validate', methods=['POST'])
def patient_scan_and_validate():
    """ Fct appelée si clic sur le bouton de validation """
    activity = Activity.query.get(request.form.get('activity_id'))
    new_patient = register_patient(activity)
    if activity.notification:
        app.communikation("app_counter", flag="notification", data = f"Demande pour '{activity.name}'")
    return patient_conclusion_page(new_patient.call_number)


@patient_bp.route('/patient/cancel_patient')
def cancel_patient():
    session['language_code'] = "fr"
    return patient_right_page()


@patient_bp.route('/patient/conclusion_page')
def patient_conclusion_page(call_number):
    image_name_qr = f"qr_patient-{call_number}.png" 

    patient = Patient.query.filter_by(call_number=call_number).first()
    page_patient_confirmation_message = choose_text_translation("page_patient_confirmation_message")
    page_patient_confirmation_message = replace_balise_phone(page_patient_confirmation_message, patient)

    return render_template('patient/conclusion_page.html',
                            call_number=call_number,
                            image_name_qr=image_name_qr,
                            page_patient_confirmation_message=page_patient_confirmation_message,
                            page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"],
                            page_patient_interface_done_print = choose_text_translation("page_patient_interface_done_print"),
                            page_patient_interface_done_extend = choose_text_translation("page_patient_interface_done_extend"),
                            page_patient_interface_done_back = choose_text_translation("page_patient_interface_done_back")
                            )

def set_server_url(app, request):
    # Stockage de l'adresse pour la génération du QR code
    if request.host_url == "http://127.0.0.1:5000/":
        server_url = app.config.get('NETWORK_ADRESS')
    else:
        server_url = request.host_url
    app.config['SERVER_URL'] = server_url

def create_qr_code(patient):
    print("create_qr_code")
    print(patient, patient.id, patient.call_number, patient.activity)
    if app.config['PAGE_PATIENT_QRCODE_WEB_PAGE']:
        if "SERVER_URL" not in app.config:
            set_server_url(app, request)
        data = f"{app.config['SERVER_URL']}/patient/phone/{patient.call_number}/{patient.activity.id}"
    else :
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

@patient_bp.route('/patient/refresh')
def patient_refresh():
    """ Permet de rafraichir la page des patients pour effectuer des changements """
    app.communikation("patient", event="refresh")
    return '', 204