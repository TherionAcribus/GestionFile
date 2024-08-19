from flask import render_template, request, jsonify,current_app as app
from sqlalchemy import func

from models import db, ConfigOption, Counter, Pharmacist, Patient, Activity

def counter_paper_add():
    return render_template('counter/paper_add.html',
                            add_paper=app.config["ADD_PAPER"])

def action_add_paper(add_paper):
    try:
        print("action_add_paper", add_paper)
        config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
        config_option.value_bool = add_paper
        db.session.commit()
        app.config["ADD_PAPER"] = add_paper
        app.communikation("counter", event="paper")
        app.communikation("app_counter", event="paper")
        return counter_paper_add()
    except Exception as e:
        print(e)

def app_paper_add():
    if request.form.get('action') is None:
        return jsonify({"status": app.config["ADD_PAPER"]}), 200 # 
    else:
        add_paper_action = True if request.form.get('action') == "activate" else False
        try:
            config_option = ConfigOption.query.filter_by(config_key="add_paper").first()
            config_option.value_bool = add_paper_action
            db.session.commit()
            app.config["ADD_PAPER"] = add_paper_action

            app.communikation("counter", event="paper")
            app.communikation("app_counter", event="paper")

        except Exception as e:
            print(e)

def app_update_counter_staff():
    return update_counter_staff()

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


def is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    # emet un signal pour provoquer le réaffichage de la liste des activités
    #socketio.emit("trigger_connect_staff", {})
    return render_template('counter/staff_on_counter.html', staff=counter.staff)


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


def app_is_patient_on_counter(counter_id):
    """ Renvoie les informations du patient actuel au comptoir (pour le client) pour l'App (démarrage)"""
    patient = Patient.query.filter(
        Patient.counter.has(id=counter_id),
        Patient.status.in_(['ongoing', 'calling'])
        ).first()
    print("PATIENT!!!", patient)
    if patient:
        return jsonify(patient.to_dict()), 200
    else:
        return jsonify({"id": None, "counter_id": counter_id}), 200   


def patients_queue_for_counter(counter_id):
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('/counter/patients_queue_for_counter.html', patients=patients, counter_id=counter_id)


def app_auto_calling():
    print("COUNTER AUTOCALLING", request.values)
    counter_id = request.form.get('counter_id')
    print("COUNTER ID", counter_id)
    counter = Counter.query.get(counter_id)

    if request.form.get('action') is None:
        return jsonify({"status": counter.auto_calling}), 200 # 

    auto_calling_action = True if request.form.get('action') == "activate" else False
    print("COUNTER AUTOCALLING", request.values)
    counter = Counter.query.get(counter_id)
    print("counter", counter.auto_calling)
    try:
        counter.auto_calling = auto_calling_action
        db.session.commit()
        # MAJ app.Config
        if auto_calling_action:
            app.config["AUTO_CALLING"].append(counter.id)
        else:
            app.config["AUTO_CALLING"].remove(counter.id)

        app.communikation("counter", event="refresh_auto_calling")

        return jsonify({"status": counter.auto_calling}), 200 # 
    except Exception as e:
        app.logger.error(f'Erreur: {e}')
        return e, 500


def app_remove_counter_staff():
    print("deconnction")
    remove_counter_staff()
    return '', 200


def web_remove_counter_staff():
    return remove_counter_staff()


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


def counter_select_patient(counter_id, patient_id):
    """ Appeler lors du choix d'un patient spécifique au comptoir """
    print("counter_select_patient", counter_id, patient_id)
    app.call_specific_patient(counter_id, patient_id)

    app.communikation("update_patient") 

    return '', 204
