from flask import Blueprint, render_template, request, jsonify, current_app
from sqlalchemy.orm import contains_eager, joinedload
from models import Patient, Activity, Counter, DashboardCard, db
from init_restore import clear_counter_table
from python.engine import add_patient, get_next_call_number
from routes.announce import refresh_announce_screens
from communication import communikation
from services.queue_service import archive_and_purge_all_patients, purge_all_patients
from routes.admin_security import require_permission, require_permission_dashboard
from pagination import parse_page_params, paginate_query
from audit_service import record_audit
from audit_log import ACTION_DELETE, OUTCOME_SUCCESS, OUTCOME_FAILURE
from ui_feedback import display_toast

admin_queue_bp = Blueprint('admin_queue', __name__)

status_list = ['ongoing', 'standing', 'done', 'calling']

# Colonnes de tri autorisées (liste blanche) pour la table des patients :
# clé exposée au client -> colonne SQLAlchemy. Voir pagination.parse_page_params.
QUEUE_SORT_COLUMNS = {
    'call_number': Patient.call_number,
    'timestamp': Patient.timestamp,
    'status': Patient.status,
    'activity': Activity.name,
}

@admin_queue_bp.route('/admin/queue')
@require_permission('queue')
def admin_queue():
    activities = Activity.query.all()
    return render_template('admin/queue.html', activities=activities)

# affiche le tableau des patients
@admin_queue_bp.route('/admin/queue/table', methods=['POST'])
@require_permission('queue')
def display_queue_table():
    # Récupération des statuts cochés. On se restreint aux clés connues
    # (status_list) : un autre champ du formulaire — search, per_page… — ne peut
    # donc pas être confondu avec un filtre de statut.
    filters = [status for status in status_list if request.form.get(status) == 'true']

    # Pagination + tri + recherche (point 5.1). On joint Activity pour permettre
    # le tri et la recherche sur le motif (nom d'activité).
    params = parse_page_params(
        request.form,
        allowed_sort=tuple(QUEUE_SORT_COLUMNS),
        default_sort='timestamp',
    )

    # Le gabarit lit patient.activity et patient.counter pour chaque ligne :
    # on charge ces relations en amont pour éviter un N+1 (une requête par
    # patient). Activity est déjà jointe pour le tri/la recherche → contains_eager
    # réutilise cette jointure ; Counter est chargée via joinedload (scalaire
    # many-to-one, compatible avec la pagination LIMIT).
    query = (
        Patient.query
        .outerjoin(Activity, Patient.activity_id == Activity.id)
        .options(contains_eager(Patient.activity), joinedload(Patient.counter))
    )
    if filters:
        query = query.filter(Patient.status.in_(filters))

    pager = paginate_query(
        query,
        params,
        sort_columns=QUEUE_SORT_COLUMNS,
        search_columns=[Patient.call_number, Patient.status, Activity.name],
    )

    return render_template('admin/queue_htmx_table.html',
                            patients=pager.items,
                            pager=pager,
                            params=params,
                            activities=Activity.query.all(),
                            status_list=status_list,
                            counters=Counter.query.all())


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

def _purge_patients_response(archive=False):
    """Traduit la purge de la file en réponse de vue (toast + statut).

    ``archive=True`` copie d'abord la file dans l'historique — copie et
    suppression sont atomiques dans le service (une seule transaction, plus
    la clé d'idempotence ``patient_source_id`` contre les doublons à la
    relance).
    """
    try:
        if archive:
            archive_and_purge_all_patients()
        else:
            purge_all_patients()
    except Exception as e:
        current_app.logger.error("Échec de la purge de la file : %s", e)
        display_toast(success=False, message="La purge de la file a échoué.")
        return "", 200
    return display_toast(message="La table Patient a été vidée")


@admin_queue_bp.route('/admin/database/clear_all_patients_with_saving', methods=['POST'])
@require_permission('queue')
def clear_all_patients_from_db_with_saving():
    # Point audit : la copie vers l'historique puis la purge étaient validées
    # en deux transactions — une copie réussie suivie d'une suppression en
    # échec produisait des doublons à la relance. Le service valide les deux
    # en une seule transaction.
    return _purge_patients_response(archive=True)

@admin_queue_bp.route('/admin/database/clear_all_patients', methods=['POST'])
@require_permission('queue')
def clear_all_patients_from_db():
    """Vide la file d'attente — voir ``services.queue_service.purge_all_patients``.

    Le métier est extrait (point audit) : la tâche planifiée l'appelle sans
    passer par cette vue décorée — ``@require_permission`` exige une requête
    HTTP (``current_user``), indisponible dans un job APScheduler.
    """
    return _purge_patients_response()


# mise à jour des informations d'un patient
@admin_queue_bp.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
@require_permission('queue')
def update_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if patient:
            if request.form.get('call_number') == '':
                display_toast(success = False, message="Un numéro d'appel est obligatoire")
                return ""
            patient.call_number = request.form.get('call_number', patient.call_number)
            patient.status = request.form.get('status', patient.status)
            activity_id = request.form.get('activity_id', patient.activity)
            patient.activity = Activity.query.get(activity_id)
            counter_id = request.form.get('counter_id', patient.counter)
            patient.counter = Counter.query.get(counter_id)

            db.session.commit()

            clear_counter_table()

            refresh_announce_screens()

            display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            display_toast(success = False, message="Patient introuvable")
            return ""

    except Exception as e:
            display_toast(success=False, message="La mise à jour a échoué.")
            current_app.logger.exception("Echec de la mise a jour d'un patient")
            return jsonify(status="error", message="La mise à jour a échoué."), 500


# affiche la modale pour confirmer la suppression d'un patient particulier
@admin_queue_bp.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
@require_permission('queue')
def confirm_delete_patient(patient_id):
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient)


# supprime un patient
@admin_queue_bp.route('/admin/queue/delete_patient/<int:patient_id>', methods=['DELETE'])
@require_permission('queue')
def delete_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            display_toast(success=False, message="Patient introuvable")
            return 200, ""

        db.session.delete(patient)
        db.session.commit()

        record_audit(ACTION_DELETE, "patient", target_id=patient_id, outcome=OUTCOME_SUCCESS)
        communikation("update_patient")
        refresh_announce_screens()
        clear_counter_table()
        display_toast()
        return "", 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Echec de la suppression d'un patient")
        record_audit(ACTION_DELETE, "patient", target_id=patient_id, outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        return "", 500


@admin_queue_bp.route('/admin/queue/create_new_patient_auto', methods=['POST'])
@require_permission('queue')
def create_new_patient_auto():
    if request.form.get('activity_id') == "":
        display_toast(success=False, message="Veuillez choisir un motif")
        return "", 204
    
    activity = Activity.query.get(request.form.get('activity_id'))
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)

    current_app.logger.debug('new_patient %s', activity)
    communikation("update_patient")

    return "", 204


@admin_queue_bp.route('/admin/queue/dashboard')
@require_permission_dashboard('queue')
def dashboard_queue():
    # Le gabarit dashboard_queue.html affiche patient.activity.name par ligne :
    # joinedload évite un N+1 sur l'activité.
    patients = (
        Patient.query
        .filter(Patient.status != "done")
        .options(joinedload(Patient.activity))
        .all()
    )
    dashboardcard = DashboardCard.query.filter_by(name="queue").first()
    return render_template('/admin/dashboard_queue.html', 
                            patients=patients, 
                            dashboardcard=dashboardcard)