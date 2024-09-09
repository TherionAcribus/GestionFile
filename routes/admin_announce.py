import os
from flask import Blueprint, render_template, request, url_for, redirect, send_from_directory, current_app as app
from models import ConfigOption, db

admin_announce_bp = Blueprint('admin_announce', __name__)

@admin_announce_bp.route('/admin/announce')
def announce_page():
    return render_template('/admin/announce.html', 
                            announce_sound = app.config['ANNOUNCE_SOUND'],
                            announce_alert = app.config['ANNOUNCE_ALERT'],
                            announce_player = app.config['ANNOUNCE_PLAYER'],
                            announce_voice = app.config['ANNOUNCE_VOICE'],
                            anounce_style = app.config['ANNOUNCE_STYLE'],
                            announce_call_text=app.config['ANNOUNCE_CALL_TEXT'],
                            announce_call_text_size=app.config['ANNOUNCE_CALL_TEXT_SIZE'],
                            announce_call_text_transition=app.config['ANNOUNCE_CALL_TEXT_TRANSITION'],
                            announce_ongoing_display=app.config['ANNOUNCE_ONGOING_DISPLAY'],
                            announce_ongoing_text=app.config['ANNOUNCE_ONGOING_TEXT'],
                            announce_title=app.config['ANNOUNCE_TITLE'],
                            announce_title_size=app.config["ANNOUNCE_TITLE_SIZE"],
                            announce_subtitle=app.config['ANNOUNCE_SUBTITLE'],
                            announce_text_up_patients=app.config['ANNOUNCE_TEXT_UP_PATIENTS'],
                            announce_text_up_patients_display=app.config['ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY'],
                            announce_text_down_patients=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS'],
                            announce_text_down_patients_display=app.config['ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY'],
                            announce_infos_display=app.config['ANNOUNCE_INFOS_DISPLAY'],
                            announce_infos_display_time=app.config['ANNOUNCE_INFOS_DISPLAY_TIME'],
                            announce_infos_transition=app.config['ANNOUNCE_INFOS_TRANSITION']   ,
                            announce_infos_height=app.config['ANNOUNCE_INFOS_HEIGHT'],
                            announce_infos_width=app.config['ANNOUNCE_INFOS_WIDTH'],
                            announce_infos_mix_folders=app.config['ANNOUNCE_INFOS_MIX_FOLDERS'],
                            )


@admin_announce_bp.route('/admin/announce/gallery_audio')
def gallery_audio():    
    return render_template('/admin/announce_audio_gallery.html',
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],)


@admin_announce_bp.route("/admin/announce/audio/gallery_list", methods=["GET", "DELETE"])
def gallery_audio_list():
    # il faut garder la methode DELETE car appeler par delete/<sound_filename> pour réafficher la galerie
    # Lister tous les fichiers wav dans le répertoire SOUND_FOLDER
    sounds = [f for f in os.listdir("static/audio/signals") if f.endswith('.wav') or f.endswith('.mp3')]
    print("sounds", sounds)
    return render_template("admin/announce_audio_gallery_list.html",
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],
                            sounds=sounds)

@admin_announce_bp.route('/sounds/<filename>')
def serve_sound(filename):
    return send_from_directory("static/audio/signals", filename)

@admin_announce_bp.route("/admin/announce/audio/delete/<sound_filename>", methods=["DELETE"])
def delete_sound(sound_filename):
    sound_path = os.path.join("static/audio/signals", sound_filename)    
    try:
        # on empeche de supprimer le son en cours d'utilisation
        if sound_filename == app.config["ANNOUNCE_ALERT_FILENAME"]:
            app.display_toast(success=False, message="Impossible de supprimer le son courant. Selectionner un autre son et valider avant de supprimer celui-ci.")
            return "", 204
        # Vérifier si le fichier existe avant de le supprimer
        if os.path.exists(sound_path):
            os.remove(sound_path)
            app.logger.info(f"Son supprimé : {sound_filename}")
            return redirect (url_for('gallery_audio_list'))
        else:
            app.logger.error(f"Fichier non trouvé : {sound_filename}")
            app.display_toast(success=False, message="Fichier non trouvé")
            return "Fichier non trouvé", 404
    except Exception as e:
        app.logger.error(f"Erreur lors de la suppression du fichier : {str(e)}")
        return "Erreur lors de la suppression du fichier", 500

@admin_announce_bp.route('/admin/announce/audio/current_signal')
def current_signal():
    return render_template('/admin/announce_audio_current_signal.html',
                            announce_alert_filename = app.config['ANNOUNCE_ALERT_FILENAME'],)


@admin_announce_bp.route('/admin/announce/audio/save_selected_sound', methods=['POST'])
def select_signal():
    print(request.values)
    filename = request.form.get('selected_sound')
    if filename:
        app.config['ANNOUNCE_ALERT_FILENAME'] = filename
        config = ConfigOption.query.filter_by(config_key='announce_alert_filename').first()
        print(config)
        config.value_str = filename
        db.session.commit()

        app.communikation("admin", event="refresh_sound")

    return "", 204


def allowed_audio_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_AUDIO_EXTENSIONS"]


@admin_announce_bp.route('/admin/announce/audio/upload', methods=['POST'])
def upload_signal_file():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file and allowed_audio_file(file.filename):
        filename = file.filename
        file.save(os.path.join("static/audio/signals", filename))
    
    return redirect(url_for('gallery_audio_list'))