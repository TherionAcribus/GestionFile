"""Page « Galerie média » : dossiers d'images du diaporama de l'écran d'annonce.

Les règles (noms, ordre des images, phrase d'état) vivent dans le noyau pur
``gallery_library`` ; ce module ne fait que le lien avec le disque, la
configuration et les gabarits.

Chaque modification :
- répond avec le fragment à jour **et** l'en-tête ``HX-Trigger: galleryChanged``
  (la liste des galeries et l'état du diaporama se rechargent sans dépendre du
  WebSocket) ;
- prévient les autres administrateurs (``refresh_gallery_list``) ;
- met à jour le diaporama des écrans d'annonce (``refresh_gallery``) quand la
  galerie touchée est affichée — plus besoin de relancer l'écran à la main.
"""

import json
from pathlib import Path

from flask import Blueprint, current_app as app, make_response, render_template, request

from audit_log import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, OUTCOME_SUCCESS
from audit_service import record_audit
from communication import communikation
from gallery_library import (
    GALLERY_NAME_MAX,
    content_digest,
    gallery_name_error,
    human_size,
    is_image_name,
    list_galleries,
    list_images,
    move_image as reorder,
    normalize_gallery_name,
    parse_selection,
    read_order,
    slideshow_summary,
    stored_image_name,
    write_order,
)
from image_storage import (
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_UPLOAD,
    SCREEN_MAX_SIDE,
    THUMBNAIL_DIR,
    accept_image_upload,
    generate_thumbnail,
    optimize_for_screen,
    thumbnail_filename,
)
from models import ConfigOption, db
from path_security import UnsafePathError, safe_path_under, to_abs_base_dir, validate_path_segment
from routes.admin_security import require_permission
from ui_feedback import display_toast

admin_gallery_bp = Blueprint('admin_gallery', __name__)

SELECTION_KEY = "announce_infos_gallery"

# Miniatures créées au plus par affichage, pour les images qui n'en ont pas
# (ajoutées avant les miniatures, ou restaurées depuis une sauvegarde).
THUMBNAILS_PER_REQUEST = 40

# Vignettes d'aperçu sur la carte d'une galerie.
COVER_COUNT = 4


# ---------------------------------------------------------------------------
# Accès disque et configuration
# ---------------------------------------------------------------------------

def _galleries_dir() -> Path:
    return to_abs_base_dir(app.config['ANNOUNCE_GALLERY_FOLDERS'], root_dir=app.root_path)


def _gallery_dir(name) -> Path:
    """Dossier d'une galerie existante. Lève ``UnsafePathError`` si le nom est
    dangereux, ``FileNotFoundError`` si la galerie n'existe pas."""
    validate_path_segment(name, what="gallery name")
    gallery_dir = safe_path_under(_galleries_dir(), name)
    if not gallery_dir.is_dir():
        raise FileNotFoundError(name)
    return gallery_dir


def _selected_galleries() -> list[str]:
    option = ConfigOption.query.filter_by(config_key=SELECTION_KEY).first()
    return parse_selection(option.value_str if option else None)


def _save_selected_galleries(names) -> None:
    """Enregistre les galeries affichées, en écartant celles qui n'existent plus."""
    existing = set(list_galleries(_galleries_dir()))
    kept = sorted({name for name in names if name in existing}, key=str.casefold)
    option = ConfigOption.query.filter_by(config_key=SELECTION_KEY).first()
    if option is None:
        option = ConfigOption(config_key=SELECTION_KEY)
        db.session.add(option)
    option.value_str = json.dumps(kept)
    db.session.commit()


def _ensure_thumbnails(gallery_dir: Path, images, limit=THUMBNAILS_PER_REQUEST) -> None:
    """Crée les miniatures manquantes de ``images`` (complète ``thumbnail``).

    Sans miniature, la page chargeait l'image d'origine — plusieurs Mo par
    vignette pour une galerie de photos.
    """
    thumb_dir = gallery_dir / THUMBNAIL_DIR
    created = 0
    for image in images:
        if image["thumbnail"] or image["size"] > MAX_IMAGE_BYTES:
            continue
        if created >= limit:
            break
        created += 1
        try:
            data = generate_thumbnail((gallery_dir / image["filename"]).read_bytes())
            if data is None:
                continue
            thumb_dir.mkdir(parents=True, exist_ok=True)
            name = thumbnail_filename(image["filename"])
            (thumb_dir / name).write_bytes(data)
            image["thumbnail"] = name
        except OSError:
            app.logger.warning("Miniature impossible pour %s", image["filename"])


def _remove_gallery_files(gallery_dir: Path) -> None:
    for child in gallery_dir.iterdir():
        if child.is_file():
            child.unlink()
        elif child.name == THUMBNAIL_DIR:
            for thumb in child.iterdir():
                if thumb.is_file():
                    thumb.unlink()
            child.rmdir()
    gallery_dir.rmdir()


# ---------------------------------------------------------------------------
# Diffusion et réponses
# ---------------------------------------------------------------------------

def _broadcast(*, screens: bool) -> None:
    communikation("admin", event="refresh_gallery_list")
    if screens:
        # Le diaporama des écrans se recharge seul, sans recharger la page.
        communikation("update_screen", event="refresh_gallery")


def _changed(body="", status=200):
    response = make_response(body, status)
    response.headers["HX-Trigger"] = "galleryChanged"
    return response


def _render_list():
    base_dir = _galleries_dir()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        app.logger.warning("Dossier des galeries inaccessible : %s", base_dir)
    selected = set(_selected_galleries())

    cards = []
    shown_images = 0
    for name in list_galleries(base_dir):
        images = list_images(base_dir / name)
        covers = images[:COVER_COUNT]
        _ensure_thumbnails(base_dir / name, covers)
        if name in selected:
            shown_images += len(images)
        cards.append({
            "name": name,
            "selected": name in selected,
            "count": len(images),
            "size_label": human_size(sum(img["size"] for img in images)) if images else "",
            "covers": covers,
        })

    summary = slideshow_summary(
        display_enabled=bool(app.config.get('ANNOUNCE_INFOS_DISPLAY', False)),
        selected_count=sum(1 for card in cards if card["selected"]),
        image_count=shown_images,
        display_time=app.config.get('ANNOUNCE_INFOS_DISPLAY_TIME', 30),
        shuffled=bool(app.config.get('ANNOUNCE_INFOS_MIX_FOLDERS', False)),
    )
    return render_template('admin/gallery_list_galleries.html', cards=cards, summary=summary)


def _render_images(name, gallery_dir):
    images = list_images(gallery_dir)
    _ensure_thumbnails(gallery_dir, images)
    return render_template('admin/gallery_list_images.html', gallery=name, images=images)


def _render_manage(name):
    return render_template(
        'admin/gallery_manage.html',
        gallery=name,
        name_max=GALLERY_NAME_MAX,
        accept=",".join(f".{ext}" for ext in sorted(ALLOWED_IMAGE_EXTENSIONS)),
        formats=", ".join(sorted(ext.upper() for ext in ALLOWED_IMAGE_EXTENSIONS - {"jpeg"})),
        max_mb=MAX_IMAGE_BYTES // (1024 * 1024),
        max_files=MAX_IMAGES_PER_UPLOAD,
        max_side=SCREEN_MAX_SIDE,
    )


def _not_found():
    display_toast(success=False, message="Cette galerie n'existe plus.")
    return _changed("", 404)


# ---------------------------------------------------------------------------
# Pages et fragments
# ---------------------------------------------------------------------------

@admin_gallery_bp.route('/admin/info')
@require_permission('gallery')
def admin_info():
    return render_template('/admin/gallery.html', name_max=GALLERY_NAME_MAX)


@admin_gallery_bp.route("/admin/gallery/list", methods=['GET'])
@require_permission('gallery')
def gallery_list():
    return _render_list()


@admin_gallery_bp.route('/admin/gallery/<name>', methods=['GET'])
@require_permission('gallery')
def gallery(name):
    try:
        _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return _not_found()
    return _render_manage(name)


@admin_gallery_bp.route('/admin/gallery/images_list/<name>', methods=['GET'])
@require_permission('gallery')
def gallery_images_list(name):
    try:
        gallery_dir = _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return _not_found()
    return _render_images(name, gallery_dir)


# ---------------------------------------------------------------------------
# Galeries : créer, afficher à l'écran, renommer, supprimer
# ---------------------------------------------------------------------------

@admin_gallery_bp.route('/admin/gallery/create_gallery', methods=['POST'])
@require_permission('gallery')
def create_gallery():
    name = normalize_gallery_name(request.form.get('name'))
    error = gallery_name_error(name, existing=list_galleries(_galleries_dir()))
    if error:
        # 204 : rien n'est remplacé, la saisie reste en place.
        return display_toast(success=False, message=error)
    try:
        safe_path_under(_galleries_dir(), name).mkdir(parents=True, exist_ok=False)
    except (UnsafePathError, OSError):
        return display_toast(success=False, message="Impossible de créer cette galerie.")

    record_audit(ACTION_CREATE, "gallery", target_id=name, outcome=OUTCOME_SUCCESS)
    _broadcast(screens=False)
    display_toast(success=True, message=f"Galerie « {name} » créée : ajoutez-y des images.")
    return _changed(_render_manage(name))


@admin_gallery_bp.route('/admin/gallery/choose_gallery', methods=['POST'])
@require_permission('gallery')
def choose_gallery():
    """Affiche ou retire une galerie du diaporama de l'écran d'annonce."""
    name = request.form.get('gallery_name')
    checked = request.form.get('checked') == "true"
    try:
        _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return _not_found()

    selected = set(_selected_galleries())
    if checked:
        selected.add(name)
    else:
        selected.discard(name)
    _save_selected_galleries(selected)
    record_audit(ACTION_UPDATE, "config",
                 target_id=SELECTION_KEY, outcome=OUTCOME_SUCCESS,
                 details=f"{name} {'sélectionnée' if checked else 'désélectionnée'}")
    _broadcast(screens=True)
    display_toast(success=True, message=(
        f"« {name} » est affichée à l'écran." if checked
        else f"« {name} » n'est plus affichée à l'écran."))
    return _render_list()


@admin_gallery_bp.route('/admin/gallery/rename/<name>', methods=['POST'])
@require_permission('gallery')
def rename_gallery(name):
    try:
        gallery_dir = _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return _not_found()

    new_name = normalize_gallery_name(request.form.get('name'))
    if new_name == name:
        return _render_manage(name)
    others = [other for other in list_galleries(_galleries_dir()) if other != name]
    error = gallery_name_error(new_name, existing=others)
    if error:
        return display_toast(success=False, message=error)
    try:
        gallery_dir.rename(safe_path_under(_galleries_dir(), new_name))
    except (UnsafePathError, OSError):
        return display_toast(success=False, message="Impossible de renommer cette galerie.")

    selected = _selected_galleries()
    was_selected = name in selected
    if was_selected:
        _save_selected_galleries([new_name if item == name else item for item in selected])
    record_audit(ACTION_UPDATE, "gallery", target_id=new_name,
                 outcome=OUTCOME_SUCCESS, details=f"renommée depuis {name}")
    _broadcast(screens=was_selected)
    display_toast(success=True, message=f"Galerie renommée en « {new_name} ».")
    return _changed(_render_manage(new_name))


@admin_gallery_bp.route('/admin/gallery/delete_gallery/<name>', methods=['DELETE'])
@require_permission('gallery')
def delete_gallery(name):
    try:
        gallery_dir = _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return "Gallery not found", 404

    # Vérifier tout le contenu AVANT de supprimer quoi que ce soit : un
    # sous-dossier inattendu ne doit pas laisser la galerie à moitié vidée.
    if any(c.is_dir() and c.name != THUMBNAIL_DIR for c in gallery_dir.iterdir()):
        display_toast(success=False, message=(
            "Cette galerie contient un sous-dossier inattendu : suppression annulée."))
        return "Gallery contains unexpected subdirectories", 400

    selected = _selected_galleries()
    was_selected = name in selected
    _remove_gallery_files(gallery_dir)
    record_audit(ACTION_DELETE, "gallery", target_id=name, outcome=OUTCOME_SUCCESS)
    if was_selected:
        _save_selected_galleries([item for item in selected if item != name])

    _broadcast(screens=was_selected)
    display_toast(success=True, message=f"Galerie « {name} » supprimée.")
    return _changed("")


# ---------------------------------------------------------------------------
# Images : ajouter, ranger, supprimer
# ---------------------------------------------------------------------------

@admin_gallery_bp.route('/admin/gallery/upload/<name>', methods=['POST'])
@require_permission('gallery')
def upload_gallery(name):
    try:
        gallery_dir = _gallery_dir(name)
    except UnsafePathError:
        return "Invalid gallery name", 400
    except FileNotFoundError:
        return _not_found()

    files = [f for f in request.files.getlist('photos') if f.filename]
    if not files:
        return display_toast(success=False, message="Choisissez au moins une image.")
    if len(files) > MAX_IMAGES_PER_UPLOAD:
        return display_toast(
            success=False,
            message=f"Trop de fichiers en une fois (maximum {MAX_IMAGES_PER_UPLOAD}).",
        )

    present = {content_digest(img["filename"]) for img in list_images(gallery_dir)}
    saved = 0
    resized = 0
    duplicates = 0
    skipped: list[str] = []
    thumb_dir = gallery_dir / THUMBNAIL_DIR
    for file in files:
        ok, err, result = accept_image_upload(file)
        if not ok:
            skipped.append(f"{file.filename} : {err}")
            continue
        digest = content_digest(result["filename"])
        if digest in present:
            duplicates += 1
            continue
        present.add(digest)
        stored = stored_image_name(file.filename, result["filename"])
        # Image plus grande que nécessaire pour un écran : on enregistre une
        # version réduite. Le nom garde l'empreinte de l'originale, pour que
        # le même fichier renvoyé plus tard soit reconnu comme doublon.
        data = optimize_for_screen(result["data"], result["ext"])
        if data is None:
            data = result["data"]
        else:
            resized += 1
        (gallery_dir / stored).write_bytes(data)
        # Miniature pour l'administration (évite de charger l'image entière).
        # En cas d'échec, elle sera retentée à l'affichage.
        thumb_data = generate_thumbnail(data)
        if thumb_data is not None:
            thumb_dir.mkdir(parents=True, exist_ok=True)
            (thumb_dir / thumbnail_filename(stored)).write_bytes(thumb_data)
        saved += 1

    parts = [f"{saved} image(s) ajoutée(s)"]
    if resized:
        parts.append(f"{resized} réduite(s) à la taille de l'écran")
    if duplicates:
        parts.append(f"{duplicates} déjà présente(s)")
    if skipped:
        detail = " ; ".join(skipped[:3])
        if len(skipped) > 3:
            detail += f" … (+{len(skipped) - 3} autre(s))"
        parts.append(f"{len(skipped)} refusée(s) — {detail}")
    display_toast(success=not skipped and (saved > 0 or duplicates > 0),
                  message=", ".join(parts) + ".")

    if saved:
        record_audit(ACTION_CREATE, "gallery_image", target_id=name,
                     outcome=OUTCOME_SUCCESS,
                     details=f"{saved} image(s) ajoutée(s)")
        _broadcast(screens=name in _selected_galleries())
    return _changed(_render_images(name, gallery_dir))


@admin_gallery_bp.route('/admin/gallery/move_image/<gallery>/<image>', methods=['POST'])
@require_permission('gallery')
def move_image(gallery, image):
    try:
        gallery_dir = _gallery_dir(gallery)
        validate_path_segment(image, what="image filename")
    except UnsafePathError:
        return "Invalid path", 400
    except FileNotFoundError:
        return _not_found()

    current = [img["filename"] for img in list_images(gallery_dir)]
    updated = reorder(current, image, request.form.get('direction', ''))
    if updated != current:
        write_order(gallery_dir, updated)
        _broadcast(screens=gallery in _selected_galleries())
        return _changed(_render_images(gallery, gallery_dir))
    return _render_images(gallery, gallery_dir)


@admin_gallery_bp.route('/admin/gallery/delete_image/<gallery>/<image>', methods=['DELETE'])
@require_permission('gallery')
def delete_image(gallery, image):
    try:
        gallery_dir = _gallery_dir(gallery)
        validate_path_segment(image, what="image filename")
        target_path = safe_path_under(gallery_dir, image)
    except UnsafePathError:
        return "Invalid path", 400
    except FileNotFoundError:
        return _not_found()

    # Seules les images se suppriment par cette route (pas le fichier d'ordre).
    if is_image_name(image) and target_path.is_file():
        target_path.unlink()
        record_audit(ACTION_DELETE, "gallery_image", target_id=image,
                     outcome=OUTCOME_SUCCESS, details=f"gallery={gallery}")
        thumb = gallery_dir / THUMBNAIL_DIR / thumbnail_filename(image)
        if thumb.is_file():
            thumb.unlink()
        order = read_order(gallery_dir)
        if image in order:
            write_order(gallery_dir, [item for item in order if item != image])
        _broadcast(screens=gallery in _selected_galleries())
        display_toast(success=True, message="Image supprimée.")

    return _changed(_render_images(gallery, gallery_dir))
