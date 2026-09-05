import os
from flask import Blueprint, render_template, request, jsonify, url_for, current_app as app
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from models import db, ConfigOption, Counter, Pharmacist, Patient, Activity, get_queue_revision
from python.engine import generate_audio_calling
from communication import communikation, send_app_notification
from services import calling_service
from transactions import atomic
from auth_utils import require_app_token_or_login

counter_bp = Blueprint('counter', __name__)

@counter_bp.route('/counter/paper_add')
def counter_paper_add():
    return render_template('counter/paper_add.html',
                            add_paper=app.config["ADD_PAPER"])


"""@counter_bp.route('app/counter/paper_add', methods=['POST'])
def app_counter_paper_add():
    action = False if request.form.get("action") == "deactivate" else True
    return app_paper_add(action)
"""

@counter_bp.route('/counter/paper_add/<int:add_paper>', methods=['GET'])
def action_add_paper(add_paper, from_printer=False):
    app.logger.debug("action_add_paper %s", add_paper)
    try:
        config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
        config_option.value_bool = add_paper
        db.session.commit()
        app.config["ADD_PAPER"] = add_paper
        communikation("counter", event="paper")
        if not from_printer:
            communikation("app_counter", data={"add_paper": add_paper}, event="paper")
            send_app_notification(origin="low_paper", data={"add_paper": add_paper})
        return counter_paper_add()
    except Exception:
        # Avant : `print(e)` puis retour implicite de None -- Flask levait alors
        # "View function did not return a valid response" et le client recevait
        # un 500 sans le moindre diagnostic, la vraie erreur n'ayant ete ecrite
        # que sur stdout.
        db.session.rollback()
        app.logger.exception("Echec de la mise a jour de l'etat papier (add_paper=%s)", add_paper)
        return jsonify({"error": "paper_update_failed"}), 500


@counter_bp.route('/app/counter/paper_add', methods=['POST'])
@require_app_token_or_login
def app_paper_add():
    if request.form.get('action') is None:
        return jsonify({"status": app.config["ADD_PAPER"]}), 200 # 
    else:
        add_paper_action = True if request.form.get('action') == "activate" else False
        app.logger.debug("app_paper_add %s", add_paper_action)
        try:
            config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
            config_option.value_bool = add_paper_action
            db.session.commit()
            app.config["ADD_PAPER"] = add_paper_action

            communikation("app_counter", {"add_paper": add_paper_action}, event="paper")

            return {"status": app.config["ADD_PAPER"] }, 200

        except Exception:
            # Meme correctif que action_add_paper : sans retour explicite, l'App
            # recevait un 500 vide au lieu d'une erreur exploitable.
            db.session.rollback()
            app.logger.exception("Echec de la mise a jour de l'etat papier depuis l'App")
            return jsonify({"error": "paper_update_failed"}), 500


@counter_bp.route('/app/counter/update_staff', methods=['POST'])
@require_app_token_or_login
def app_update_counter_staff():
    return update_counter_staff()


@counter_bp.route('/counter/update_staff', methods=['POST'])
def web_update_counter_staff():
    return update_counter_staff()


def update_counter_staff():
    app.logger.debug('ma_request %s', request.form)
    counter = Counter.query.get(request.form.get('counter_id'))
    initials = request.form.get('initials')
    # la demande vient elle de l'App en mode reduit ?
    from_app = request.form.get("app") == "True"
    # `or ''` : le champ est absent des requetes qui ne le transmettent pas ;
    # `None.lower()` levait alors une AttributeError (500).
    veut_deconnecter = (request.form.get('deconnect') or '').strip().lower() == "true"
    staff = Pharmacist.query.filter(func.lower(Pharmacist.initials) == func.lower(initials)).first()
    if staff:
        # Point 6 : deconnexion de tous les postes ET rattachement au nouveau
        # comptoir dans UNE transaction. Auparavant deux commits successifs (un
        # dans deconnect_staff_from_all_counters, un ici) : un echec du second
        # laissait le membre deconnecte de partout sans etre connecte nulle part.
        with atomic():
            if veut_deconnecter:
                app.logger.debug("Deconnexion des autres comptoirs")
                deconnect_staff_from_all_counters(staff)
            counter.staff = staff

        # mise a jour des boutons
        communikation("counter", event="update buttons")
        # On rappelle la base de donnees pour etre sur que bonne personne au bon comptoir
        if from_app:
            return api_is_staff_on_counter(request.form.get('counter_id'))
        else:
            return is_staff_on_counter(request.form.get('counter_id'))

    # Si les initiales ne correspondent a rien
    # on deconnecte l'utilisateur precedemement connecte
    with atomic():
        counter.staff = None
    # mise a jour des boutons
    communikation("counter", event="update buttons")
    # on affiche une erreur a la place du nom
    if from_app:
        return "", 204
    else:
        return render_template('counter/staff_on_counter.html', staff=False)


@counter_bp.route('/counter/is_staff_on_counter/<int:counter_id>', methods=['GET'])
def is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    # emet un signal pour provoquer le réaffichage de la liste des activités
    #socketio.emit("trigger_connect_staff", {})
    return render_template('counter/staff_on_counter.html', staff=counter.staff)


@counter_bp.route('/api/counter/is_staff_on_counter/<int:counter_id>', methods=['GET'])
@require_app_token_or_login
def api_is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    if counter.staff:
        app.logger.debug('counter %s', counter.staff)
        return jsonify({"staff": counter.staff.to_dict()}), 200
    else:
        return "", 204 


def remove_counter_staff(origine=None):
    counter_id = request.form.get('counter_id')
    counter = Counter.query.get(counter_id) 
    counter.staff = None
    db.session.commit()

    # quand on se déconnecte on enleve l'autocalling
    update_counter_auto_calling(counter_id=counter_id, auto_calling_value=False)

    if origine == "dashboard":
        communikation("app_counter", event="disconnect_user", data={'counter_id': counter.id, "staff": "Admin"})

    # mise à jour des boutons
    communikation("counter", event="update buttons")
    return is_staff_on_counter(request.form.get('counter_id'))


def deconnect_staff_from_all_counters(staff):
    """ Deconnecte le membre de l'equipe de tous les comptoirs.

    NE COMMITTE PAS : la transaction appartient a l'appelant
    (update_counter_staff), pour que la deconnexion et la reconnexion forment un
    tout indivisible.
    """
    app.logger.debug("Deconnexion en cours...")

    # Recupere tous les comptoirs associes a ce membre du personnel
    affected_counters = Counter.query.filter_by(staff=staff).all()

    if not affected_counters:
        app.logger.debug("Aucun comptoir a deconnecter pour ce membre du personnel.")
        return

    for counter in affected_counters:
        app.logger.debug('counter-> %s', counter)
        counter.staff = None
        communikation("app_counter", event="disconnect_user", data={'counter_id': counter.id, "staff": staff.name})

    # TODO A MODIFIER....
    communikation("counter", event="update buttons")

    app.logger.debug(f"Deconnexion reussie de {len(affected_counters)} comptoir(s).")

@counter_bp.route('/api/counter/is_patient_on_counter/<int:counter_id>', methods=['GET'])
@require_app_token_or_login
def app_is_patient_on_counter(counter_id):
    """ Renvoie les informations du patient actuel au comptoir (pour le client) pour l'App (démarrage)"""
    patient = Patient.query.filter(
        Patient.counter.has(id=counter_id),
        Patient.status.in_(['ongoing', 'calling'])
        ).first()
    if patient:
        return jsonify(patient.to_dict()), 200
    else:
        return jsonify({"id": None, "counter_id": counter_id}), 200   


@counter_bp.route('/api/counter/<int:counter_id>/state', methods=['GET'])
@require_app_token_or_login
def api_counter_state(counter_id):
    """ État autoritatif complet du comptoir en une seule snapshot atomique.

    Remplace, au démarrage de l'App et à la resynchronisation, la série de
    requêtes séparées (patient en cours + liste des patients + init_app + staff)
    qui pouvaient se chevaucher et laisser l'App dans un état incohérent (course
    de démarrage). Renvoie aussi la révision courante de la file : le client la
    mémorise pour, ensuite, détecter les évènements Socket.IO manqués. """
    counter = Counter.query.get(counter_id)
    if not counter:
        return jsonify({"error": "counter not found"}), 404

    current = Patient.query.filter(
        Patient.counter.has(id=counter_id),
        Patient.status.in_(['ongoing', 'calling'])
    ).first()

    # La boucle ci-dessous lit p.activity.name, p.activity.staff_id et
    # p.language.code : sans chargement anticipe, chaque patient declenchait deux
    # requetes supplementaires (N+1). Meme motif que patients_queue_for_counter.
    standing = (
        Patient.query
        .filter_by(status="standing")
        .options(joinedload(Patient.activity), joinedload(Patient.language))
        .order_by(Patient.timestamp, Patient.id)
        .all()
    )
    standing_list = [{
        "id": p.id,
        "call_number": p.call_number,
        "activity_id": p.activity_id,
        "activity": p.activity.name,
        "activity_is_staff": p.activity.staff_id,
        "language_code": p.language.code,
    } for p in standing]

    activity_staff = Activity.query.filter_by(is_staff=True).all()

    return jsonify({
        "revision": get_queue_revision(),
        "counter_id": counter_id,
        "counter_name": counter.name,
        "current_patient": current.to_dict() if current else {"id": None, "counter_id": counter_id},
        "standing_list": standing_list,
        "autocalling": counter.auto_calling,
        "add_paper": app.config["ADD_PAPER"],
        "activities_staff": [activity.to_dict_for_app() for activity in activity_staff],
        "staff": counter.staff.to_dict() if counter.staff else None,
    }), 200


@counter_bp.route('/counter/patients_queue_for_counter/<int:counter_id>', methods=['GET'])
def patients_queue_for_counter(counter_id):
    # Le gabarit lit patient.activity.name et patient.language.code par ligne :
    # joinedload charge ces deux relations en amont pour éviter un N+1.
    patients = (
        Patient.query
        .filter_by(status='standing')
        .options(joinedload(Patient.activity), joinedload(Patient.language))
        .order_by(Patient.timestamp, Patient.id)
        .all()
    )
    return render_template('/counter/patients_queue_for_counter.html', patients=patients, counter_id=counter_id)


def update_counter_auto_calling(counter_id, auto_calling_value):
    """ Fonction commune pour le changement de l'autocalling web et App.

    Delegue au service : c'est lui qui detient la sequence « appeler + activer +
    annoncer », qui etait ici recopiee a l'identique depuis app.py (au commentaire
    pres). Il n'y a plus non plus de liste app.config["AUTO_CALLING"] a maintenir
    en parallele de Counter.auto_calling.
    """
    try:
        return calling_service.set_auto_calling(counter_id, auto_calling_value)
    except SQLAlchemyError as e:
        db.session.rollback()
        app.logger.exception("Erreur base de donnees sur l'appel automatique")
        return False, str(e), 500
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Erreur inattendue sur l'appel automatique")
        return False, str(e), 500


@counter_bp.route('/counter/update_switch_auto_calling', methods=['POST'])
def update_switch_auto_calling():
    counter_id = request.values.get('counter_id')
    value = request.values.get('value')
    auto_calling_value = value.lower() == "true"

    success, result, status_code = update_counter_auto_calling(counter_id, auto_calling_value)

    # Notification de changement
    communikation("app_counter", event="change_auto_calling", 
                                    data={"counter_id": counter_id, "autocalling": auto_calling_value})
    if not success:
        return result, status_code
    return "", 204


@counter_bp.route('/app/counter/auto_calling', methods=['POST'])
@require_app_token_or_login
def app_auto_calling():
    counter_id = request.form.get('counter_id')
    action = request.form.get('action')
    app.logger.debug('autocalling %s', action)

    if action is None:
        counter = Counter.query.get(counter_id)
        return jsonify({"status": counter.auto_calling}), 200

    auto_calling_value = action == "activate"

    success, result, status_code = update_counter_auto_calling(counter_id, auto_calling_value)

    # notification de changement
    communikation("counter", event="refresh_auto_calling", data={"auto_calling": auto_calling_value})

    app.logger.debug('%s %s %s', success, result, status_code)
    if not success:
        return jsonify({"error": result}), status_code
    app.logger.debug("OL ")
    return jsonify(result), status_code
    

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @counter_bp.route('/app/counter/init_app', methods=['POST'])
def app_init_app():
    """ Fonction d'initialisation de l'application pour récupérer les infos utiles en une seule requete """
    counter_id = request.form.get('counter_id')
    counter = Counter.query.get(counter_id)
    activity_staff = Activity.query.filter_by(is_staff=True).all()
    activities_data = [activity.to_dict_for_app() for activity in activity_staff]
    return jsonify({"autocalling": counter.auto_calling,
                    "add_paper": app.config["ADD_PAPER"],
                    "activities_staff": activities_data,
                    "counter_name": counter.name          
                    }), 200


@counter_bp.route('/app/counter/remove_staff', methods=['POST'])
@require_app_token_or_login
def app_remove_counter_staff():
    app.logger.debug("deconnction")
    remove_counter_staff()
    return '', 200

@counter_bp.route('/dash/counter/remove_staff', methods=['POST'])
def dashboard_remove_counter_staff():
    remove_counter_staff(origine="dashboard")
    communikation("admin", event="refresh_counter_dashboard")
    
    return '', 200


@counter_bp.route('/counter/remove_staff', methods=['POST'])
def web_remove_counter_staff():
    return remove_counter_staff()


@counter_bp.route('/counter/list_of_activities', methods=['POST'])
def list_of_activities():
    activities = Activity.query.all()
    staff_id = request.form.get('staff_id')
    if staff_id == "0":
        # TODO Créer un user "Anonyme" ????
        # si personne au comptoir, on affiche toutes les activités
        staff_activities_ids = [activity.id for activity in activities]

    else:     
        staff = Pharmacist.query.get(staff_id)
        # on renvoie les activités du membre de l'équipe pour les cocher dans la liste
        staff_activities_ids = [activity.id for activity in staff.activities]

    return render_template('counter/counter_list_of_activities.html', activities=activities, staff_activities_ids=staff_activities_ids)


@counter_bp.route('/counter/select_patient/<int:counter_id>/<int:patient_id>', methods=['GET'])
def counter_select_patient(counter_id, patient_id):
    """ Appeler lors du choix d'un patient spécifique au comptoir """
    app.logger.debug('counter_select_patient %s %s', counter_id, patient_id)
    # Le service emet lui-meme update_patient et notify_patient_phone : les
    # refaire ici doublait les messages temps reel.
    calling_service.call_specific(counter_id, patient_id)
    return '', 204


def do_relaunch_patient_call(counter_id):
    patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if not patient:
        return

    audiofile = f'patient_{patient.call_number}.mp3'
    audio_path = os.path.join(app.static_folder, 'audio/annonces', audiofile)

    if os.path.exists(audio_path):
        audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)
    else:
        # Le mp3 est peut-être encore en cours de génération en tâche de fond
        # (cf. trigger_async_audio_calling, appelé juste avant par call_next),
        # ou l'annonce sonore est désactivée. "Relancer l'appel" est une action
        # manuelle rare déclenchée à la main par le pharmacien : contrairement à
        # l'appel du patient suivant, un léger délai ici est acceptable, donc on
        # (re)génère de façon synchrone plutôt que de diffuser un lien mort.
        audio_url = generate_audio_calling(counter_id, patient, language_code=patient.language.code)
        if not audio_url:
            return

    communikation("update_audio", event="audio", data=audio_url)


@counter_bp.route('/counter/relaunch_patient_call/<int:counter_id>', methods=['GET'])
def relaunch_patient_call(counter_id):
    do_relaunch_patient_call(counter_id)
    return '', 204


@counter_bp.route('/app/counter/relaunch_patient_call/<int:counter_id>', methods=['POST'])
@require_app_token_or_login
def app_relaunch_patient_call(counter_id):
    do_relaunch_patient_call(counter_id)
    return '', 204


# Ces actions modifient l'état : POST uniquement. Un ancien GET reçoit 405
# (Method Not Allowed), renvoyé automatiquement par Flask.
@counter_bp.route('/api/counter/put_standing_list/<int:patient_id>/<int:activity_id>', methods=['POST'])
@require_app_token_or_login
def put_waiting_list_with_activity(patient_id, activity_id):
    return handle_patient_from_app(patient_id, action="standing", activity_id=activity_id)


@counter_bp.route('/api/counter/put_standing_list/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def put_waiting_list(patient_id):
    return handle_patient_from_app(patient_id, action="standing")

@counter_bp.route('/api/counter/validate_patient/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def validate_patient_from_app(patient_id):
    return handle_patient_from_app(patient_id, action="validate")

@counter_bp.route('/api/counter/delete_patient/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def delete_patient_from_app(patient_id):
    return handle_patient_from_app(patient_id, action="delete")

def handle_patient_from_app(patient_id, action, activity_id=None):    
    patient = Patient.query.get(patient_id)
    app.logger.debug('STANDING %s', patient)

    if action == "delete":
        status = "done"  # en cas de suppression de la part du comptoir, on marque le patient comme terminé
    elif action == "standing":
        status = "standing"
    elif action == "validate":
        status = "done"

    if patient:
        # on change les infos du patient
        patient.status = status
        patient.counter = None

        # Si une nouvelle activité est spécifiée, on met à jour l'activité du patient
        if activity_id is not None:
            new_activity = Activity.query.get(activity_id)
            if new_activity:
                patient.activity_id = activity_id
                patient.activity = new_activity
                app.logger.debug(f"Activity changed to: {new_activity.name}")
        
        db.session.commit()

        # rafraichissement de la page
        communikation("update_screen", event="remove_calling", data={"id": patient_id})

        # rafraichissement des infos
        communikation("update_patient")

        # notification au staff concerné si connecté
        if activity_id is not None:
            counters = get_all_counter_ids_from_activity(activity_id)
            if counters:
                send_app_notification(origin="patient_for_staff_from_app", data={"patient":patient, "counters": counters})

        return "", 201
    else:
        return 'Patient not found', 404
    

def get_all_counter_ids_from_activity(activity_id):
    """ Permet de récuperer tous les comptoirs associés à une activité (staff) et d'en faire une liste d'id"""
    counters = get_counters_from_activity(activity_id)
    if counters:
        return [counter.id for counter in counters]
    else:
        return None


def get_counters_from_activity(activity_id):
    """ Permet de récuperer tous les comptoirs associés à une activité (staff)"""
    # Récupérer l'activité à partir de l'ID
    activity = Activity.query.get(activity_id)
    if not activity:
        app.logger.debug(f"Activity with ID {activity_id} not found.")
        return None

    # Récupérer le staff associé à cette activité
    staff = activity.staff
    if not staff:
        app.logger.debug(f"No staff associated with Activity ID {activity_id}.")
        return None

    # Récupérer tous les comptoirs associés à ce staff
    counters = staff.counter if isinstance(staff.counter, list) else [staff.counter]

    if not counters:
        app.logger.debug(f"No counters associated with staff ID {staff.id} for Activity ID {activity_id}.")
        return None

    # Retourner les comptoirs
    return counters
