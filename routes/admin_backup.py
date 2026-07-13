import json
from flask import (
    Blueprint, request, Response, current_app,
    render_template_string, make_response,
)
from datetime import datetime
from routes.admin_security import require_permission
from backup_service import (
    BACKUP_SECTIONS, SECTION_GROUPS, export_sections,
    restore_sections, preview_backup,
    load_and_validate_backup, BackupValidationError, MAX_BACKUP_FILE_BYTES,
)

admin_backup_bp = Blueprint('admin_backup', __name__)


# Message générique renvoyé à l'utilisateur lorsqu'une restauration échoue :
# le détail technique (exception, section, trace) est journalisé côté serveur,
# jamais renvoyé au navigateur.
_GENERIC_RESTORE_ERROR = "La restauration a échoué. Consultez les journaux du serveur."


# Gabarit Jinja de la prévisualisation. Rendu via ``render_template_string`` :
# l'auto-échappement de Jinja garantit qu'aucune métadonnée du fichier importé
# (app, version, date, clés/labels de section) n'est injectée en HTML brut.
_PREVIEW_TEMPLATE = """
<div class="card mb-3">
    <div class="card-body">
        <p class="mb-1"><strong>Application :</strong> {{ info.app }}</p>
        <p class="mb-1"><strong>Version format :</strong> {{ info.format_version }}</p>
        <p class="mb-1"><strong>Date :</strong> {{ info.timestamp }}</p>
        <p class="mb-2"><strong>Sections ({{ info.sections|length }}) :</strong></p>
        <div class="mb-3">
            {% for s in info.sections %}
                <div class="form-check">
                    <input class="form-check-input restore-section-check" type="checkbox"
                           name="restore_sections" value="{{ s.key }}" id="chk_{{ s.key }}" checked>
                    <label class="form-check-label" for="chk_{{ s.key }}">
                        {{ s.label }}{% if s.count %} ({{ s.count }} éléments){% endif %}
                    </label>
                </div>
            {% endfor %}
        </div>
        <div class="alert alert-warning mb-2">
            <i class="bi bi-exclamation-triangle"></i>
            Attention : les données existantes des sections sélectionnées seront remplacées !
        </div>
        <button type="submit" class="btn btn-danger btn-sm" id="btn_confirm_restore">
            Restaurer les sections sélectionnées
        </button>
    </div>
</div>
"""


def _alert(kind: str, message: str) -> str:
    """Fragment d'alerte Bootstrap avec message échappé."""
    return render_template_string(
        '<div class="alert alert-{{ kind }}">{{ message }}</div>',
        kind=kind, message=message,
    )


def _read_uploaded_backup():
    """Lit, borne et valide le fichier de sauvegarde téléversé.

    Renvoie ``(backup_data, None)`` en cas de succès, ou ``(None, message)`` où
    ``message`` est sûr pour l'affichage (aucun détail interne). Vérifie
    l'extension, la taille maximale de la requête et du fichier, puis délègue la
    validation structurelle stricte à :func:`load_and_validate_backup`."""
    file = request.files.get('file')
    if not file or not file.filename or not file.filename.lower().endswith('.json'):
        return None, "Fichier invalide. Sélectionnez un fichier .json."

    # Refus rapide via l'en-tête Content-Length (borne supérieure de la requête).
    if request.content_length is not None and request.content_length > MAX_BACKUP_FILE_BYTES:
        return None, "Fichier trop volumineux."

    # Lecture bornée : au plus MAX+1 octets, pour détecter le dépassement même
    # si Content-Length est absent ou mensonger.
    raw = file.read(MAX_BACKUP_FILE_BYTES + 1)
    if len(raw) > MAX_BACKUP_FILE_BYTES:
        return None, "Fichier trop volumineux."

    try:
        data = load_and_validate_backup(raw)
    except BackupValidationError as e:
        return None, str(e)
    return data, None


def _labels_for(keys):
    """Libellés (contrôlés côté serveur) des sections restaurées."""
    labels = []
    for key in keys:
        cls = BACKUP_SECTIONS.get(key)
        labels.append(cls.label if cls else key)
    return labels


@admin_backup_bp.route('/admin/backup/export')
@require_permission('app')
def backup_export():
    """Export selected sections as a JSON file download."""
    sections_param = request.args.get('sections', 'all')

    if sections_param == 'all':
        section_keys = list(BACKUP_SECTIONS.keys())
    else:
        section_keys = [s.strip() for s in sections_param.split(',') if s.strip()]

    backup_data = export_sections(section_keys)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(section_keys) == 1:
        filename = f"gf_backup_{section_keys[0]}_{timestamp}.json"
    else:
        filename = f"gf_backup_{timestamp}.json"

    return Response(
        json.dumps(backup_data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@admin_backup_bp.route('/admin/backup/preview', methods=['POST'])
@require_permission('app')
def backup_preview():
    """Preview the contents of an uploaded backup file (HTMX endpoint)."""
    backup_data, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    info = preview_backup(backup_data)
    return render_template_string(_PREVIEW_TEMPLATE, info=info)


@admin_backup_bp.route('/admin/backup/import', methods=['POST'])
@require_permission('app')
def backup_import():
    """Restore sections from an uploaded backup file."""
    backup_data, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    sections_param = request.form.getlist('restore_sections')
    if not sections_param:
        sections_param = None

    report = restore_sections(backup_data, sections_param)

    try:
        current_app.load_configuration(current_app)
    except Exception:
        current_app.logger.exception("Backup import: reload configuration failed")

    if report.get("errors"):
        current_app.logger.error(f"Backup import errors: {report.get('errors')}")

    if report.get("success"):
        restored_labels = _labels_for(report.get("restored", []))
        msg = f"Restauration réussie : {', '.join(restored_labels)}"
        if report.get("errors"):
            msg += " (certaines sections n'ont pas pu être restaurées)"

        try:
            current_app.display_toast(success=True, message=msg)
        except Exception:
            pass

        resp = make_response(_alert("success", msg))
        resp.headers['HX-Refresh'] = 'true'
        return resp

    try:
        current_app.display_toast(success=False, message=_GENERIC_RESTORE_ERROR)
    except Exception:
        pass
    return _alert("danger", _GENERIC_RESTORE_ERROR)


@admin_backup_bp.route('/admin/backup/import_multi', methods=['POST'])
@require_permission('app')
def backup_import_multi():
    """Restore multiple sections from an uploaded backup file (for per-page restore with CSS + config)."""
    sections_param = request.form.get('sections', '')
    requested_keys = [s.strip() for s in sections_param.split(',') if s.strip()]
    if not requested_keys:
        return _alert("danger", "Aucune section spécifiée.")

    backup_data, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    available = backup_data.get("sections", [])
    keys_to_restore = [k for k in requested_keys if k in available and k in BACKUP_SECTIONS]

    if not keys_to_restore:
        return _alert("warning", "Aucune des sections demandées n'est présente dans ce fichier.")

    report = restore_sections(backup_data, keys_to_restore)

    try:
        current_app.load_configuration(current_app)
    except Exception:
        current_app.logger.exception("Backup import_multi: reload configuration failed")

    if report.get("errors"):
        current_app.logger.error(f"Backup import_multi errors: {report.get('errors')}")

    if report.get("restored"):
        restored_labels = _labels_for(report.get("restored", []))
        msg = f"Restauration réussie : {', '.join(restored_labels)}"
        if report.get("errors"):
            msg += " (certaines sections n'ont pas pu être restaurées)"
        try:
            current_app.display_toast(success=True, message=msg)
        except Exception:
            pass
        resp = make_response(_alert("success", msg))
        resp.headers['HX-Refresh'] = 'true'
        return resp

    return _alert("danger", _GENERIC_RESTORE_ERROR)


@admin_backup_bp.route('/admin/backup/import_single', methods=['POST'])
@require_permission('app')
def backup_import_single():
    """Restore a single section from an uploaded backup file (for per-page restore)."""
    section_key = request.form.get('section')
    if not section_key or section_key not in BACKUP_SECTIONS:
        return _alert("danger", "Section inconnue.")

    backup_data, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    cls = BACKUP_SECTIONS.get(section_key)
    label = cls.label if cls else section_key

    if section_key not in backup_data.get("sections", []):
        return _alert("danger", f"La section « {label} » est absente de ce fichier.")

    report = restore_sections(backup_data, [section_key])

    try:
        current_app.load_configuration(current_app)
    except Exception:
        current_app.logger.exception("Backup import_single: reload configuration failed")

    if report.get("errors"):
        current_app.logger.error(f"Backup import_single errors: {report.get('errors')}")

    if report.get("restored"):
        try:
            current_app.display_toast(success=True, message=f"Restauration de '{label}' réussie")
        except Exception:
            pass
        resp = make_response(_alert("success", f"Restauration de « {label} » réussie."))
        resp.headers['HX-Refresh'] = 'true'
        return resp

    return _alert("danger", _GENERIC_RESTORE_ERROR)
