from flask import Blueprint, render_template, request, jsonify, current_app as app
from models import Patient, Activity, Counter, db
from init_restore import clear_counter_table
from python.engine import add_patient, get_next_call_number
from routes.announce import announce_refresh

admin_queue_bp = Blueprint('admin_queue', __name__)

status_list = ['ongoing', 'standing', 'done', 'calling']

@admin_queue_bp.route('/admin/queue')
def admin_queue():
    activities = Activity.query.all()
    return render_template('admin/queue.html', activities=activities)

# affiche le tableau des patients
@admin_queue_bp.route('/admin/queue/table', methods=['POST'])
def display_queue_table():
    # Récupération des statuts en une liste pour tri ultérieur
    filters = [status for status, value in request.form.items() if value == 'true']
    print("Filters received:", filters)

    # Filtrage des patients en fonction des statuts sélectionnés
    if filters:
        patients = Patient.query.filter(Patient.status.in_(filters)).all()
    else:
        patients = Patient.query.all()

    return render_template('admin/queue_htmx_table.html', 
                            patients=patients, 
                            activities=Activity.query.all(), 
                            status_list=status_list,
                            counters=Counter.query.all())


# affiche la modale pour confirmer la suppression de toute la table patient
@admin_queue_bp.route('/admin/database/confirm_delete_patient_table')
def confirm_delete_patient_table():
    return render_template('/admin/queue_modal_confirm_delete.html')

@admin_queue_bp.route('/admin/database/clear_all_patients')
def clear_all_patients_from_db():
    print("Suppression de la table Patient")
    with app.app_context():  # Nécessaire pour pouvoir effacer la table via le CRON
        try:
            db.session.query(Patient).delete()
            db.session.commit()
            app.logger.info("La table Patient a été vidée")
            app.communikation("update_patient")
            announce_refresh()
            clear_counter_table()
            return app.display_toast(message="La table Patient a été vidée")
        except Exception as e:
            db.session.rollback()
            app.logger.error(str(e))
            app.display_toast(success = False, message=str(e))
            return "", 200


# mise à jour des informations d'un patient
@admin_queue_bp.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
def update_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if patient:
            print(request.form)
            if request.form.get('call_number') == '':
                app.display_toast(success = False, message="Un numéro d'appel est obligatoire")
                return ""
            patient.call_number = request.form.get('call_number', patient.call_number)
            patient.status = request.form.get('status', patient.status)
            activity_id = request.form.get('activity_id', patient.activity)
            patient.activity = Activity.query.get(activity_id)
            counter_id = request.form.get('counter_id', patient.counter)
            patient.counter = Counter.query.get(counter_id)

            db.session.commit()

            clear_counter_table()

            announce_refresh()

            app.display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            app.display_toast(success = False, message="Patient introuvable")
            return ""

    except Exception as e:
            app.display_toast(success = False, message=str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un patient particulier
@admin_queue_bp.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
def confirm_delete_patient(patient_id):
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient)


# supprime un patient
@admin_queue_bp.route('/admin/queue/delete_patient/<int:patient_id>', methods=['GET'])
def delete_patient(patient_id):
    print("id", patient_id)
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            app.display_toast(success=False, message="Patient introuvable")
            return 200, ""

        db.session.delete(patient)
        db.session.commit()
        
        app.communikation("update_patient")
        announce_refresh()
        clear_counter_table()
        app.display_toast()
        return "", 200

    except Exception as e:
        app.logger(e)
        app.display_toast(success=False, message=str(e))
        return "", 500


@admin_queue_bp.route('/admin/queue/create_new_patient_auto', methods=['POST'])
def create_new_patient_auto():
    if request.form.get('activity_id') == "":
        app.display_toast(success=False, message="Veuillez choisir un motif")
        return "", 204
    
    activity = Activity.query.get(request.form.get('activity_id'))
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)

    print("new_patient", activity)
    app.communikation("update_patient")

    return "", 204