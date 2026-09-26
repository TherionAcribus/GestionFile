import json
from datetime import datetime

from flask import Blueprint, render_template, request, current_app
from sqlalchemy import func
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
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, OUTCOME_SUCCESS, OUTCOME_FAILURE,
)
from config import time_tz
from queue_explain import (
    EDITABLE_STATUSES, STATUS_BADGES, STATUS_LABELS, describe_minutes,
    minutes_between, validate_edit,
)
from ui_feedback import display_toast

admin_queue_bp = Blueprint('admin_queue', __name__)

# Statuts filtrables (cases de la barre d'outils), dans l'ordre du parcours.
status_list = ['pending', 'standing', 'calling', 'ongoing', 'done', 'cancelled']

# Toute modification de la file recharge la liste (hx-trigger
# « refresh_queue_patient from:body ») sans attendre le WebSocket.
_QUEUE_CHANGED = {"HX-Trigger": json.dumps({"refresh_queue_patient": True})}

# Colonnes de tri autorisées (liste blanche) pour la table des patients :
# clé exposée au client -> colonne SQLAlchemy. Voir pagination.parse_page_params.
QUEUE_SORT_COLUMNS = {
    'call_number': Patient.call_number,
    'timestamp': Patient.timestamp,
    'status': Patient.status,
    'activity': Activity.name,
}


def _now():
    return datetime.now(time_tz)


@admin_queue_bp.route('/admin/queue')
@require_permission('queue')
def admin_queue():
    activities = Activity.query.order_by(Activity.is_staff, Activity.letter, Activity.name).all()
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

    # Compteurs par statut (toute la file, indépendamment des filtres) et
    # attente la plus longue parmi les patients encore en attente.
    counts = dict(db.session.query(Patient.status, func.count(Patient.id))
                  .group_by(Patient.status).all())
    oldest = (Patient.query.filter_by(status='standing')
              .order_by(Patient.timestamp).first())
    now = _now()

    return render_template('admin/queue_htmx_table.html',
                            patients=pager.items,
                            pager=pager,
                            params=params,
                            activities=Activity.query.order_by(Activity.letter, Activity.name).all(),
                            status_list=list(EDITABLE_STATUSES),
                            counters=Counter.query.order_by(Counter.sort_order).all(),
                            counts=counts,
                            longest_wait=describe_minutes(minutes_between(oldest.timestamp, now)) if oldest else None,
                            # Horodatages enregistrés en heure locale naïve.
                            now=now.replace(tzinfo=None),
                            status_labels=STATUS_LABELS,
                            status_badges=STATUS_BADGES)


# affiche la modale pour confirmer la suppression de toute la table patient
@admin_queue_bp.route('/admin/database/confirm_delete_patient_table_without_saving')
@require_permission('queue')
def confirm_delete_patient_table_without_saving():
    return render_template('/admin/queue_modal_confirm_delete.html',
                            saving=False, total=Patient.query.count())

# affiche la modale pour confirmer la suppression de toute la table patient
@admin_queue_bp.route('/admin/database/confirm_delete_patient_table_with_saving')
@require_permission('queue')
def confirm_delete_patient_table_with_saving():
    return render_template('/admin/queue_modal_confirm_delete.html',
                            saving=True, total=Patient.query.count())

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
    body, status = display_toast(message="La file a été vidée")
    return body, status, _QUEUE_CHANGED


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


def _after_queue_change():
    """Propagation commune après une correction manuelle de la file."""
    clear_counter_table()
    communikation("update_patient")
    refresh_announce_screens()


# correction manuelle d'un patient
@admin_queue_bp.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
@require_permission('queue')
def update_patient(patient_id):
    patient = db.session.get(Patient, patient_id)
    if patient is None:
        return display_toast(success=False, message="Patient introuvable")

    call_number = (request.form.get('call_number') or '').strip()
    status = request.form.get('status', patient.status)
    counter_raw = request.form.get('counter_id') or ''
    counter = db.session.get(Counter, int(counter_raw)) if counter_raw.isdigit() else None
    activity_raw = request.form.get('activity_id') or ''
    activity = db.session.get(Activity, int(activity_raw)) if activity_raw.isdigit() else None

    # Erreurs = 204 : rien n'est remplacé, la saisie reste dans le formulaire.
    if not call_number or len(call_number) > 10:
        return display_toast(success=False, message="Numéro d'appel obligatoire (10 caractères maximum).")
    if activity is None:
        return display_toast(success=False, message="Motif inconnu.")
    erreur = validate_edit(status, counter.id if counter else None)
    if erreur:
        return display_toast(success=False, message=erreur)

    try:
        patient.call_number = call_number
        # Clôture cohérente : un passage à 'done' sans fin horodatée ne
        # participait à aucune statistique de durée ; une réouverture efface
        # la fin pour ne pas fausser les mesures si le patient repart.
        if status == 'done' and patient.timestamp_end is None:
            patient.timestamp_end = _now()
        elif status != 'done' and patient.timestamp_end is not None:
            patient.timestamp_end = None
        patient.status = status
        patient.activity = activity
        # Un patient en attente n'occupe aucun comptoir.
        patient.counter = counter if status != 'standing' else None
        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "patient", target_id=patient_id, outcome=OUTCOME_FAILURE)
        current_app.logger.exception("Echec de la mise a jour d'un patient")
        return display_toast(success=False, message="La mise à jour a échoué.")

    record_audit(ACTION_UPDATE, "patient", target_id=patient_id, outcome=OUTCOME_SUCCESS,
                 details=f"call_number={call_number} status={status}")
    _after_queue_change()
    display_toast(success=True, message=f"Patient {call_number} corrigé")
    return "", 200, _QUEUE_CHANGED


# affiche la modale pour confirmer la suppression d'un patient particulier
@admin_queue_bp.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
@require_permission('queue')
def confirm_delete_patient(patient_id):
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient,
                           status_labels=STATUS_LABELS)


# supprime un patient
@admin_queue_bp.route('/admin/queue/delete_patient/<int:patient_id>', methods=['DELETE'])
@require_permission('queue')
def delete_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            # Auparavant `return 200, ""` : tuple inversé, erreur 500.
            return display_toast(success=False, message="Patient introuvable")

        call_number = patient.call_number
        db.session.delete(patient)
        db.session.commit()

        record_audit(ACTION_DELETE, "patient", target_id=patient_id, outcome=OUTCOME_SUCCESS)
        _after_queue_change()
        display_toast(success=True, message=f"Patient {call_number} retiré de la file")
        return "", 200, _QUEUE_CHANGED

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Echec de la suppression d'un patient")
        record_audit(ACTION_DELETE, "patient", target_id=patient_id, outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        return "", 500


@admin_queue_bp.route('/admin/queue/create_new_patient_auto', methods=['POST'])
@require_permission('queue')
def create_new_patient_auto():
    activity_raw = request.form.get('activity_id') or ''
    activity = db.session.get(Activity, int(activity_raw)) if activity_raw.isdigit() else None
    if activity is None:
        return display_toast(success=False, message="Veuillez choisir un motif")

    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    record_audit(ACTION_CREATE, "patient", target_id=new_patient.id, outcome=OUTCOME_SUCCESS,
                 details=f"call_number={call_number} (ajout manuel)")

    communikation("update_patient")
    display_toast(success=True, message=f"Patient {call_number} ajouté à la file ({activity.name})")
    return "", 204, _QUEUE_CHANGED


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