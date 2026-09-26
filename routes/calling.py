"""Routes d'appel patient et pages de comptoir.

Extrait d'``app.py`` (point 9.5d). Ce sont des vues **fines** : toute la logique
métier vit dans ``services/calling_service.py`` (point 9.4). Elles ne font que
lire la requête, déléguer, et traduire le retour du service en réponse HTTP.
"""

from flask import Blueprint, current_app as app, jsonify, render_template, session

from auth_utils import require_app_token_or_login
from idempotency import idempotent
from models import Activity, Counter, Patient
from services import calling_service

calling_bp = Blueprint('calling', __name__)


def wrong_counter(counter_id):
    """Page affichée quand le comptoir demandé n'existe pas."""
    return render_template('counter/wrong_counter.html',
                    counters=Counter.query.all(),
                    counter_id=counter_id)

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @calling_bp.route('/patient_right_page_default')
def patient_right_page_default():
    app.logger.debug("default")
    return render_template('htmx/patient_right_page_default.html')



@calling_bp.route('/call_specific_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def call_specific_patient(counter_id, patient_id):
    ok, payload, status_code = calling_service.call_specific(counter_id, patient_id)
    return jsonify(payload), status_code



@calling_bp.route('/validate_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def validate_patient(counter_id, patient_id):
    # Valide l'arrivée du patient appelé à CE comptoir (calling -> ongoing).
    # La transition est vérifiée par le service : état de départ autorisé et
    # rattachement au comptoir — plus de 'done' ressuscité ni de validation
    # depuis le mauvais comptoir.
    app.logger.debug('validation %s', patient_id)
    ok, payload, status_code = calling_service.arrive_at_counter(counter_id, patient_id)
    return jsonify(payload), status_code



@calling_bp.route('/counter/<int:counter_id>')
def counter(counter_id):

    app.logger.debug('counter_number %s', counter_id)
    counter = Counter.query.get(counter_id)
    activities = Activity.query.all()
    # si l'id du comptoir n'existe pas -> page avec liste des comptoirs

    if not counter:
        return wrong_counter(counter_id)
    return render_template('counter/counter.html', 
                            counter=counter,
                            activities=activities)



@calling_bp.route('/current_patient_for_counter/<int:counter_id>')
def current_patient_for_counter(counter_id):
    """ Affiche le patient en cours de traitement pour un comptoir """
    app.logger.debug('counter_number ?? %s', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id,
        Patient.status != 'done'
    ).first()
    app.logger.debug('CURRENT %s', patient)
    return render_template('counter/current_patient_for_counter.html', patient=patient)



@calling_bp.route('/counter/buttons_for_counter/<int:counter_id>')
def current_patient_for_counter_test(counter_id):
    """ Affiche le patient en cours de traitement pour un comptoir """
    app.logger.debug('counter_number %s', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id, 
        Patient.status != "done"
    ).first()
    if not patient:
        patient_id = None
        patient_status = None
    else :
        patient_id = patient.id
        patient_status = patient.status
    return render_template('counter/buttons_for_counter.html', 
                            patient=patient, 
                            patient_id=patient_id, 
                            counter_id=counter_id, 
                            status = patient_status,
                            current_staff=Counter.query.get(counter_id).staff  # TODO Utiliser une classe pour stocker ces infos
                            )



@calling_bp.route('/counter/switch_auto_calling/<int:counter_id>')
def switch_auto_calling(counter_id):
    counter = Counter.query.get(counter_id)
    return render_template('counter/switch_auto_calling.html',
                            counter=counter,
                            auto_calling=counter.auto_calling)





# A SUPPRIMER, NE FONCTIONNE PLUS AVEC HTTPS
@calling_bp.route('/validate_and_call_next/<int:counter_id>', methods=['POST'])
@require_app_token_or_login
@idempotent
def validate_and_call_next(counter_id):
    ok, resultat = calling_service.validate_and_call_next(counter_id)
    if ok:
        return jsonify(resultat.to_dict()), 200
    # pas de patient suivant : le service a repasse le comptoir inactif
    return '', 204



@calling_bp.route('/pause_patient/<int:counter_id>/<int:patient_id>', methods=['POST'])
@require_app_token_or_login
def pause_patient(counter_id, patient_id):
    return jsonify(calling_service.pause(counter_id, patient_id)), 200



# A PRIORI NE SERT PLUS A RIEN
@calling_bp.route('/current_patients')
def current_patients():
    # Supposons que vous vouliez afficher les patients dont le statut est "au comptoir"
    patients = Patient.query.filter_by(status='ongoing').all()
    app.logger.debug("%s", patients)
    return render_template('htmx/update_patients.html', patients=patients)
