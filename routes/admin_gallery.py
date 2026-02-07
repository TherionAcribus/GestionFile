import os
import json
import time as tm
from pathlib import Path
from flask import Blueprint, render_template, request, current_app as app
from models import ConfigOption, db
from wtforms import MultipleFileField, SubmitField
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
from communication import communikation
from routes.admin_security import require_permission
from path_security import UnsafePathError, safe_path_under, to_abs_base_dir, validate_path_segment

admin_gallery_bp = Blueprint('admin_gallery', __name__)


def _galleries_dir() -> Path:
    return to_abs_base_dir(app.config['ANNOUNCE_GALLERY_FOLDERS'], root_dir=app.root_path)

@admin_gallery_bp.route('/admin/info')
@require_permission('gallery')
def admin_info():
    return render_template('/admin/gallery.html',
                            galleries = os.listdir(app.config['ANNOUNCE_GALLERY_FOLDERS']))


@admin_gallery_bp.route('/admin/gallery/choose_gallery', methods=['POST'])
@require_permission('gallery')
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

    try:
        validate_path_segment(gallery_name, what="gallery name")
    except UnsafePathError:
        return "Invalid gallery name", 400

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
        folder_path = Path(folder)
        files = [p.name for p in folder_path.iterdir() if p.is_file()]
        images = []
        for file in files:
            filepath = folder_path / file
            date = tm.strftime('%Y-%m-%d %H:%M:%S', tm.localtime(filepath.stat().st_mtime))
            images.append({'filename': file, 'date': date})
        return images
    except (FileNotFoundError, OSError):
        print("File not found")
        return []
    

@admin_gallery_bp.route("/admin/gallery/list", methods=['GET'])
@require_permission('gallery')
def gallery_list():
    base_dir = _galleries_dir()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        galleries = [p.name for p in base_dir.iterdir() if p.is_dir()]
    except OSError:
        galleries = []
    config_option = ConfigOption.query.filter_by(config_key="announce_infos_gallery").first()
    if config_option:
        selected_galleries = json.loads(config_option.value_str)
    else:
        selected_galleries = []
    return render_template('admin/gallery_list_galleries.html', 
                            galleries=galleries,
                            selected_galleries=selected_galleries)


@admin_gallery_bp.route('/admin/gallery/<name>', methods=['GET', 'POST'])
@require_permission('gallery')
def gallery(name):
    try:
        validate_path_segment(name, what="gallery name")
    except UnsafePathError:
        return "Invalid gallery name", 400
    #if request.method == 'POST':
    #    for file in request.files.getlist('photos'):
    #        filename = secure_filename(file.filename)
    #        os.makedirs(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name), exist_ok=True)
    #        file.save(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name, filename))
    #images = get_images_with_dates(os.path.join(app.config['ANNOUNCE_GALLERY_FOLDERS'], name))
    return render_template('/admin/gallery_manage.html', gallery=name)


@admin_gallery_bp.route('/admin/gallery/images_list/<name>', methods=['GET'])
@require_permission('gallery')
def gallery_images_list(name):
    if not name:
        return "No gallery name provided", 400
    try:
        validate_path_segment(name, what="gallery name")
        folder_path = safe_path_under(_galleries_dir(), name)
    except UnsafePathError:
        return "Invalid gallery name", 400

    images = get_images_with_dates(folder_path)
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@admin_gallery_bp.route('/admin/gallery/upload/<name>', methods=['POST'])
@require_permission('gallery')
def upload_gallery(name):
    try:
        validate_path_segment(name, what="gallery name")
        gallery_dir = safe_path_under(_galleries_dir(), name)
    except UnsafePathError:
        return "Invalid gallery name", 400

    gallery_dir.mkdir(parents=True, exist_ok=True)

    for file in request.files.getlist('photos'):
        filename = secure_filename(file.filename or "")
        if not filename:
            continue
        try:
            target_path = safe_path_under(gallery_dir, filename)
        except UnsafePathError:
            continue
        file.save(str(target_path))

    images = get_images_with_dates(gallery_dir)
    print("images", images)
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


@admin_gallery_bp.route('/admin/gallery/delete_image/<gallery>/<image>', methods=['DELETE'])
@require_permission('gallery')
def delete_image(gallery, image):
    try:
        validate_path_segment(gallery, what="gallery name")
        validate_path_segment(image, what="image filename")
        target_path = safe_path_under(_galleries_dir(), gallery, image)
        gallery_dir = safe_path_under(_galleries_dir(), gallery)
    except UnsafePathError:
        return "Invalid path", 400

    if target_path.exists() and target_path.is_file():
        target_path.unlink()

    images = get_images_with_dates(gallery_dir)
    return render_template('admin/gallery_list_images.html', gallery=gallery, images=images)


@admin_gallery_bp.route('/admin/gallery/delete_gallery/<name>', methods=['DELETE'])
@require_permission('gallery')
def delete_gallery(name):
    try:
        validate_path_segment(name, what="gallery name")
        gallery_dir = safe_path_under(_galleries_dir(), name)
    except UnsafePathError:
        return "Invalid gallery name", 400

    if not gallery_dir.exists() or not gallery_dir.is_dir():
        return "Gallery not found", 404

    for child in gallery_dir.iterdir():
        if child.is_dir():
            return "Gallery contains subdirectories", 400
        if child.is_file():
            child.unlink()

    gallery_dir.rmdir()

    # on supprime la selection pour cette galerie si elle est selectionnée
    choose_gallery(gallery_name=name, checked="false")

    communikation("admin", event="refresh_gallery_list")

    return "", 200


@admin_gallery_bp.route('/admin/gallery/create_gallery', methods=['POST'])
@require_permission('gallery')
def create_gallery():
    name = request.form.get('name')
    if name == "":
        app.display_toast(success=False, message="Le nom de la galerie doit être renseigné")
        return "", 400
    try:
        validate_path_segment(name, what="gallery name")
        new_dir = safe_path_under(_galleries_dir(), name)
        new_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        app.display_toast(success=False, message="La galerie doit avoir un nom unique")
        return "", 400
    except UnsafePathError:
        app.display_toast(success=False, message="Nom de galerie invalide")
        return "", 400

    base_dir = _galleries_dir()
    try:
        galleries = [p.name for p in base_dir.iterdir() if p.is_dir()]
    except OSError:
        galleries = []
    communikation("admin", event="display_new_gallery", data=name)
    return render_template('admin/gallery_list_galleries.html', galleries=galleries)


