from flask import Blueprint, jsonify
from models import db, Patient, Activity, Language
from auth_utils import require_app_token_or_login

pyside_bp = Blueprint('pyside', __name__)


def create_patients_list_for_pyside():
    """Snapshot de la file (patients 'standing') pour l'App Comptoir.

    Fonction MÉTIER pure, sans garde d'authentification : elle est appelée par
    ``communication.communikation("update_patient")`` depuis n'importe quel
    contexte — requête borne anonyme, scheduler sans requête, action admin ou
    App. Elle a longtemps porté directement ``@require_app_token_or_login`` :
    appelée depuis une inscription borne (ni jeton, ni session), le décorateur
    renvoyait ``(jsonify(...), 401)`` à la place de la liste ; l'émission vers
    ``/socket_app_counter`` échouait alors en silence (payload non
    sérialisable) tandis que celle vers ``/socket_update_patient`` partait —
    l'Admin voyait le patient, l'App Comptoir jamais.
    """
    # Cette liste est reconstruite à chaque mutation de la file (design de
    # convergence : on rediffuse un snapshot complet + révision). On sélectionne
    # donc directement les seules colonnes utiles via une projection + JOIN :
    # une seule requête, aucune hydratation d'objets ORM Patient/Activity/
    # Language. Le JOIN sur Language est externe car language_id est nullable
    # (un patient sans langue ne doit pas disparaître de la file).
    rows = (
        db.session.query(
            Patient.id,
            Patient.call_number,
            Patient.activity_id,
            Activity.name,
            Activity.staff_id,
            Language.code,
        )
        .join(Activity, Patient.activity_id == Activity.id)
        .outerjoin(Language, Patient.language_id == Language.id)
        .filter(Patient.status == "standing")
        # Ordre FIFO explicite et déterministe : timestamp, puis id comme
        # départage stable quand deux patients partagent le même horodatage.
        .order_by(Patient.timestamp, Patient.id)
        .all()
    )
    patients_list = [{"id": r.id,
                        "call_number": r.call_number,
                        "activity_id": r.activity_id,
                        "activity": r.name,
                        "activity_is_staff": r.staff_id,
                        "language_code": r.code
                    } for r in rows]
    return patients_list


@pyside_bp.route('/api/patients_list_for_pyside', methods=['GET'])
@require_app_token_or_login
def patients_list_for_pyside_route():
    """Exposition HTTP du snapshot, réservée aux clients authentifiés."""
    return jsonify(create_patients_list_for_pyside())
