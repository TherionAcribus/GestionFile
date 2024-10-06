from flask import Blueprint, render_template, request, jsonify, url_for, current_app as app
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from models import db, ConfigOption, Counter, Pharmacist, Patient, Activity
from python.engine import call_next
from utils import replace_balise_announces

counter_bp = Blueprint('counter', __name__)

@counter_bp.route('/counter/paper_add')
def counter_paper_add():
    return render_template('counter/paper_add.html',
                            add_paper=app.config["ADD_PAPER"])

@counter_bp.route('app/counter/paper_add', methods=['POST'])
def app_counter_paper_add():
    action = False if request.form.get("action") == "deactivate" else True
    return action_add_paper(action)

@counter_bp.route('/counter/paper_add/<int:add_paper>', methods=['GET'])
def action_add_paper(add_paper):
    print("action_add_paper", add_paper)
    try:
        print("action_add_paper", add_paper)
        config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
        config_option.value_bool = add_paper
        db.session.commit()
        app.config["ADD_PAPER"] = add_paper
        app.communikation("counter", event="paper")
        app.communikation("app_counter", data={"add_paper": add_paper}, event="paper")
        return counter_paper_add()
    except Exception as e:
        print(e)


@counter_bp.route('/counter/paper_add', methods=['POST'])
def app_paper_add():
    if request.form.get('action') is None:
        return jsonify({"status": app.config["ADD_PAPER"]}), 200 # 
    else:
        add_paper_action = True if request.form.get('action') == "activate" else False
        print("app_paper_add", add_paper_action)
        try:
            config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
            config_option.value_bool = add_paper_action
            db.session.commit()
            app.config["ADD_PAPER"] = add_paper_action

            #app.communikation("counter", event="paper")
            app.communikation("app_counter", {"add_paper": add_paper_action}, event="paper")
        
            return "", 200

        except Exception as e:
            print(e)


@counter_bp.route('/app/counter/update_staff', methods=['POST'])
def app_update_counter_staff():
    return update_counter_staff()


@counter_bp.route('/counter/update_staff', methods=['POST'])
def web_update_counter_staff():
    return update_counter_staff()


def update_counter_staff():
    print("RECONNEXION ")
    print(request.form)
    counter = Counter.query.get(request.form.get('counter_id'))  
    initials = request.form.get('initials')
    # la demande vient elle de l'App en mode réduit ?
    from_app = request.form.get("app") == "True"
    staff = Pharmacist.query.filter(func.lower(Pharmacist.initials) == func.lower(initials)).first()
    if staff:
        # si demande de déconnexion
        if request.form.get('deconnect').lower() == "true":
            # deconnexion de tous les postes
            deconnect_staff_from_all_counters(staff)
        # Ajout du membre de l'équipe au comptoir        
        counter.staff = staff
        db.session.commit()

        # mise à jour des boutons
        app.communikation("counter", event="update buttons")
        # On rappelle la base de données pour être sûr que bonne personne au bon comptoir
        if from_app:
            return api_is_staff_on_counter(request.form.get('counter_id'))
        else:
            return is_staff_on_counter(request.form.get('counter_id'))

    # Si les initiales ne correspondent à rien
    # on déconnecte l'utilisateur précedemement connecté
    counter.staff = None
    db.session.commit()
    # mise à jour des boutons
    app.communikation("counter", event="update buttons")
    # on affiche une erreur à la place du nom
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
def api_is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    if counter.staff:
        print("counter", counter.staff)
        return jsonify({"staff": counter.staff.to_dict()}), 200
    else:
        return "", 204 


def remove_counter_staff():
    counter = Counter.query.get(request.form.get('counter_id')) 
    counter.staff = None
    db.session.commit()

    # mise à jour des boutons
    app.communikation("counter", event="update buttons")
    return is_staff_on_counter(request.form.get('counter_id'))


def deconnect_staff_from_all_counters(staff):
    """ Déconnecte le membre de l'équipe de tous les comptoirs """
    print("deconnecte")
    for counter in Counter.query.all():
        if counter.staff == staff:
            counter.staff = None
            db.session.commit()
            #socketio.emit("trigger_disconnect_staff", {})
            # mise à jour des boutons
            app.communikation("counter", event="update buttons")


@counter_bp.route('/api/counter/is_patient_on_counter/<int:counter_id>', methods=['GET'])
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


@counter_bp.route('/counter/patients_queue_for_counter/<int:counter_id>', methods=['GET'])
def patients_queue_for_counter(counter_id):
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('/counter/patients_queue_for_counter.html', patients=patients, counter_id=counter_id)


def update_counter_auto_calling(counter_id, auto_calling_value):
    """ FOnction commune pour le changement de l'autocalling web et App"""
    try:
        counter = Counter.query.get(counter_id)
        if not counter:
            app.logger.error(f"Counter not found: {counter_id}")
            return False, "Counter not found", 404

        counter.auto_calling = auto_calling_value
        db.session.commit()

        # Mise à jour de app.config
        if auto_calling_value:
            app.config["AUTO_CALLING"].append(counter.id)
        else:
            app.config["AUTO_CALLING"].remove(counter.id)

        # si on relance autocalling, on appelle automatiquement le patient suivant
        # uniquement si le comptoir est inactif
        if auto_calling_value and not counter.is_active:
            patient = call_next(counter.id)
            app.communikation("app_counter", event="update_auto_calling", data={"counter_id": counter.id, "patient": patient.to_dict()})
            # mise à jour écran ... bizarremment l'audio est dans le call next....
            text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], patient)
            app.communikation("update_screen", event="add_calling", data={"id": patient.id, "text": text})

        return True, {"status": counter.auto_calling}, 200

    except SQLAlchemyError as e:
        db.session.rollback()
        app.logger.error(f'Database error: {str(e)}')
        return False, str(e), 500
    except Exception as e:
        app.logger.error(f'Unexpected error: {str(e)}')
        return False, str(e), 500


@counter_bp.route('/counter/update_switch_auto_calling', methods=['POST'])
def update_switch_auto_calling():
    counter_id = request.values.get('counter_id')
    value = request.values.get('value')
    auto_calling_value = value.lower() == "true"

    success, result, status_code = update_counter_auto_calling(counter_id, auto_calling_value)

    # Notification de changement
    app.communikation("app_counter", event="change_auto_calling", 
                                    data={"counter_id": counter_id, "autocalling": auto_calling_value})
    if not success:
        return result, status_code
    return "", 204


@counter_bp.route('/app/counter/auto_calling', methods=['POST'])
def app_auto_calling():
    counter_id = request.form.get('counter_id')
    action = request.form.get('action')

    if action is None:
        counter = Counter.query.get(counter_id)
        return jsonify({"status": counter.auto_calling}), 200

    auto_calling_value = action == "activate"

    success, result, status_code = update_counter_auto_calling(counter_id, auto_calling_value)

    # notification de changement
    app.communikation("counter", event="refresh_auto_calling", data={"auto_calling": auto_calling_value})

    if not success:
        return jsonify({"error": result}), status_code
    return jsonify(result), status_code
    

@counter_bp.route('/app/counter/init_app', methods=['POST'])
def app_init_app():
    """ Fonction d'initialisation de l'application pour récupérer les infos utiles en une seule requete """
    counter_id = request.form.get('counter_id')
    counter = Counter.query.get(counter_id)
    return jsonify({"autocalling": counter.auto_calling,
                    "add_paper": app.config["ADD_PAPER"]}), 200


@counter_bp.route('/app/counter/remove_staff', methods=['POST'])
def app_remove_counter_staff():
    print("deconnction")
    remove_counter_staff()
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
    print("counter_select_patient", counter_id, patient_id)
    app.call_specific_patient(counter_id, patient_id)
    app.communikation("update_patient")
    return '', 204


@counter_bp.route('/counter/relaunch_patient_call/<int:counter_id>', methods=['GET'])
def relaunch_patient_call(counter_id):
    patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    audiofile = f'patient_{patient.call_number}.mp3'
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)
    app.communikation("update_audio", event="audio", data=audio_url)
    return '', 204
