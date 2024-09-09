from flask import Blueprint, current_app as app
from models import Patient

pyside_bp = Blueprint('pyside', __name__)

@pyside_bp.route('/api/patients_list_for_pyside', methods=['GET'])
def create_patients_list_for_pyside():
    patients = Patient.query.filter_by(status="standing").all()
    patients_list = [{"id": patient.id, "call_number": patient.call_number, "activity_id": patient.activity_id, "activity": patient.activity.name} for patient in patients]
    return patients_list