import os
import json
import time as tm
from flask import Blueprint, render_template, request, current_app as app
from models import ConfigOption, db
from wtforms import MultipleFileField, SubmitField
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
from communication import communikation

admin_gallery_bp = Blueprint('admin_gallery', __name__)

@admin_gallery_bp.route('/admin/info')
def admin_info():
    return render_template('/admin/gallery.html',
                            galleries = os.listdir(app.config['ANNOUNCE_GALLERY_FOLDERS']))


@admin_gallery_bp.route('/admin/gallery/choose_gallery', methods=['POST'])
def choose_gallery(gallery_name="", checked=""):
    """ 
    Ajout ou suppression de la galerie via le panel d'admin (POST)
    Ou lors de la suppression du dossier via le panel admin (arguments de la fonction)    
    """
    print("choose_gallery")
    print(request.form)
    if request.method == 'POST':
        gallery_name = request.form.get('gallery_name')
        checked = request.form.get('checked')

    # Récupérer l'entrée existante ou créer une nouvelle
    config_option = ConfigOption.query.filter_by(config_key="announce_infos_gallery").first()
    if config_option is None:
        config_option = ConfigOption(config_key="announce_infos_gallery", value_str=json.dumps([]))
        db.session.add(config_option)

    # Charger les galeries existantes à partir de la chaîne JSON
    galleries = json.loads(config_option.value_str)

    print("CHECK", checked)
    message = ""    
    if checked == "true":
        message="Galerie selectionnée"
        if gallery_name not in galleries:
            galleries.append(gallery_name)
    else:
        message="Galerie deselectionnée"
        if gallery_name in galleries:
            galleries.remove(gallery_name)            

    # Enregistrer les galeries mises à jour
    config_option.value_str = json.dumps(galleries)
    db.session.commit()

    app.display_toast(success=True, message=message)

    return "", 200

class UploadForm(FlaskForm):
    photos = MultipleFileField('Upload Images')
    submit = SubmitField('Upload')

def get_images_with_dates(folder):
    try:
        files = os.listdir(folder)
        images = []
        for file in files:
            filepath = os.path.join(folder, file)
            date = tm.strftime('%Y-%m-%d %H:%M:%S', tm.localtime(os.path.getmtime(filepath)))
            images.append({'filename': file, 'date': date})
        return images
    except FileNotFoundError:
        print("File not found")
        return []
    

@admin_gallery_bp.route("/admin/gallery/list", methods=['GET'])
def gallery_list():
    galleries = os.listdir(app.config['ANNOUNCE_GALLERY_FOLDERS'])
    config_option = ConfigOption.query.filter_by(config_key="announce_infos_gallery").first()
    if config_option:
        selected_galleries = json.loads(config_option.value_str)
    else:
        selected_galleries = []
    return render_template('admin/gallery_list_galleries.html', 
                            galleries=galleries,
                            selected_galleries=selected_galleries)


@admin_gallery_bp.route('/admin/gallery/<name>', methods=['GET', 'POST'])
def gallery(name):
    #if request.method == 'POST':
    #    for file in request.files.getlist('photos'):
    #        filename = secure_filename(file.filename)
    #        os.makedirs(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name), exist_ok=True)
    #        file.save(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name, filename))
    #images = get_images_with_dates(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))
    return render_template('/admin/gallery_manage.html', gallery=name)


@admin_gallery_bp.route('/admin/gallery/images_list/<name>', methods=['GET'])
def gallery_images_list(name):
    if not name:
        return "No gallery name provided", 400
    images = get_images_with_dates(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@admin_gallery_bp.route('/admin/gallery/upload/<name>', methods=['POST'])
def upload_gallery(name):
    print(name)
    request.files.getlist('photos')
    for file in request.files.getlist('photos'):
        print("FILES", file)
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name, filename))
    images = get_images_with_dates(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))
    print("images", images)
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@admin_gallery_bp.route('/admin/gallery/delete_image/<gallery>/<image>', methods=['DELETE'])
def delete_image(gallery, image):
    os.remove(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], gallery, image))
    images = get_images_with_dates(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], gallery))
    return render_template('admin/gallery_list_images.html', gallery=gallery, images=images)


@admin_gallery_bp.route('/admin/gallery/delete_gallery/<name>', methods=['DELETE'])
def delete_gallery(name):
    for image in os.listdir(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name)):
        os.remove(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name, image))
    os.rmdir(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))

    # on supprime la selection pour cette galerie si elle est selectionnée
    choose_gallery(gallery_name=name, checked="false")

    communikation("admin", event="refresh_gallery_list")

    return "", 200


@admin_gallery_bp.route('/admin/gallery/create_gallery', methods=['POST'])
def create_gallery():
    name = request.form.get('name')
    if name == "":
        app.display_toast(success=False, message="Le nom de la galerie doit être renseigné")
        return "", 400
    try:
        os.makedirs(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))
    except FileExistsError:
        app.display_toast(success=False, message="La galerie doit avoir un nom unique")
        return "", 400
    galleries = os.listdir(app.config['ANNOUNCE_GALLERY_FOLDERS'])
    communikation("admin", event="display_new_gallery", data=name)
    return render_template('admin/gallery_list_galleries.html', galleries=galleries)


