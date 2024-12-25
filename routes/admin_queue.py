from flask import Blueprint, render_template, request, jsonify, current_app
from models import Patient, Activity, Counter, DashboardCard, db
from init_restore import clear_counter_table
from python.engine import add_patient, get_next_call_number
from routes.announce import announce_refresh
from communication import communikation
from bdd import transfer_patients_to_history
from routes.admin_security import require_permission
from flask_login import current_user

admin_queue_bp = Blueprint('admin_queue', __name__)

status_list = ['ongoing', 'standing', 'done', 'calling']

@admin_queue_bp.route('/admin/queue')
@require_permission('queue')
def admin_queue():
    activities = Activity.query.all()
    can_write = any(role.has_permission('queue', 'write') for role in current_user.roles)
    return render_template('admin/queue.html', activities=activities, can_write=can_write)

# affiche le tableau des patients
@admin_queue_bp.route('/admin/queue/table', methods=['POST'])
@require_permission('queue')
def display_queue_table():
    # Récupération des statuts en une liste pour tri ultérieur
    filters = [status for status, value in request.form.items() if value == 'true']
    print("Filters received:", filters)

    # Filtrage des patients en fonction des statuts sélectionnés
    if filters:
        patients = Patient.query.filter(Patient.status.in_(filters)).all()
    else:
        patients = Patient.query.all()

    can_write = any(role.has_permission('queue', 'write') for role in current_user.roles)
    
    return render_template('admin/queue_htmx_table.html', 
                            patients=patients, 
                            activities=Activity.query.all(),
                            status_list=status_list,
                            counters=Counter.query.all(),
                            can_write=can_write)


# affiche la modale pour confirmer la suppression de toute la table patient
@admin_queue_bp.route('/admin/database/confirm_delete_patient_table_without_saving')
@require_permission('queue')
def confirm_delete_patient_table_without_saving():
    return render_template('/admin/queue_modal_confirm_delete.html',
                            saving=False)

# affiche la modale pour confirmer la suppression de toute la table patient
@admin_queue_bp.route('/admin/database/confirm_delete_patient_table_with_saving')
@require_permission('queue')
def confirm_delete_patient_table_with_saving():
    return render_template('/admin/queue_modal_confirm_delete.html',
                            saving=True)

@admin_queue_bp.route('/admin/database/clear_all_patients_with_saving')
@require_permission('queue')
def clear_all_patients_from_db_with_saving():
    success = transfer_patients_to_history()
    if success:
        clear_all_patients_from_db()
    else:
        current_app.logger.error("Failed to transfer patients to history")
        current_app.display_toast(success=False, message="Echec de transfert des patients vers l'historique. La suppression des patients est annulée.")

@admin_queue_bp.route('/admin/database/clear_all_patients')
@require_permission('queue')
def clear_all_patients_from_db(app_context=None):
    print("Suppression de la table Patient")
    # je dois passer le contexte dans le cas d'APscheduler car dans un Thread différent d'où "app_context", 
    # je ne peux pas utiliser simplement current_app. Par contre quand appelé par le bouton supprimé on utilise current_app
    app_context = current_app if not app_context else app_context
    with current_app.app_context():  # Nécessaire pour pouvoir effacer la table via le CRON
        try:
            db.session.query(Patient).delete()
            db.session.commit()
            app_context.logger.info("La table Patient a été vidée")
            communikation("update_patient")
            # rafraichissement de la page Announce
            announce_refresh()
            # mise à jour des dispos des comptoirs
            clear_counter_table()
            communikation("app_counter", event="refresh_after_clear_patient_list")
            return current_app.display_toast(message="La table Patient a été vidée")
        except Exception as e:
            db.session.rollback()
            app_context.logger.error(str(e))
            app_context.display_toast(success = False, message=str(e))
            return "", 200


# mise à jour des informations d'un patient
@admin_queue_bp.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
@require_permission('queue')
def update_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if patient:
            print(request.form)
            if request.form.get('call_number') == '':
                current_app.display_toast(success = False, message="Un numéro d'appel est obligatoire")
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

            current_app.display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            current_app.display_toast(success = False, message="Patient introuvable")
            return ""

    except Exception as e:
            current_app.display_toast(success = False, message=str(e))
            current_app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un patient particulier
@admin_queue_bp.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
@require_permission('queue')
def confirm_delete_patient(patient_id):
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient)


# supprime un patient
@admin_queue_bp.route('/admin/queue/delete_patient/<int:patient_id>', methods=['GET'])
@require_permission('queue')
def delete_patient(patient_id):
    print("id", patient_id)
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            current_app.display_toast(success=False, message="Patient introuvable")
            return 200, ""

        db.session.delete(patient)
        db.session.commit()
        
        communikation("update_patient")
        announce_refresh()
        clear_counter_table()
        current_app.display_toast()
        return "", 200

    except Exception as e:
        current_app.logger(e)
        current_app.display_toast(success=False, message=str(e))
        return "", 500


@admin_queue_bp.route('/admin/queue/create_new_patient_auto', methods=['POST'])
@require_permission('queue')
def create_new_patient_auto():
    if request.form.get('activity_id') == "":
        current_app.display_toast(success=False, message="Veuillez choisir un motif")
        return "", 204
    
    activity = Activity.query.get(request.form.get('activity_id'))
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)

    print("new_patient", activity)
    communikation("update_patient")

    return "", 204


@admin_queue_bp.route('/admin/queue/dashboard')
@require_permission('queue')
def dashboard_queue():
    patients = Patient.query.filter(Patient.status != "done").all()
    dashboardcard = DashboardCard.query.filter_by(name="queue").first()
    return render_template('/admin/dashboard_queue.html', 
                            patients=patients, 
                            dashboardcard=dashboardcard)