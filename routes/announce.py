import os
import json
import random
from flask import Blueprint, render_template, url_for, current_app as app, jsonify, request, redirect
from models import Patient, ConfigOption, get_queue_revision
from utils import replace_balise_announces, replace_balise_welcome
from communication import communikation
from python.engine import get_global_patient_queue
from image_storage import ALLOWED_IMAGE_EXTENSIONS
from auth_utils import is_authenticated_request, wants_json_response
from routes.admin_security import require_permission_api

announce_bp = Blueprint('announce', __name__)

# Endpoints de ce blueprint qui restent publics meme quand SECURITY_LOGIN_SCREEN
# est actif. Volontairement vide : tout ce que la page /display consomme (etat
# des appels, fragments HTMX, galerie) releve de la meme session que la page
# elle-meme. N'ajouter un endpoint qu'apres avoir verifie qu'il n'expose ni
# donnee patient ni action sensible.
_ANNOUNCE_PUBLIC_ENDPOINTS = frozenset()


@announce_bp.before_request
def _require_screen_access():
    """Garde commune du perimetre « ecran ».

    Avant, SECURITY_LOGIN_SCREEN ne protegeait que /display (branche du
    before_request global d'app.py) : les routes /announce/* qu'il consomme
    restaient publiques et exposaient appels, comptoirs et prochains patients
    meme securite activee. Quand le drapeau est actif, TOUTES les routes de ce
    blueprint — /display compris — exigent desormais une preuve d'identite :
    session authentifiee ou jeton applicatif valide (X-App-Token), comme le
    namespace /socket_update_screen. Les exceptions eventuelles sont declarees
    explicitement dans _ANNOUNCE_PUBLIC_ENDPOINTS.

    Forme du refus : 401 JSON pour un appel programmatique (HTMX/fetch),
    redirection vers la connexion pour une navigation navigateur.
    """
    if request.endpoint in _ANNOUNCE_PUBLIC_ENDPOINTS:
        return
    if not app.config.get("SECURITY_LOGIN_SCREEN", False):
        return
    if is_authenticated_request():
        return
    if wants_json_response(request):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for('admin_security.login', next=request.url))


@announce_bp.route('/display')
def display():
    app.logger.debug("start display")
    # TODO verifier qu'existe
    return render_template('/announce/announce.html',
                            #current_patients=current_patients,
                            announce_infos_display= app.config['ANNOUNCE_INFOS_DISPLAY'],
                            # Textes « welcome » : les balises {P} {D} {H} sont
                            # résolues au rendu (le titre par défaut contient {P}).
                            announce_title=replace_balise_welcome(app.config['ANNOUNCE_TITLE']),
                            announce_subtitle=replace_balise_welcome(app.config['ANNOUNCE_SUBTITLE']),
                            announce_text_up_patients=replace_balise_welcome(app.config['ANNOUNCE_TEXT_UP_PATIENTS']),
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_up_patients_size=app.config['ANNOUNCE_TEXT_UP_PATIENTS_SIZE'],
                            announce_text_down_patients=replace_balise_welcome(app.config['ANNOUNCE_TEXT_DOWN_PATIENTS']),
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            announce_text_down_patients_size=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_SIZE'],
                            call_patients = patient_list_for_init_display(),
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_title_size=app.config['ANNOUNCE_TITLE_SIZE'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],
                            announce_next_patients_display=app.config.get('ANNOUNCE_NEXT_PATIENTS_DISPLAY', False),)


def _calling_patients_list():
    """ Liste des appels en cours telle que l'écran les affiche (bannières). """
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    announce_call_text = ConfigOption.query.filter_by(config_key="announce_call_text").first().value_str
    return [
        {
            'id': patient.id,
            'counter_id': patient.counter_id,
            'text': replace_balise_announces(announce_call_text, patient),
        }
        for patient in patients
    ]


def patient_list_for_init_display():
    """ Création de la liste de patients pour initialiser l'écran d'annonce"""
    return _calling_patients_list()


@announce_bp.route('/announce/state')
def announce_state():
    """ État autoritatif des bannières d'appel + révision de la file.

    Les évènements add_calling/remove_calling sont incrémentaux : un message
    perdu sans coupure franche laissait une bannière fantôme ou manquante
    jusqu'à l'évènement suivant (l'écran ne rechargait la page qu'à la
    reconnexion). Ce snapshot — même rôle que /api/counter/<id>/state pour
    l'App comptoir — permet au client de réconcilier son affichage à la
    demande, sans rechargement complet. """
    return jsonify({
        "revision": get_queue_revision(),
        "calling": _calling_patients_list(),
    })


@announce_bp.route('/announce/patients_ongoing')
def patients_ongoing():
    announce_ongoing_text = app.config['ANNOUNCE_ONGOING_TEXT']
    patients = Patient.query.filter_by(status='ongoing').order_by(Patient.counter_id).all()
    ongoing_patients = []
    for patient in patients:
        ongoing_patients.append(replace_balise_announces(announce_ongoing_text, patient))
        app.logger.debug('ONGOINT %s', ongoing_patients)
        app.logger.debug("%s", patient)
    return render_template('announce/patients_ongoing.html', ongoing_patients=ongoing_patients)

@announce_bp.route('/announce/patients_next')
def patients_next():
    # Texte hors contexte patient : {P}/{D}/{H} résolus, balises patient vides.
    announce_next_patients_text = replace_balise_welcome(
        app.config.get('ANNOUNCE_NEXT_PATIENTS_TEXT', "Prochains patients :"))
    announce_next_patients_alignment = app.config.get('ANNOUNCE_NEXT_PATIENTS_ALIGNMENT', 'center')
    
    # Use the global queue algorithm instead of simple timestamp sort
    patients = get_global_patient_queue()
    
    next_patients = [p.call_number for p in patients]
    return render_template('announce/patients_next.html', 
                           announce_next_patients_text=announce_next_patients_text,
                           announce_next_patients_alignment=announce_next_patients_alignment,
                           next_patients=next_patients)

@announce_bp.route('/announce/init_gallery')
def announce_init_gallery():
    """ Création de la liste des images pour la galerie"""
    app.logger.debug("Init gallery")
    
    # Récupérer la liste des galeries sélectionnées, si rien on envoie une liste vide
    config_option = ConfigOption.query.filter_by(config_key="announce_infos_gallery").first()
    if config_option:
        announce_infos_galleries = json.loads(config_option.value_str)
    else:
        announce_infos_galleries = []

    app.logger.debug("announce_infos_galleries : " + str(announce_infos_galleries))
    
    images = []
    for gallery in announce_infos_galleries:
        try:
            image_dir = os.path.join(app.static_folder, "galleries", gallery)
            images.extend([
                url_for('static', filename=f"galleries/{gallery}/{image}")
                for image in os.listdir(image_dir)
                if "." in image
                and image.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
            ])
        except FileNotFoundError:
            app.logger.error(f"Gallery {gallery} not found")


    # Mélange des images si l'option est active. L'ancien code faisait un
    # ``sort`` (tri alphabétique) au lieu d'un vrai mélange — le nom de
    # l'option et son commentaire parlaient bien de « mélange ».
    if app.config.get("ANNOUNCE_INFOS_MIX_FOLDERS", False):
        random.shuffle(images)
    
    return render_template('announce/gallery.html', images=images,
                            time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION'],
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],)

def refresh_announce_screens():
    """Envoie l'ordre de rechargement aux ecrans d'annonce.

    Separe de la route : admin_queue l'appelle directement apres ses mutations,
    sans repasser par HTTP ni par la garde de permission posee sur l'endpoint.
    """
    communikation("update_screen", event="refresh")
    app.logger.debug("Refresh DISPLAY!!")


@announce_bp.route('/announce/refresh', methods=['POST'])
@require_permission_api('announce')
def announce_refresh():
    """Relance les ecrans d'annonce — action d'administration.

    Avant : GET publique, n'importe qui pouvait forcer le rechargement de tous
    les ecrans (et un simple lien la declenchait — CSRF/prechargement). Elle est
    desormais en POST et reservee aux utilisateurs porteurs de la permission
    'announce', quelle que soit la valeur de SECURITY_LOGIN_SCREEN.
    """
    refresh_announce_screens()
    return '', 204