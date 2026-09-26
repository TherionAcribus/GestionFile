import uuid
import markdown2
from flask import Blueprint, render_template, make_response, request, session, url_for, redirect, jsonify, current_app as app
from models import Language, Button, Activity, Patient, db, record_printer_status, page_editor_published_revision
from utils import choose_text_translation, get_button_translations, get_text_translation, replace_balise_phone, replace_balise_welcome, format_ticket_text, get_activity_message_translation, get_activity_inactivity_message_translation, balise_values, render_balises
from python.engine import peek_next_call_number, get_futur_patient, register_patient, register_pending_patient, register_journey_patient, find_patient_by_journey, activate_patient, qr_code_data_uri
from communication import communikation, send_app_notification
from auth_utils import make_patient_phone_token, check_patient_phone_token, patient_ticket_patient_id, check_kiosk_login_ticket, KIOSK_SESSION_KEY

patient_bp = Blueprint('patient', __name__)

def utility_processor():
    def get_css_url():
        return app.css_manager.get_current_css_url()
    return dict(get_css_url=get_css_url)

@patient_bp.route('/patient/kiosk_login/<ticket>')
def kiosk_login(ticket):
    """Connexion de la borne (étape 2, sans compte utilisateur).

    La borne a obtenu ce ticket signé via POST /api/kiosk/session_ticket sur
    présentation de son jeton applicatif. On vérifie signature + fraîcheur,
    puis on pose le drapeau de session borne — cookie de session HttpOnly
    classique, limité à la zone patient (la garde /patient de app.py est la
    seule à l'accepter ; il n'ouvre ni /admin ni /counter).

    Cette route est exemptée de la garde /patient dans app.py : c'est elle qui
    CRÉE la session. Un ticket invalide ou expiré renvoie simplement vers
    /patient (qui redirigera lui-même vers /login si la sécurité est active) ;
    la borne détecte la page de connexion et redemande un ticket frais.
    """
    if not check_kiosk_login_ticket(ticket):
        app.logger.warning("Ticket de connexion borne refusé (invalide ou expiré).")
        return redirect(url_for('patient.patients_front_page'))
    session[KIOSK_SESSION_KEY] = True
    # Session permanente : la borne tourne en continu, sa session ne doit pas
    # dépendre de la fermeture du navigateur embarqué.
    session.permanent = True
    app.logger.info("Session borne ouverte (zone patient).")
    return redirect(url_for('patient.patients_front_page'))


@patient_bp.route('/patient')
def patients_front_page():
    language_code = request.args.get('language_code')

    languages = db.session.query(Language).filter_by(is_active = True).order_by(Language.sort_order).all()

    # définition de la langue — un code absent/inconnu retombe sur la
    # référence française au lieu d'être stocké tel quel en session
    active_codes = {language.code for language in languages}
    if language_code not in active_codes:
        language_code = "fr"
    session['language_code'] = language_code

    return render_template('patient/patient_front_page.html',
                            languages=languages,
                            page_patient_display_translations=app.config["PAGE_PATIENT_DISPLAY_TRANSLATIONS"],
                            # Révision publiée par l'éditeur visuel : renvoyée
                            # dans l'accusé socket de (re)connexion.
                            page_revision=page_editor_published_revision("patient"))


@patient_bp.route('/patient/change_language/<language_code>')
def change_language(language_code):
    # Enregistrer le code de la langue dans la session
    return redirect(url_for('patient.patients_front_page', language_code=language_code))

@patient_bp.route('/patient/patient_title')
def patient_display_title():
    # {P}/{D}/{H} sont remplacés au rendu (nom de la pharmacie, date, heure) :
    # le titre est affiché hors contexte patient, d'où le helper « welcome ».
    return f'<p>{replace_balise_welcome(choose_text_translation("page_patient_title"))}</p>'

@patient_bp.route('/patient/patient_cancel')
def patient_cancel():
    session["language_code"] = "fr"
    return patient_right_page()

# affiche les boutons
@patient_bp.route('/patient/patient_buttons')
def patient_right_page():
    buttons = Button.query.order_by(Button.sort_order).filter_by(is_present = True, parent_button_id = None).all()

    # Aucun bouton présent (tous décochés) : message clair au lieu d'une page
    # vide / d'un plantage. Le personnel est par ailleurs alerté côté admin
    # (carte « Alertes » : diagnostics.collect_patient_page_alerts -> no_usable_button).
    if not buttons:
        communikation("patient", event="refresh_title")
        return render_template('patient/no_buttons.html')

    language_code = session.get('language_code', 'fr')
    button_translations = {}
    if language_code != "fr":
        button_translations = get_button_translations(buttons, language_code)
        page_patient_subtitle = get_text_translation("page_patient_subtitle", language_code)["translation"]
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)["translation"]
    else:
        page_patient_subtitle = app.config['PAGE_PATIENT_SUBTITLE']
        page_patient_interface_validate_cancel = app.config['PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL']

    max_length = 2 if buttons and buttons[0].shape == "square" else 4

    # Boutons parents affichés sans sous-bouton affiché : ils seront grisés et
    # rendus non cliquables par le gabarit (au lieu de mener à une page vide /
    # de planter sur children_buttons[0]). Voir diagnostics.find_empty_present_parents.
    empty_parent_ids = {
        b.id for b in buttons
        if b.is_parent and not any(child.is_present for child in b.dependent_buttons)
    }

    # UUID d'intention du parcours : il voyage dans les hx-vals des boutons
    # puis devient le journey_id du patient créé. Clé d'unicité serveur —
    # rejeu, second téléphone ou bascule impression/scan ne créent pas de
    # doublon (cf. register_journey_patient).
    journey_id = str(uuid.uuid4())

    buttons_content = render_template('patient/patient_buttons_left.html',
                            buttons=buttons,
                            button_translations=button_translations,
                            max_length=max_length,
                            journey_id=journey_id,
                            empty_parent_ids=empty_parent_ids,
                            page_patient_interface_validate_cancel=page_patient_interface_validate_cancel)
    
    subtitle_content = render_template(
        'patient/patient_default_subtitle.html', 
        page_patient_subtitle=page_patient_subtitle,
    )
    
    communikation("patient", event="refresh_title")
    
    return f"{buttons_content}{subtitle_content}"



@patient_bp.route('/patients_submit', methods=['POST'])
def patients_submit():
    app.logger.debug("patients_submit")
    # Récupération des données du formulaire
    app.logger.debug('SUBMIT %s', request.form)
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
        # ``message`` doit être résolu dans cette branche aussi : il n'était
        # défini qu'en français -> NameError (500) pour tout patient en
        # langue étrangère sur une activité inactive.
        # Le helper replie déjà sur le texte d'activité français quand la
        # traduction manque ; reste le repli sur le message par défaut.
        message = (get_activity_inactivity_message_translation(activity, language_code)
                   or get_text_translation("page_patient_disable_default_message", language_code)["translation"])
    else:
        default_subtitle = app.config['PAGE_PATIENT_SUBTITLE']
        message = activity.inactivity_message or app.config['PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE']

    return render_template('patient/activity_inactive.html',
                            page_patient_disable_default_message=message,
                            default_subtitle=default_subtitle,
                            page_patient_timer_activity_inactive=app.config["PAGE_PATIENT_TIMER_ACTIVITY_INACTIVE"])


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
    button_translations = {}
    if language_code != "fr":
        button_translations = get_button_translations(children_buttons, language_code)
        page_patient_interface_validate_cancel = get_text_translation("page_patient_interface_validate_cancel", language_code)["translation"]
    else:
        page_patient_interface_validate_cancel = app.config["PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL"]

    # Garde-fou : un parent sans sous-bouton affiché est normalement grisé côté
    # borne (cf. patient_right_page), mais on protège quand même l'accès direct
    # à cette route contre children_buttons[0] sur liste vide.
    max_length = 2 if children_buttons and children_buttons[0].shape == "square" else 4
    return render_template('patient/patient_buttons_left.html',
                            buttons=children_buttons,
                            button_translations=button_translations,
                            page_patient_interface_validate_cancel=page_patient_interface_validate_cancel,
                            max_length=max_length,
                            # L'intention de parcours suit la navigation dans
                            # les sous-boutons : l'inscription finale reste
                            # dédupliquée sur la même clé.
                            journey_id=request.form.get('journey'),
                            children=True)

# affiche la page de validation pour page gauche et droite
def display_validation_after_choice(request):
    activity_id = request.form.get('activity_id')
    app.logger.debug('reason %s', activity_id)

    # Si le bouton contient bien une activité
    if activity_id != "":
        if app.config.get("PAGE_PATIENT_DIRECT_PRINT", False):
             return patient_return_validation_page_and_print_data(print_ticket=True)
        activity = Activity.query.get(activity_id)
        #socketio.emit('trigger_valide_activity', {'activity': activity.id})
        return left_page_validate_patient(activity)
    
    app.logger.error("Le bouton ne possède pas d'activité")
    

# page de validation (QR Code, Impression, Validation, Annulation)
def left_page_validate_patient(activity):
    app.logger.debug('CONFIG QRCODE: %s', app.config.get("PAGE_PATIENT_QRCODE_DISPLAY"))
    # Numéro PRÉVISIONNEL seulement : peek ne consomme pas le compteur —
    # afficher la page ne doit pas brûler un numéro. Le vrai numéro est
    # attribué à l'inscription (print_and_validate / scan_and_validate).
    call_number = peek_next_call_number(activity)
    #new_patient = add_patient(call_number, activity)
    futur_patient = get_futur_patient(call_number, activity)
    app.logger.debug('futur_patient %s', futur_patient.id)
    # Chaque parcours QR est identifié par un UUID : encodé dans l'URL du QR,
    # rendu dans le fragment (data-journey-id), utilisé comme salle
    # Socket.IO dédiée — seule la borne affichant CE QR reçoit alors
    # update_scan_phone — ET stocké sur le patient créé (clé d'unicité de
    # l'inscription). Le même UUID circule depuis la page des boutons, donc
    # impression et scan partagent la même clé de parcours.
    journey_id = request.form.get('journey') or str(uuid.uuid4())
    # QR généré en mémoire (data URI) : l'image affichée correspond toujours
    # au parcours courant — un fichier statique réutilisé par un jour, une
    # langue ou une activité différente pourrait servir un QR périmé depuis
    # le cache navigateur.
    qr_data_uri = (qr_code_data_uri(futur_patient, journey_id=journey_id)
                   if app.config["PAGE_PATIENT_QRCODE_DISPLAY"] else None)
    text = f"{activity.name}"
    # NB : pas d'émission ici. À ce stade le patient n'est que « futur »
    # (get_futur_patient ne l'enregistre PAS en base) : la file n'a pas changé.
    # Diffuser update_patient bumperait la révision et forcerait tous les
    # clients à recharger un état identique. Le vrai update_patient part lors
    # de l'enregistrement effectif (confirmation impression/scan).

    page_patient_validation_message = choose_text_translation("page_patient_validation_message")
    page_patient_validation_message = replace_balise_phone(page_patient_validation_message, futur_patient)
    app.logger.debug("%s", page_patient_validation_message)

    # Traduction si disponible, texte source sinon — le repli fr/étranger
    # est géré par le helper, les deux branches deviennent identiques.
    page_patient_subtitle = get_activity_message_translation(
        activity, session.get('language_code', 'fr'))

    main_content = render_template('patient/patient_qr_right_page.html', 
                            qr_data_uri=qr_data_uri,
                            text=text,
                            activity=activity,
                            futur_patient=futur_patient,
                            page_patient_display_qrcode=app.config["PAGE_PATIENT_QRCODE_DISPLAY"],
                            page_patient_display_button_scan=app.config["PAGE_PATIENT_DISPLAY_BUTTON_SCAN"],
                            page_patient_display_scan_explanation=app.config["PAGE_PATIENT_DISPLAY_SCAN_EXPLANATION"],
                            journey_id=journey_id,
                            page_patient_validation_message=page_patient_validation_message,
                            page_patient_button_print_ticket_display_picture=app.config["PAGE_PATIENT_BUTTON_PRINT_TICKET_DISPLAY_PICTURE"],
                            page_patient_button_print_ticket_picture=app.config["PAGE_PATIENT_BUTTON_PRINT_TICKET_PICTURE"],
                            page_patient_button_cancel_display_picture=app.config["PAGE_PATIENT_BUTTON_CANCEL_DISPLAY_PICTURE"],
                            page_patient_button_cancel_picture=app.config["PAGE_PATIENT_BUTTON_CANCEL_PICTURE"],
                            page_patient_interface_validate_print=choose_text_translation("page_patient_interface_validate_print"),
                            page_patient_interface_validate_scan=choose_text_translation("page_patient_interface_validate_scan"),
                            page_patient_interface_validate_cancel=choose_text_translation("page_patient_interface_validate_cancel"),
                            page_patient_interface_scan_explanation=choose_text_translation("page_patient_interface_scan_explanation"),

                            )
    
    # si on veut afficher un message specifique (et qu'il existe). Retourné via oob-swap
    if app.config["PAGE_PATIENT_DISPLAY_SPECIFIC_MESSAGE"] and activity.specific_message != "":
        subtitle_content = render_template(
        'patient/patient_default_subtitle.html',
        page_patient_subtitle=page_patient_subtitle,
        is_specific_message=True
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
    Le but est de pouvoir réimprimer facilement, même si on a choisi Scan au départ.

    Mode impression (print_ticket=True) : l'inscription est créée EN ATTENTE
    (status='pending', hors file) avec un print_job_id. Le patient n'entre
    réellement dans la file qu'après confirmation de l'impression, via
    /patient/confirm_print (voir confirm_print). Ainsi un patient n'est jamais
    ajouté sans avoir reçu de ticket, et la page ne « valide » plus à l'aveugle.

    Mode scan (print_ticket=False) : pas d'impression locale, donc inscription
    immédiate dans la file (comportement inchangé)."""

    # Récupération et traitement des données comme avant
    activity_id = request.form.get('activity_id')
    activity = Activity.query.get(activity_id)
    journey_id = request.form.get('journey') or None

    # Un parcours = une inscription : on retrouve le patient du parcours
    # (clé unique Patient.journey_id) avant de créer quoi que ce soit.
    existing = find_patient_by_journey(journey_id)
    if existing is None:
        if print_ticket:
            # Inscription en attente : elle sera activée à la confirmation d'impression.
            print_job_id = str(uuid.uuid4())
            new_patient = register_pending_patient(activity, print_job_id,
                                                   journey_id=journey_id)
        else:
            # Scan : aucune impression, on active immédiatement (inchangé).
            print_job_id = None
            new_patient = register_patient(activity, journey_id=journey_id)

            # Notification "nouveau patient" : uniquement quand il rejoint
            # réellement la file (en mode impression, différée à confirm_print).
            if activity.notification:
                send_app_notification(origin="activity", data={"patient": new_patient, "activity": activity})
    elif existing.status == 'pending':
        if print_ticket:
            # Re-clic « Imprimer » sur le même parcours : on conserve le même
            # print_job_id — confirm_print reste idempotent, aucune nouvelle
            # inscription n'est créée.
            print_job_id = existing.print_job_id
            if not print_job_id:
                print_job_id = str(uuid.uuid4())
                existing.print_job_id = print_job_id
                db.session.commit()
            new_patient = existing
        else:
            # Bascule impression -> scan sur le même parcours : l'inscription
            # en attente entre en file sans ticket (activation notifie).
            _activate_and_notify(existing)
            new_patient = existing
            print_job_id = None
    else:
        # Parcours déjà conclu (standing/appelé/servi) : on réaffiche la
        # conclusion sans créer de doublon ni relancer l'impression auto.
        new_patient = existing
        print_ticket = False
        print_job_id = None

    print_data = format_ticket_text(new_patient, activity)
    # On ne journalise PAS le contenu du ticket, seulement sa taille encodée.
    app.logger.debug(f"print_data généré ({len(print_data)} caractères base64)")

    if 'HX-Request' in request.headers:
        # Requête provenant de HTMX
        # Rendre le template de la page de conclusion
        html_content = patient_conclusion_page(new_patient.id,
                                                print_ticket=print_ticket,
                                                print_data=print_data,
                                                print_job_id=print_job_id)

        # Inclure les données d'impression dans un en-tête HX-Trigger si nécessaire (voir plus bas)
        response = make_response(html_content)
        return response
    else:
        # Redirection traditionnelle si pas de requête AJAX
        return redirect(url_for('patient.patient_conclusion_page', patient_id=new_patient.id))


def _alert_staff_print_failure(patient, code, message):
    """ Signale au personnel qu'un ticket n'a pas pu être imprimé pour un
    patient donné (notification comptoir + trace dans le tableau de bord
    imprimante)."""
    detail = f"Ticket non imprimé pour le patient {patient.call_number} ({code or 'inconnu'})"
    try:
        send_app_notification(origin="printer_error", data={"message": detail})
    except Exception as e:
        app.logger.error(f"Alerte personnel (impression) impossible: {e}")

    # Trace horodatée dans le tableau de bord imprimante du staff. En base et
    # non plus dans app.config["PRINTER_INFOS"] : visible par tous les process
    # et sans commit implicite de la session (écriture sur connexion dédiée —
    # on est au milieu du flux d'écriture de confirm_print).
    try:
        record_printer_status("printer_error", detail, is_error=True)
        communikation("admin", event="refresh_printer_dashboard")
    except Exception as e:
        app.logger.error(f"Journalisation dashboard imprimante impossible: {e}")


@patient_bp.route('/patient/confirm_print', methods=['POST'])
def confirm_print():
    """ Confirme au serveur le résultat de l'impression locale d'un ticket.

    Appelée par la Borne (patients.js) après l'appel à l'API d'impression. Selon
    le résultat, active l'inscription pending (succès) ou applique le
    comportement d'échec configuré (PAGE_PATIENT_PRINT_FAIL_BEHAVIOR :
    'ask' -> laisser le patient décider ; 'keep' -> conserver ; 'cancel' ->
    annuler).

    Idempotente : un second appel pour un patient déjà traité renvoie l'état
    MÉTIER correspondant ('activated' / 'cancelled'), pas le statut interne
    brut. La borne retente l'acquittement tant qu'elle n'a pas de réponse :
    si la première réponse a été perdue en vol, 'standing' signifierait « déjà
    en file » mais serait lu comme un échec par le client."""
    data = request.get_json(silent=True) or request.form
    print_job_id = data.get('print_job_id')
    success = data.get('success')
    # Tolère bool JSON, "true"/"false" (form), etc.
    if isinstance(success, str):
        success = success.lower() in ('true', '1', 'yes', 'on')
    else:
        success = bool(success)
    code = data.get('code')
    message = data.get('message')

    if not print_job_id:
        return jsonify({'status': 'error', 'reason': 'missing_print_job_id'}), 400

    patient = Patient.query.filter_by(print_job_id=print_job_id).first()
    if patient is None:
        # Inconnu ou déjà purgé (inscription pending expirée).
        return jsonify({'status': 'expired'}), 410

    # Déjà confirmé : idempotence (renvoi réseau, réessai de la file locale
    # de la borne...). Tout statut hors 'print_failed' signifie que le
    # patient a rejoint (ou dépassé) la file.
    if patient.status != 'pending':
        if patient.status == 'print_failed':
            return jsonify({'status': 'cancelled', 'call_number': patient.call_number}), 200
        return jsonify({'status': 'activated', 'call_number': patient.call_number}), 200

    if success:
        _activate_and_notify(patient)
        return jsonify({'status': 'activated', 'call_number': patient.call_number}), 200

    # Échec d'impression : comportement piloté par la configuration Admin
    # (onglet Page Patient). 'cancel' | 'keep' | 'ask'.
    behavior = app.config.get("PAGE_PATIENT_PRINT_FAIL_BEHAVIOR", "ask")
    _alert_staff_print_failure(patient, code, message)

    if behavior == "keep":
        # On conserve le patient dans la file malgré l'absence de ticket.
        _activate_and_notify(patient)
        return jsonify({
            'status': 'activated_no_ticket',
            'call_number': patient.call_number
        }), 200

    if behavior == "cancel":
        # On annule l'inscription : pas de ticket => pas de patient dans la
        # file. journey_id est libéré : un nouveau scan du même QR crée alors
        # une inscription neuve au lieu de retrouver ce parcours annulé.
        patient.status = 'print_failed'
        patient.journey_id = None
        db.session.commit()
        return jsonify({
            'status': 'cancelled',
            'call_number': patient.call_number
        }), 200

    # behavior == "ask" (défaut) : on laisse l'inscription EN ATTENTE et on
    # renvoie au patient les options à afficher (Réessayer / Appeler le
    # personnel). La décision explicite du patient déclenchera soit une nouvelle
    # tentative (re-print + confirm_print), soit /patient/print_call_staff.
    # L'inscription pending abandonnée sera purgée par le TTL.
    return jsonify({
        'status': 'ask',
        'call_number': patient.call_number,
        'show_retry': bool(app.config.get("PAGE_PATIENT_PRINT_FAIL_SHOW_RETRY", True)),
        'show_staff': bool(app.config.get("PAGE_PATIENT_PRINT_FAIL_SHOW_STAFF", True)),
        # Garde-fou anti-blocage : délai (s) avant retour auto à l'accueil si le
        # patient ne choisit rien. 0 = jamais.
        'abandon_timer': int(app.config.get("PAGE_PATIENT_PRINT_FAIL_ABANDON_TIMER", 60))
    }), 200


def _activate_and_notify(patient):
    """ Active un patient pending (entrée en file) et émet la notification
    'nouveau patient' si l'activité le demande (différée jusqu'à l'entrée réelle
    en file, pour ne pas annoncer un patient qui aurait pu être annulé)."""
    activate_patient(patient)
    if patient.activity and patient.activity.notification:
        send_app_notification(origin="activity", data={"patient": patient, "activity": patient.activity})


@patient_bp.route('/patient/print_call_staff', methods=['POST'])
def print_call_staff():
    """ Décision explicite du patient « Appeler le personnel » après un échec
    d'impression (mode 'ask'). On alerte le personnel ET on ajoute le patient à
    la file (il a demandé de l'aide, il doit être pris en charge malgré
    l'absence de ticket). Idempotente."""
    data = request.get_json(silent=True) or request.form
    print_job_id = data.get('print_job_id')
    if not print_job_id:
        return jsonify({'status': 'error', 'reason': 'missing_print_job_id'}), 400

    patient = Patient.query.filter_by(print_job_id=print_job_id).first()
    if patient is None:
        return jsonify({'status': 'expired'}), 410

    if patient.status != 'pending':
        # Déjà traité (idempotence).
        return jsonify({'status': patient.status, 'call_number': patient.call_number}), 200

    _alert_staff_print_failure(patient, 'call_staff', "Le patient demande de l'aide (ticket non imprimé)")
    _activate_and_notify(patient)
    return jsonify({
        'status': 'activated_no_ticket',
        'call_number': patient.call_number,
        'staff_called': True
    }), 200


@patient_bp.route('/patient/print_abandon', methods=['POST'])
def print_abandon():
    """ Abandon de l'écran d'échec (mode 'ask') : le patient n'a rien choisi et
    le garde-fou anti-blocage a expiré. On annule l'inscription pending (elle
    n'entre pas dans la file) avant de renvoyer la borne à l'accueil. Idempotente."""
    data = request.get_json(silent=True) or request.form
    print_job_id = data.get('print_job_id')
    if not print_job_id:
        return jsonify({'status': 'error', 'reason': 'missing_print_job_id'}), 400

    patient = Patient.query.filter_by(print_job_id=print_job_id).first()
    if patient is None:
        return jsonify({'status': 'expired'}), 410

    if patient.status == 'pending':
        # Même libération de journey_id que la branche 'cancel' de
        # confirm_print : le parcours annulé ne doit pas bloquer un nouveau
        # scan du même QR.
        patient.status = 'print_failed'
        patient.journey_id = None
        db.session.commit()
    return jsonify({'status': 'cancelled', 'call_number': patient.call_number}), 200



def patient_validate_scan(activity_id, journey_id=None):
    """ Fct appelée lors du scan du QRCode (validation).

    Avec un journey_id, l'inscription est dédupliquée sur le parcours :
    un rejeu ou un second téléphone retrouve le patient existant sans
    recréer de ligne ni renvoyer la notification « nouveau patient »."""
    activity = Activity.query.get(activity_id)
    if journey_id:
        new_patient, created = register_journey_patient(activity, journey_id)
    else:
        new_patient = register_patient(activity)
        created = True
    if created and activity.notification:
        send_app_notification(origin="activity", data={"patient":new_patient, "activity": activity})
    return new_patient


@patient_bp.route('/patient/scan_already_validate', methods=['POST'])
def patient_scan_already_validate():
    """ Fct appelée une fois la scan fait pour retourner la page de confirmation sur l'interface patient"""
    patient_id = request.form.get('patient_id')
    app.logger.debug('already scanned %s', patient_id)
    if patient_id:
        return patient_conclusion_page(int(patient_id), print_ticket=False, print_data=False)
    # Repli compat (client sans patient_id) : le numéro d'appel est réutilisé
    # d'un jour à l'autre, on prend donc le patient le PLUS RÉCENT portant ce
    # numéro — pas le premier trouvé, qui pouvait dater d'hier.
    patient_call_number = request.form.get('patient_call_number')
    patient = (Patient.query.filter_by(call_number=patient_call_number)
               .order_by(Patient.id.desc()).first())
    return patient_conclusion_page(patient.id if patient else 0,
                                   print_ticket=False, print_data=False)


@patient_bp.route('/patient/cancel_patient')
def cancel_patient():
    session['language_code'] = "fr"
    return patient_right_page()
    

@patient_bp.route('/patient/conclusion_page/<int:patient_id>')
def patient_conclusion_page(patient_id, print_ticket=False, print_data=None, print_job_id=None):
    # ``print_ticket`` doit avoir une valeur par defaut : la fonction sert a la
    # fois d'aide interne (appelee avec tous ses arguments depuis
    # patients_submit et patient_scan_already_validate) ET de vue pour cette
    # route, que Flask appelle avec le seul <patient_id>. Sans defaut, l'acces
    # par URL levait un TypeError (500) -- ce qui cassait la redirection non-HTMX
    # de patients_submit, seul chemin emprunte quand le navigateur n'envoie pas
    # d'en-tete HX-Request. False correspond au mode « pas d'impression en
    # cours », comme pour l'arrivee par scan.
    #
    # Le patient est identifié par son id — PAS par call_number : celui-ci est
    # réutilisé d'un jour à l'autre et une recherche dessus pouvait ressortir
    # un ancien patient (confirmation affichée pour un autre).
    app.logger.debug('CONFIG QRCODE CONCLUSION: %s', app.config.get("PAGE_PATIENT_QRCODE_DISPLAY"))
    patient = Patient.query.get(patient_id)
    call_number = patient.call_number if patient else ""
    # QR régénéré à la volée pour le patient RÉEL (et non retrouvé sur disque
    # : le numéro « futur » du QR de validation peut différer du numéro
    # attribué, et le chemin d'impression directe ne générait aucun fichier).
    qr_data_uri = (qr_code_data_uri(patient)
                   if patient and app.config["PAGE_PATIENT_QRCODE_DISPLAY"] else None)
    page_patient_confirmation_message = choose_text_translation("page_patient_confirmation_message")
    page_patient_confirmation_message = replace_balise_phone(page_patient_confirmation_message, patient)

    # print_ticket == False si mode Scan. On défini si on affiche ou non les boutons pour réimprimer et prolonger
    reprint = False
    if (print_ticket and app.config["PAGE_PATIENT_PRINT_AFTER_PRINT"]) or (not print_ticket and app.config["PAGE_PATIENT_PRINT_AFTER_SCAN"]):
        reprint = True

    # Libellés du flux d'impression, résolus dans la langue courante du patient
    # (repli FR). Injectés dans la page pour que patients.js (qui construit
    # l'overlay dynamiquement) les utilise au lieu de textes codés en dur.
    # {N} n'est PAS résolu ici : la borne le remplace par le numéro dans un
    # élément mis en valeur (.print_status_number). Les autres balises
    # ({P} {D} {H} {A}) le sont, comme pour les autres textes « avant appel ».
    _print_label_keys = {
        "printing": "page_patient_interface_printing",
        "print_failed": "page_patient_interface_print_failed",
        "retry": "page_patient_interface_retry",
        "call_staff": "page_patient_interface_call_staff",
        "staff_called": "page_patient_interface_staff_called",
        "no_ticket": "page_patient_interface_no_ticket",
        "print_failed_staff": "page_patient_interface_print_failed_staff",
        "back": "page_patient_interface_done_back",
    }
    raw_labels = {name: choose_text_translation(key) for name, key in _print_label_keys.items()}
    values = balise_values(
        patient,
        with_activity=any("{a}" in str(text).lower() for text in raw_labels.values()),
    )
    values.pop("N", None)
    print_ui_labels = {name: render_balises(text, values) for name, text in raw_labels.items()}

    return render_template('patient/conclusion_page.html',
                        print_ui_labels=print_ui_labels,
                        call_number=call_number,
                        qr_data_uri=qr_data_uri,
                        page_patient_confirmation_message=page_patient_confirmation_message,
                        page_patient_end_timer=app.config["PAGE_PATIENT_END_TIMER"],
                        page_patient_display_qrcode=app.config["PAGE_PATIENT_QRCODE_DISPLAY"],
                        page_patient_interface_done_print=choose_text_translation("page_patient_interface_done_print"),
                        page_patient_interface_done_extend=choose_text_translation("page_patient_interface_done_extend"),
                        page_patient_interface_done_back=choose_text_translation("page_patient_interface_done_back"),
                        print_data=print_data,
                        print_ticket=print_ticket,
                        print_job_id=print_job_id,
                        reprint=reprint
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
    # Le code vient de l'URL du QR : une langue inconnue ou désactivée
    # retombe sur la référence française.
    if language_code != "fr" and not Language.query.filter_by(
            code=language_code, is_active=True).first():
        language_code = "fr"
    session["language_code"] = language_code
    if language_code != "fr":
        phone_title = get_text_translation("phone_title", language_code)["translation"]
    else:
        phone_title = app.config['PHONE_TITLE']

    # UUID du parcours QR (query string posée par qr_code_data_uri). Propagé au
    # ping via le gabarit pour que update_scan_phone cible la salle de la
    # borne affichant CE QR.
    journey_id = request.args.get('journey', '')
    # Lien de suivi signé (QR de conclusion) : suivre le passage existant —
    # sans cookies et SANS inscription au ping.
    ticket = request.args.get('ticket', '')

    if ticket:
        pid = patient_ticket_patient_id(ticket)
        patient = Patient.query.get(pid) if pid is not None else None
        if patient is None:
            return render_template('patient/phone_link_invalid.html',
                                   phone_title=phone_title,
                                   language_code=language_code)
        return render_template('/patient/phone.html',
                                phone_title=phone_title,
                                patient_id=patient.call_number,
                                activity_id=patient.activity_id,
                                journey_id='',
                                ticket=ticket,
                                language_code=language_code,
                                page_revision=page_editor_published_revision("phone"))

    if request.cookies.get('patient_call_number') != patient_id:
        if request.cookies.get('patient_id') != patient_id:
            response = make_response(render_template('/patient/phone.html',
                                                    patient_id=patient_id,
                                                    activity_id=activity_id,
                                                    phone_title=phone_title,
                                                    journey_id=journey_id,
                                                    ticket='',
                                                    language_code=language_code,
                                                    page_revision=page_editor_published_revision("phone")))
            response.set_cookie('patient_id', "", expires=0)
            response.set_cookie('patient_call_number', "", expires=0)
            response.set_cookie('patient_token', "", expires=0)
            return response
    return render_template('/patient/phone.html',
                            phone_title=phone_title,
                            patient_id=patient_id,
                            activity_id=activity_id,
                            journey_id=journey_id,
                            ticket='',
                            language_code=language_code,
                            # Révision publiée : renvoyée dans l'accusé socket
                            # de (re)connexion (accusés « écran » de l'éditeur).
                            page_revision=page_editor_published_revision("phone"))


@patient_bp.route('/patient/phone/status', methods=['GET'])
def phone_patient_status():
    """ Permet au téléphone du patient de vérifier son statut réel.

    Utilisé au (re)connect du WebSocket pour rattraper une notification
    "your_turn" manquée pendant une coupure (SocketIO ne rejoue pas les
    évènements manqués -- fréquent sur mobile : verrouillage d'écran,
    bascule wifi/4G, mise en arrière-plan du navigateur).
    """
    # Le cookie patient_id est falsifiable : seul un patient_token signé
    # (posé par /patient/phone/ping) autorise à lire le statut d'un patient.
    if not check_patient_phone_token(request.cookies.get('patient_token'),
                                     request.cookies.get('patient_id'),
                                     request.cookies.get('patient_call_number')):
        return jsonify({"status": None}), 200

    patient = Patient.query.get(request.cookies.get('patient_id'))
    if not patient:
        return jsonify({"status": None}), 200

    return jsonify({"status": patient.status, "call_number": patient.call_number}), 200


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
    journey_id = request.form.get('journey')
    ticket = request.form.get('ticket')

    if ticket:
        # Lien de suivi signé (QR de conclusion) : on SUIT le passage existant
        # — jamais d'inscription, même sans cookie ni après leur expiration.
        pid = patient_ticket_patient_id(ticket)
        patient = Patient.query.get(pid) if pid is not None else None
        if patient is None:
            return render_template('patient/phone_link_invalid_fragment.html')
    elif journey_id:
        # Le parcours QR est la clé d'unicité de l'inscription : un rejeu ou
        # un second téléphone retrouve le MÊME patient — plus de doublon.
        patient = find_patient_by_journey(journey_id)
        if patient is None:
            patient = patient_validate_scan(activity_id, journey_id=journey_id)
        elif patient.status == 'pending':
            # Inscription en attente d'impression finalement validée par le
            # scan : elle entre en file sans ticket.
            _activate_and_notify(patient)
        # Émission ciblée dans la salle du parcours (création comme rejeu) :
        # seule la borne affichant CE QR reçoit update_scan_phone, avec le
        # numéro RÉELLEMENT attribué ET l'id du patient — la conclusion se
        # fait par id, le call_number étant réutilisé d'un jour à l'autre.
        # Sans journey (QR généré avant cette version) on ne diffuse plus à
        # toutes les bornes : une confirmation ne doit pas atterrir sur le
        # mauvais écran.
        from sockets import scan_journey_room
        communikation("patient", event="update_scan_phone",
                      data={"call_number": patient.call_number,
                            "patient_id": patient.id},
                      room=scan_journey_room(journey_id))
    # si déja inscrit — le cookie patient_id seul est falsifiable : exiger le
    # jeton signé. Sans lui (ou s'il est périmé), on retombe sur une nouvelle
    # inscription, comme pour un client sans cookie (QR sans journey).
    elif check_patient_phone_token(request.cookies.get('patient_token'),
                                 request.cookies.get('patient_id'),
                                 request.cookies.get('patient_call_number')):
        patient = Patient.query.get(request.cookies.get('patient_id'))
    # si pas encore inscrit
    else:
        patient = patient_validate_scan(activity_id)

    phone_lines = []

    for line in range(1, 7):
        phone_line = (app.config[f'PHONE_LINE{line}'] if language_code == "fr"
                      else get_text_translation(f'phone_line{line}', language_code)['translation'])
        phone_lines.append(replace_balise_phone(phone_line, patient))
    activity = Activity.query.get(activity_id)
    # Langue du formulaire, comme les lignes ci-dessus — session.get() et le
    # paramètre pouvaient diverger ; le helper gère le repli fr/étranger.
    specific_message = get_activity_message_translation(activity, language_code)

    # Convertir le texte des phone_lines de markdown en HTML
    phone_lines = [markdown2.markdown(line) for line in phone_lines]

    response = make_response(render_template('/patient/phone_confirmation.html', 
                                            patient=patient,
                                            activity_id=activity_id,
                                            language_code=language_code,
                                            phone_lines=phone_lines,
                                            your_turn=False,
                                            specific_message = specific_message,
                                            phone_display_specific_message=app.config['PHONE_DISPLAY_SPECIFIC_MESSAGE'],
                                            phone_center=app.config['PHONE_CENTER']))
    response.set_cookie('patient_id', str(patient.id), max_age=60*30)  # Cookie valable pour 20 minutes
    response.set_cookie('patient_call_number', str(patient.call_number), max_age=60*30)
    # Jeton signé liant ce couple de cookies : preuve opposable pour la salle
    # /socket_phone, /patient/phone/status et la réaffichage ci-dessus.
    response.set_cookie('patient_token', make_patient_phone_token(patient.id, patient.call_number), max_age=60*30)
    return response


@patient_bp.route('/patient/phone/your_turn', methods=['POST'])
def phone_patient_your_turn():
    """
    Endpoint spécifique pour afficher le message 'c'est votre tour'
    """
    activity_id = request.form.get('activity_id')
    language_code = request.form.get('language_code')
    # Même garde que /patient/phone/status : un cookie patient_id forgé ne doit
    # pas dévoiler la page "votre tour" d'un autre patient.
    if check_patient_phone_token(request.cookies.get('patient_token'),
                                 request.cookies.get('patient_id'),
                                 request.cookies.get('patient_call_number')):
        patient = Patient.query.get(request.cookies.get('patient_id'))
    else:
        patient = None

    phone_lines = []

    for line in range(1, 7):
        phone_line = (app.config[f'PHONE_YOUR_TURN_LINE{line}'] if language_code == "fr"
                      else get_text_translation(f'phone_your_turn_line{line}', language_code)['translation'])
        phone_lines.append(replace_balise_phone(phone_line, patient))
    activity = Activity.query.get(activity_id)
    specific_message = get_activity_message_translation(activity, language_code)

    # Convertir le texte des phone_lines de markdown en HTML
    phone_lines = [markdown2.markdown(line) for line in phone_lines]

    return render_template('/patient/phone_confirmation.html', 
                                            patient=patient,
                                            phone_lines=phone_lines,
                                            your_turn=True,
                                            specific_message = specific_message,
                                            phone_display_specific_message=app.config['PHONE_DISPLAY_SPECIFIC_MESSAGE'],
                                            phone_center=app.config['PHONE_CENTER'])
