import markdown2
import json
from flask import Blueprint, render_template, make_response, request, session, url_for, redirect, jsonify, current_app as app
from models import Language, Button, Activity, Patient, db
from utils import choose_text_translation, get_buttons_translation, get_text_translation, replace_balise_phone, format_ticket_text, get_activity_message_translation
from python.engine import get_next_call_number, get_futur_patient, register_patient, create_qr_code
from communication import communikation, send_app_notification

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
    return redirect(url_for('patient.patients_front_page', language_code=language_code))

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
        page_patient_subtitle = get_text_translation("page_patient_subtitle", language_code)["translation"]
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)["translation"]
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
    
    communikation("patient", event="refresh_title")
    
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
        default_subtitle = get_text_translation("page_patient_subtitle", language_code)["translation"]
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
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)["translation"]
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
    
    communikation("update_patient")

    page_patient_validation_message = choose_text_translation("page_patient_validation_message")
    page_patient_validation_message = replace_balise_phone(page_patient_validation_message, futur_patient)
    print(page_patient_validation_message)

    if session.get('language_code', 'fr') == "fr":
        page_patient_subtitle=activity.specific_message
    else:
        page_patient_subtitle=get_activity_message_translation(activity, session.get('language_code', 'fr'))

    main_content = render_template('patient/patient_qr_right_page.html', 
                            image_name_qr=image_name_qr, 
                            text=text,
                            activity=activity,
                            futur_patient=futur_patient,                            
                            page_patient_display_button_scan=app.config["PAGE_PATIENT_DISPLAY_BUTTON_SCAN"],
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
        page_patient_subtitle=page_patient_subtitle
        )

        return f"{main_content}{subtitle_content}"
    
    return main_content

@patient_bp.route('/patient/print_and_validate', methods=['POST'])
def print_and_validate():
    return patient_return_validation_page_and_print_data(print_ticket=True)


@patient_bp.route('/patient/scan_and_validate', methods=['POST'])
def patient_scan_and_validate():
    return patient_return_validation_page_and_print_data(print_ticket=False)


def patient_return_validation_page_and_print_data(print_ticket):
    """ Fournit le template pour la page conclusion et les données d'impressions
    Les données d'impression sont fournies dans tous les cas, mais utilisées selon l'ordre d'impression. 
    Le but est de pouvoir réimprimer facilement, même si on a choisi Scan au départ."""

    # Récupération et traitement des données comme avant
    activity_id = request.form.get('activity_id')
    activity = Activity.query.get(activity_id)
    new_patient = register_patient(activity)
    print_data = format_ticket_text(new_patient, activity)
    print("print_data", print_data)
    communikation("app_patient", flag="print", data=print_data)

    # Gestion des notifications si nécessaire
    if activity.notification:
        send_app_notification(origin="activity", data={"patient": new_patient, "activity": activity})

    if 'HX-Request' in request.headers:
        # Requête provenant de HTMX
        # Rendre le template de la page de conclusion
        html_content = patient_conclusion_page(new_patient.call_number, print_ticket=print_ticket, print_data=print_data)

        # Inclure les données d'impression dans un en-tête HX-Trigger si nécessaire (voir plus bas)
        response = make_response(html_content)
        return response
    else:
        # Redirection traditionnelle si pas de requête AJAX
        return redirect(url_for('patient.patient_conclusion_page', call_number=new_patient.call_number))
        

def patient_validate_scan(activity_id):
    """ Fct appelée lors du scan du QRCode (validation) """
    activity = Activity.query.get(activity_id)
    new_patient = register_patient(activity)
    if activity.notification:
        send_app_notification(origin="activity", data={"patient":new_patient, "activity": activity})
    return new_patient


@patient_bp.route('/patient/scan_already_validate', methods=['POST'])
def patient_scan_already_validate():
    """ Fct appelée une fois la scan fait pour retourner la page de confirmation sur l'interface patient"""
    patient_call_number = request.form.get('patient_call_number')
    print("already scanned", patient_call_number)
    return patient_conclusion_page(patient_call_number)


@patient_bp.route('/patient/cancel_patient')
def cancel_patient():
    session['language_code'] = "fr"
    return patient_right_page()
    

@patient_bp.route('/patient/conclusion_page/<call_number>')
def patient_conclusion_page(call_number, print_ticket, print_data=None):
    image_name_qr = f"qr_patient-{call_number}.png" 

    patient = Patient.query.filter_by(call_number=call_number).first()
    page_patient_confirmation_message = choose_text_translation("page_patient_confirmation_message")
    page_patient_confirmation_message = replace_balise_phone(page_patient_confirmation_message, patient)

    return render_template('patient/conclusion_page.html',
                        call_number=call_number,
                        image_name_qr=image_name_qr,
                        page_patient_confirmation_message=page_patient_confirmation_message,
                        page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"],
                        page_patient_interface_done_print=choose_text_translation("page_patient_interface_done_print"),
                        page_patient_interface_done_extend=choose_text_translation("page_patient_interface_done_extend"),
                        page_patient_interface_done_back=choose_text_translation("page_patient_interface_done_back"),
                        print_data=print_data,
                        print_ticket=print_ticket
                        )

@patient_bp.route('/patient/refresh')
def patient_refresh():
    """ Permet de rafraichir la page des patients pour effectuer des changements """
    communikation("patient", event="refresh")
    return '', 204


@patient_bp.route('/patient/phone/<language_code>/<patient_id>/<int:activity_id>', methods=['GET'])
def phone_patient(language_code, patient_id, activity_id):
    """ 
    Page pour téléphone appelé lors du scan
    Affiche la structure puis les infos spécifiques au patient sont chargées lors du ping en htmx
    On regarde s'il y a un cookie déja placé (par ping). Si c'est le cas et que le numéro est différent c'est qu'il y a un nouvel enregistrement
    Dans ce cas on efface le cookie, sinon c'est un rafraichissement de la page et donc on le laisse.
    """
    app.logger.debug(f"TITLE2 {app.config['PHONE_TITLE']}")
    session["language_code"] = language_code
    if language_code != "fr":
        phone_title = get_text_translation("phone_title", language_code)["translation"]
        print("phone_title", phone_title)
    else:
        phone_title = app.config['PHONE_TITLE']

    if request.cookies.get('patient_call_number') != patient_id:
        if request.cookies.get('patient_id') != patient_id:
            response = make_response(render_template('/patient/phone.html', 
                                                    patient_id=patient_id, 
                                                    activity_id=activity_id,
                                                    phone_title=phone_title,
                                                    language_code=language_code))
            response.set_cookie('patient_id', "", expires=0)
            response.set_cookie('patient_call_number', "", expires=0)
            return response
    return render_template('/patient/phone.html',
                            phone_title=phone_title,
                            patient_id=patient_id, 
                            activity_id=activity_id,
                            language_code=language_code)


@patient_bp.route('/patient/phone/ping', methods=['POST'])
def phone_patient_ping():
    """ 
    Fct qui s'execute une fois que la page phone est chargee.
    Renvoie la page de confirmation de l'activité
    Place un cookie pendant 20 minutes. Le but du cookie est de stocker l'id du patient
    Si le cookie existe, c'est qu'il s,'est déja inscrit et qu'il ne faut pas l'inscrire une seconde fois
    mais simplement lui réafficher les informations. Cela arrive s'il rafraichit la page.
    S'il vient à la page phone avec un autre numéro (nouvelle inscription) le cookie est effacé dans la fonction précédente
    """
    activity_id = request.form.get('activity_id')
    language_code = request.form.get('language_code')
    # si déja inscrit
    if request.cookies.get('patient_id'):
        patient = Patient.query.get(request.cookies.get('patient_id'))
    # si pas encore inscrit
    else:
        patient = patient_validate_scan(activity_id)
        communikation("patient", event="update_scan_phone")

    phone_lines = []

    if language_code != "fr":
        for line in range(1, 7):            
            exec(f"phone_line{line} = get_text_translation('phone_line{line}', language_code)['translation']"),
            exec(f"phone_line{line} = replace_balise_phone(phone_line{line}, patient)"),
            phone_lines.append(eval(f"phone_line{line}"))
        activity = Activity.query.get(activity_id)
        specific_message = get_activity_message_translation(activity, session.get('language_code', 'fr'))
    else:
        for line in range(1, 7):
            exec(f"phone_line{line} = app.config['PHONE_LINE{line}']"),
            exec(f"phone_line{line} = replace_balise_phone(phone_line{line}, patient)"),
            phone_lines.append(eval(f"phone_line{line}"))
        specific_message= Activity.query.get(activity_id).specific_message

    # Convertir le texte des phone_lines de markdown en HTML
    phone_lines = [markdown2.markdown(line) for line in phone_lines]

    response = make_response(render_template('/patient/phone_confirmation.html', 
                                            patient=patient,
                                            phone_lines=phone_lines,
                                            specific_message = specific_message,
                                            phone_display_specific_message=app.config['PHONE_DISPLAY_SPECIFIC_MESSAGE'],
                                            phone_center=app.config['PHONE_CENTER']))
    response.set_cookie('patient_id', str(patient.id), max_age=60*30)  # Cookie valable pour 20 minutes
    response.set_cookie('patient_call_number', str(patient.call_number), max_age=60*30)
    return response

