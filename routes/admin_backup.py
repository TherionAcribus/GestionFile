import json
from flask import Blueprint, request, Response, current_app, jsonify, render_template, make_response
from datetime import datetime
from routes.admin_security import require_permission
from backup_service import (
    BACKUP_SECTIONS, SECTION_GROUPS, export_sections,
    restore_sections, preview_backup
)

admin_backup_bp = Blueprint('admin_backup', __name__)


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
    file = request.files.get('file')
    if not file or not file.filename.endswith('.json'):
        return '<div class="alert alert-danger">Fichier invalide. Veuillez sélectionner un fichier .json</div>'

    try:
        backup_data = json.load(file)
    except Exception:
        return '<div class="alert alert-danger">Impossible de lire le fichier JSON.</div>'

    info = preview_backup(backup_data)

    if info.get("app") != "GestionFile":
        return '<div class="alert alert-danger">Ce fichier n\'est pas un fichier de sauvegarde GestionFile.</div>'

    html = f'''
    <div class="card mb-3">
        <div class="card-body">
            <p class="mb-1"><strong>Application :</strong> {info["app"]}</p>
            <p class="mb-1"><strong>Version format :</strong> {info["format_version"]}</p>
            <p class="mb-1"><strong>Date :</strong> {info["timestamp"]}</p>
            <p class="mb-2"><strong>Sections ({len(info["sections"])}) :</strong></p>
            <div class="mb-3">
    '''

    for s in info["sections"]:
        count_label = f" ({s['count']} éléments)" if s["count"] else ""
        html += f'''
                <div class="form-check">
                    <input class="form-check-input restore-section-check" type="checkbox"
                           name="restore_sections" value="{s['key']}" id="chk_{s['key']}" checked>
                    <label class="form-check-label" for="chk_{s['key']}">
                        {s['label']}{count_label}
                    </label>
                </div>
        '''

    html += '''
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
    '''
    return html


@admin_backup_bp.route('/admin/backup/import', methods=['POST'])
@require_permission('app')
def backup_import():
    """Restore sections from an uploaded backup file."""
    file = request.files.get('file')
    if not file or not file.filename.endswith('.json'):
        return jsonify({"success": False, "error": "Fichier invalide."}), 400

    try:
        backup_data = json.load(file)
    except Exception:
        return jsonify({"success": False, "error": "Impossible de lire le fichier JSON."}), 400

    sections_param = request.form.getlist('restore_sections')
    if not sections_param:
        sections_param = None

    report = restore_sections(backup_data, sections_param)

    try:
        current_app.load_configuration(current_app)
    except Exception:
        pass

    if report.get("success"):
        restored_labels = []
        for key in report.get("restored", []):
            cls = BACKUP_SECTIONS.get(key)
            restored_labels.append(cls.label if cls else key)

        msg = f"Restauration réussie : {', '.join(restored_labels)}"
        if report.get("errors"):
            msg += f" (erreurs : {'; '.join(report['errors'])})"

        try:
            current_app.display_toast(success=True, message=msg)
        except Exception:
            pass

        resp = make_response(f'<div class="alert alert-success">{msg}</div>')
        resp.headers['HX-Refresh'] = 'true'
        return resp
    else:
        error_msg = report.get("error", "; ".join(report.get("errors", [])))
        try:
            current_app.display_toast(success=False, message=error_msg)
        except Exception:
            pass
        return f'<div class="alert alert-danger">{error_msg}</div>'


@admin_backup_bp.route('/admin/backup/import_multi', methods=['POST'])
@require_permission('app')
def backup_import_multi():
    """Restore multiple sections from an uploaded backup file (for per-page restore with CSS + config)."""
    file = request.files.get('file')
    sections_param = request.form.get('sections', '')

    if not file or not file.filename.endswith('.json'):
        return '<div class="alert alert-danger">Fichier invalide.</div>'

    requested_keys = [s.strip() for s in sections_param.split(',') if s.strip()]
    if not requested_keys:
        return '<div class="alert alert-danger">Aucune section spécifiée.</div>'

    try:
        backup_data = json.load(file)
    except Exception:
        return '<div class="alert alert-danger">Impossible de lire le fichier JSON.</div>'

    if backup_data.get("app") != "GestionFile":
        return '<div class="alert alert-danger">Fichier de sauvegarde invalide.</div>'

    available = backup_data.get("sections", [])
    keys_to_restore = [k for k in requested_keys if k in available and k in BACKUP_SECTIONS]

    if not keys_to_restore:
        return '<div class="alert alert-warning">Aucune des sections demandées n\'est présente dans ce fichier.</div>'

    report = restore_sections(backup_data, keys_to_restore)

    try:
        current_app.load_configuration(current_app)
    except Exception:
        pass

    if report.get("restored"):
        restored_labels = []
        for key in report.get("restored", []):
            cls = BACKUP_SECTIONS.get(key)
            restored_labels.append(cls.label if cls else key)
        msg = f"Restauration réussie : {', '.join(restored_labels)}"
        if report.get("errors"):
            msg += f" (erreurs : {'; '.join(report['errors'])})"
        try:
            current_app.display_toast(success=True, message=msg)
        except Exception:
            pass
        resp = make_response(f'<div class="alert alert-success">{msg}</div>')
        resp.headers['HX-Refresh'] = 'true'
        return resp
    else:
        error_msg = "; ".join(report.get("errors", ["Erreur inconnue"]))
        return f'<div class="alert alert-danger">{error_msg}</div>'


@admin_backup_bp.route('/admin/backup/import_single', methods=['POST'])
@require_permission('app')
def backup_import_single():
    """Restore a single section from an uploaded backup file (for per-page restore)."""
    file = request.files.get('file')
    section_key = request.form.get('section')

    if not file or not file.filename.endswith('.json'):
        return '<div class="alert alert-danger">Fichier invalide.</div>'

    if not section_key or section_key not in BACKUP_SECTIONS:
        return '<div class="alert alert-danger">Section inconnue.</div>'

    try:
        backup_data = json.load(file)
    except Exception:
        return '<div class="alert alert-danger">Impossible de lire le fichier JSON.</div>'

    if backup_data.get("app") != "GestionFile":
        return '<div class="alert alert-danger">Fichier de sauvegarde invalide.</div>'

    if section_key not in backup_data.get("sections", []):
        return f'<div class="alert alert-danger">La section "{section_key}" est absente de ce fichier.</div>'

    report = restore_sections(backup_data, [section_key])

    try:
        current_app.load_configuration(current_app)
    except Exception:
        pass

    if report.get("restored"):
        cls = BACKUP_SECTIONS.get(section_key)
        label = cls.label if cls else section_key
        try:
            current_app.display_toast(success=True, message=f"Restauration de '{label}' réussie")
        except Exception:
            pass
        resp = make_response(f'<div class="alert alert-success">Restauration de "{label}" réussie.</div>')
        resp.headers['HX-Refresh'] = 'true'
        return resp
    else:
        error_msg = "; ".join(report.get("errors", ["Erreur inconnue"]))
        return f'<div class="alert alert-danger">{error_msg}</div>'
