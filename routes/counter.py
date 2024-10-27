from flask import Blueprint, render_template, request, jsonify, url_for, current_app as app
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from models import db, ConfigOption, Counter, Pharmacist, Patient, Activity
from python.engine import call_next
from utils import replace_balise_announces
from python.engine import counter_become_active, counter_become_inactive
from communication import communikation, send_app_notification

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
def action_add_paper(add_paper):
    print("action_add_paper", add_paper)
    try:
        print("action_add_paper", add_paper)
        config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
        config_option.value_bool = add_paper
        db.session.commit()
        app.config["ADD_PAPER"] = add_paper
        communikation("counter", event="paper")
        communikation("app_counter", data={"add_paper": add_paper}, event="paper")
        send_app_notification(origin="printer_paper", data={"add_paper": add_paper})
        return counter_paper_add()
    except Exception as e:
        print(e)


@counter_bp.route('app/counter/paper_add', methods=['POST'])
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
            communikation("app_counter", {"add_paper": add_paper_action}, event="paper")
        
            return {"status": app.config["ADD_PAPER"] }, 200

        except Exception as e:
            print(e)


@counter_bp.route('/app/counter/update_staff', methods=['POST'])
def app_update_counter_staff():
    return update_counter_staff()


@counter_bp.route('/counter/update_staff', methods=['POST'])
def web_update_counter_staff():
    return update_counter_staff()


def update_counter_staff():
    print('ma_request', request.form)
    counter = Counter.query.get(request.form.get('counter_id'))  
    initials = request.form.get('initials')
    # la demande vient elle de l'App en mode réduit ?
    from_app = request.form.get("app") == "True"
    staff = Pharmacist.query.filter(func.lower(Pharmacist.initials) == func.lower(initials)).first()
    if staff:
        # si demande de déconnexion
        if request.form.get('deconnect').lower() == "true" or request.form.get('deconnect') == 'True':
            # deconnexion de tous les postes
            print("on se barre")
            deconnect_staff_from_all_counters(staff)
        # Ajout du membre de l'équipe au comptoir        
        counter.staff = staff
        db.session.commit()

        # mise à jour des boutons
        communikation("counter", event="update buttons")
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
    communikation("counter", event="update buttons")
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
    """ Déconnecte le membre de l'équipe de tous les comptoirs """
    print("Déconnexion en cours...")
    
    # Récupère tous les comptoirs associés à ce membre du personnel
    affected_counters = Counter.query.filter_by(staff=staff).all()
    
    if not affected_counters:
        print("Aucun comptoir à déconnecter pour ce membre du personnel.")
        return
    
    for counter in affected_counters:
        print("counter->", counter)
        counter.staff = None
        communikation("app_counter", event="disconnect_user", data={'counter_id': counter.id, "staff": staff.name})
    
    db.session.commit()
    
    # TODO A MODIFIER.... 
    communikation("counter", event="update buttons")
    
    print(f"Déconnexion réussie de {len(affected_counters)} comptoir(s).")

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
            # avant de supprimer, on vérifie que le comptoir est bien dans la liste
            if counter.id in app.config["AUTO_CALLING"]:
                app.config["AUTO_CALLING"].remove(counter.id)
            else:
                app.logger(f"Le comptoir {counter.id} n'étais pas dans la liste des autocalling")
        app.logger.info(f"counter autocalling : {app.config['AUTO_CALLING']}" )

        # si on relance autocalling, on appelle automatiquement le patient suivant
        # uniquement si le comptoir est inactif
        if auto_calling_value and not counter.is_active:
            is_patient, patient = call_next(counter.id)
            if is_patient:
                counter_become_active(counter.id)
                communikation("app_counter", event="update_auto_calling", data={"counter_id": counter.id, "patient": patient.to_dict()})
                # mise à jour écran ... bizarremment l'audio est dans le call next....
                text = replace_balise_announces(app.config['ANNOUNCE_CALL_TEXT'], patient)
                communikation("update_screen", event="add_calling", data={"id": patient.id, "text": text})


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
    communikation("app_counter", event="change_auto_calling", 
                                    data={"counter_id": counter_id, "autocalling": auto_calling_value})
    if not success:
        return result, status_code
    return "", 204


@counter_bp.route('/app/counter/auto_calling', methods=['POST'])
def app_auto_calling():
    counter_id = request.form.get('counter_id')
    action = request.form.get('action')
    print("autocalling", action)

    if action is None:
        counter = Counter.query.get(counter_id)
        return jsonify({"status": counter.auto_calling}), 200

    auto_calling_value = action == "activate"

    success, result, status_code = update_counter_auto_calling(counter_id, auto_calling_value)

    # notification de changement
    communikation("counter", event="refresh_auto_calling", data={"auto_calling": auto_calling_value})

    print(success, result, status_code)
    if not success:
        return jsonify({"error": result}), status_code
    print("OL ")
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
    print("counter_select_patient", counter_id, patient_id)
    app.call_specific_patient(counter_id, patient_id)
    communikation("update_patient")
    return '', 204


@counter_bp.route('/counter/relaunch_patient_call/<int:counter_id>', methods=['GET'])
def relaunch_patient_call(counter_id):
    patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    audiofile = f'patient_{patient.call_number}.mp3'
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)
    communikation("update_audio", event="audio", data=audio_url)
    return '', 204


@counter_bp.route('/api/counter/put_standing_list/<int:patient_id>', methods=['GET'])
def put_waiting_list(patient_id):
    return handle_patient_from_app(patient_id, action="standing")

@counter_bp.route('/api/counter/delete_patient/<int:patient_id>', methods=['GET'])
def delete_patient_from_app(patient_id):
    return handle_patient_from_app(patient_id, action="delete")

def handle_patient_from_app(patient_id, action):
    patient = Patient.query.get(patient_id)
    print("STANDING", patient)

    if action == "delete":
        status = "done"  # en cas de suppression de la part du comptoir, on marque le patient comme terminé
    elif action == "standing":
        status = "standing"
    
    if patient:
        # on change les infos du patient
        patient.status = status
        patient.counter = None
        db.session.commit()

        # rafraichissement de la page
        communikation("update_screen", event="remove_calling", data={"id": patient_id})

        # rafraichissement des infos
        communikation("update_patient")
        return "", 201
    else:
        return 'Patient not found', 404
