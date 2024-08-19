import os
import json
from flask import render_template, url_for, current_app as app
from datetime import datetime, date
from models import Patient, ConfigOption

from utils import replace_balise_announces, replace_balise_phone

def display():
    app.logger.debug("start display")
    # TODO verifier qu'existe
    return render_template('/announce/announce.html', 
                            #current_patients=current_patients,
                            announce_infos_display= app.config['ANNOUNCE_INFOS_DISPLAY'],
                            announce_title=app.config['ANNOUNCE_TITLE'] ,
                            announce_subtitle=app.config['ANNOUNCE_SUBTITLE'],
                            announce_text_up_patients=app.config['ANNOUNCE_TEXT_UP_PATIENTS'],
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_down_patients=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS'],
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            call_patients = patient_list_for_init_display(),
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_title_size=app.config['ANNOUNCE_TITLE_SIZE'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],)

def patient_list_for_init_display():
    """ Création de la liste de patients pour initialiser l'écran d'annonce"""
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    announce_call_text = ConfigOption.query.filter_by(config_key="announce_call_text").first().value_str
    call_patients = []
    for patient in patients:
        print("PATATOR", patient)
        call_patient = {
            'id': patient.id,
            'text': replace_balise_announces(announce_call_text, patient)
        }
        call_patients.append(call_patient)
    return call_patients


def patients_ongoing():
    announce_ongoing_text = app.config['ANNOUNCE_ONGOING_TEXT']
    patients = Patient.query.filter_by(status='ongoing').order_by(Patient.counter_id).all()
    ongoing_patients = []
    for patient in patients:
        print("PATATE", patient)
        ongoing_patients.append(replace_balise_announces(announce_ongoing_text, patient))
    return render_template('announce/patients_ongoing.html', ongoing_patients=ongoing_patients)


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
            images.extend([url_for('static', filename=f"galleries/{gallery}/{image}") for image in os.listdir(image_dir) if image.endswith((".png", ".jpg", ".jpeg"))])
        except FileNotFoundError:
            app.logger.error(f"Gallery {gallery} not found")


    # Mélange des images si l'option est active
    if app.config.get("ANNOUNCE_INFOS_MIX_FOLDERS", False):
        print(images)
        images.sort(key=lambda x: os.path.basename(x))
        print(images)
    
    return render_template('announce/gallery.html', images=images,
                            time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION'],
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],)


def announce_refresh():
    """ Permet de rafraichir la page des annonces pour appliquer les changements """
    app.communikation("update_screen", event="refresh")
    app.communication("update_announce")
    return '', 204