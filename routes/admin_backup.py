import json
import os
import tempfile
import zipfile
from flask import (
    Blueprint, request, Response, current_app,
    render_template_string, make_response, jsonify, stream_with_context,
)
from datetime import datetime
from routes.admin_security import require_permission
from backup_service import (
    BACKUP_SECTIONS, SECTION_GROUPS, export_sections,
    restore_sections, preview_backup,
    load_and_validate_backup, load_and_validate_archive,
    BackupValidationError, MAX_BACKUP_FILE_BYTES, MAX_ARCHIVE_FILE_BYTES,
    selection_has_binary, write_backup_archive, estimate_export_size,
)
from audit_service import record_audit
from audit_log import ACTION_RESTORE, OUTCOME_SUCCESS, OUTCOME_FAILURE

admin_backup_bp = Blueprint('admin_backup', __name__)


def _safe_unlink(path):
    """Supprime un fichier temporaire sans jamais lever."""
    try:
        os.unlink(path)
    except OSError:
        pass


class _BackupUpload:
    """Sauvegarde téléversée, prête à être prévisualisée ou restaurée.

    ``manifest`` a la même forme quel que soit le format (2.0 JSON plat ou 3.0
    manifeste d'archive). ``archive`` est un :class:`BackupArchive` pour les
    fichiers 3.0 (accès aux images binaires), ``None`` sinon. ``close()`` libère
    l'archive et le fichier temporaire éventuels."""

    def __init__(self, manifest, archive=None, tmp_path=None):
        self.manifest = manifest
        self.archive = archive
        self.tmp_path = tmp_path

    def close(self):
        if self.archive is not None:
            self.archive.close()
        if self.tmp_path is not None:
            _safe_unlink(self.tmp_path)


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
        {% if info.excluded_secrets %}
        <div class="alert alert-info mb-2">
            <i class="bi bi-shield-lock"></i>
            Cette sauvegarde <strong>ne contient aucun secret</strong>
            ({{ info.excluded_secrets|length }} exclu(s) : {{ info.excluded_secrets|join(', ') }}).
            Après restauration, ressaisissez-les manuellement dans les pages concernées.
        </div>
        {% endif %}
        <button type="button" class="btn btn-danger btn-sm" id="btn_confirm_restore"
                hx-post="/admin/backup/import"
                hx-target="#importResult"
                hx-swap="innerHTML"
                hx-disabled-elt="this"
                hx-indicator="#restoreSpinner"
                hx-confirm="Confirmer la restauration ? Les données existantes des sections sélectionnées seront définitivement remplacées.">
            <i class="bi bi-arrow-counterclockwise"></i>
            Restaurer les sections sélectionnées
            <span id="restoreSpinner" class="htmx-indicator spinner-border spinner-border-sm ms-1" role="status"></span>
        </button>
    </div>
</div>
"""


# Rapport de restauration : une ligne par section demandée, avec son état
# (restaurée / non restaurée). Rendu via ``render_template_string`` (auto-échappé).
# Aucun détail technique n'y figure : les erreurs restent dans les journaux.
_RESTORE_REPORT_TEMPLATE = """
<div class="alert alert-{{ tone }}">
    <strong>{{ heading }}</strong>
</div>
<ul class="list-group mb-2">
    {% for row in rows %}
    <li class="list-group-item d-flex justify-content-between align-items-center py-2">
        <span>{{ row.label }}</span>
        {% if row.ok %}
        <span class="badge bg-success"><i class="bi bi-check-lg"></i> Restaurée</span>
        {% else %}
        <span class="badge bg-danger"><i class="bi bi-x-lg"></i> Non restaurée</span>
        {% endif %}
    </li>
    {% endfor %}
</ul>
{% if any_restored %}
<p class="small text-muted mb-1">
    Rechargez la page pour visualiser les données restaurées dans l'interface.
</p>
<button type="button" class="btn btn-primary btn-sm" onclick="window.location.reload()">
    <i class="bi bi-arrow-clockwise"></i> Recharger la page
</button>
{% endif %}
"""


def _alert(kind: str, message: str) -> str:
    """Fragment d'alerte Bootstrap avec message échappé."""
    return render_template_string(
        '<div class="alert alert-{{ kind }}">{{ message }}</div>',
        kind=kind, message=message,
    )


def _load_zip_upload(raw):
    """Écrit l'archive téléversée sur disque, l'ouvre et valide son manifeste.

    Renvoie ``(_BackupUpload, None)`` ou ``(None, message sûr)``. Le fichier
    temporaire et la ``ZipFile`` sont libérés par ``_BackupUpload.close()`` (ou
    immédiatement supprimés en cas d'erreur)."""
    tmp = tempfile.NamedTemporaryFile(prefix="gf_restore_", suffix=".zip", delete=False)
    try:
        tmp.write(raw)
        tmp.flush()
    except Exception:
        tmp.close()
        _safe_unlink(tmp.name)
        return None, "Fichier illisible."
    finally:
        if not tmp.closed:
            tmp.close()

    if not zipfile.is_zipfile(tmp.name):
        _safe_unlink(tmp.name)
        return None, "Fichier ZIP invalide."

    try:
        zf = zipfile.ZipFile(tmp.name, "r")
    except Exception:
        _safe_unlink(tmp.name)
        return None, "Fichier ZIP invalide."

    try:
        archive = load_and_validate_archive(zf)
    except BackupValidationError as e:
        try:
            zf.close()
        except Exception:
            pass
        _safe_unlink(tmp.name)
        return None, str(e)

    return _BackupUpload(manifest=archive.manifest, archive=archive, tmp_path=tmp.name), None


def _read_uploaded_backup():
    """Lit, borne et valide le fichier de sauvegarde téléversé (.json ou .zip).

    Renvoie ``(_BackupUpload, None)`` en cas de succès, ou ``(None, message)`` où
    ``message`` est sûr pour l'affichage (aucun détail interne). Vérifie
    l'extension et la taille maximale, puis délègue la validation structurelle
    stricte selon le format (JSON plat 2.0 ou archive ZIP 3.0)."""
    file = request.files.get('file')
    if not file or not file.filename:
        return None, "Fichier invalide. Sélectionnez un fichier .json ou .zip."

    name = file.filename.lower()
    is_zip = name.endswith('.zip')
    is_json = name.endswith('.json')
    if not (is_zip or is_json):
        return None, "Fichier invalide. Sélectionnez un fichier .json ou .zip."

    # Les archives (images brutes) peuvent être plus volumineuses que les JSON
    # (base64) : borne dédiée par format.
    limit = MAX_ARCHIVE_FILE_BYTES if is_zip else MAX_BACKUP_FILE_BYTES

    # Refus rapide via l'en-tête Content-Length (borne supérieure de la requête).
    if request.content_length is not None and request.content_length > limit:
        return None, "Fichier trop volumineux."

    # Lecture bornée : au plus limit+1 octets, pour détecter le dépassement même
    # si Content-Length est absent ou mensonger.
    raw = file.read(limit + 1)
    if len(raw) > limit:
        return None, "Fichier trop volumineux."

    if is_zip:
        return _load_zip_upload(raw)

    try:
        data = load_and_validate_backup(raw)
    except BackupValidationError as e:
        return None, str(e)
    return _BackupUpload(manifest=data), None


def _labels_for(keys):
    """Libellés (contrôlés côté serveur) des sections restaurées."""
    labels = []
    for key in keys:
        cls = BACKUP_SECTIONS.get(key)
        labels.append(cls.label if cls else key)
    return labels


def _render_restore_report(report: dict, requested_keys: list) -> str:
    """Rend un rapport de restauration section par section.

    ``requested_keys`` est l'ensemble des sections que l'utilisateur a demandé à
    restaurer ; chacune est marquée « restaurée » ou « non restaurée » selon
    ``report['restored']``. Le ton (success / warning / danger) et l'en-tête
    reflètent fidèlement le résultat : jamais « réussie » si seule une partie a
    été restaurée."""
    restored = set(report.get("restored", []))
    labels = _labels_for(requested_keys)
    rows = [
        {"label": label, "ok": key in restored}
        for key, label in zip(requested_keys, labels)
    ]
    n_ok = sum(1 for r in rows if r["ok"])
    n_total = len(rows)
    n_fail = n_total - n_ok

    if n_ok == 0:
        tone = "danger"
        heading = "Échec de la restauration : aucune section n'a été restaurée."
    elif n_fail == 0:
        tone = "success"
        heading = f"Restauration réussie : {n_ok} section(s) restaurée(s)."
    else:
        tone = "warning"
        heading = (
            f"Restauration partielle : {n_ok} section(s) restaurée(s), "
            f"{n_fail} en échec (voir les journaux du serveur)."
        )

    return render_template_string(
        _RESTORE_REPORT_TEMPLATE,
        tone=tone, heading=heading, rows=rows, any_restored=n_ok > 0,
    )


def _section_keys_from_request():
    """Sections demandées via ``?sections=`` (``all`` ou liste séparée par des virgules)."""
    sections_param = request.args.get('sections', 'all')
    if sections_param == 'all':
        return list(BACKUP_SECTIONS.keys())
    return [s.strip() for s in sections_param.split(',') if s.strip()]


def _stream_and_delete(path):
    """Génère le contenu d'un fichier par blocs puis le supprime (streaming)."""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
    finally:
        _safe_unlink(path)


@admin_backup_bp.route('/admin/backup/estimate')
@require_permission('app')
def backup_estimate():
    """Estimation de la taille (images) d'un export, pour avertir l'utilisateur.

    Renvoie du JSON consommé par l'interface pour afficher une estimation et un
    avertissement lorsque l'export serait lourd (point 13)."""
    return jsonify(estimate_export_size(_section_keys_from_request()))


@admin_backup_bp.route('/admin/backup/export')
@require_permission('app')
def backup_export():
    """Export selected sections.

    Format ZIP (3.0) dès qu'une section d'images est incluse — le manifeste et
    les images (fichiers binaires) sont produits en flux via un fichier
    temporaire, sans charger d'énorme JSON base64 en mémoire (point 13). Sinon,
    JSON plat (2.0) rétrocompatible."""
    section_keys = _section_keys_from_request()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if selection_has_binary(section_keys):
        tmp = tempfile.NamedTemporaryFile(prefix="gf_export_", suffix=".zip", delete=False)
        try:
            write_backup_archive(section_keys, tmp)
            tmp.flush()
        except Exception:
            if not tmp.closed:
                tmp.close()
            _safe_unlink(tmp.name)
            current_app.logger.exception("Backup export archive failed")
            return "L'export a échoué. Consultez les journaux du serveur.", 500
        finally:
            if not tmp.closed:
                tmp.close()

        if len(section_keys) == 1:
            filename = f"gf_backup_{section_keys[0]}_{timestamp}.zip"
        else:
            filename = f"gf_backup_{timestamp}.zip"

        # Content-Length calculé avant streaming (le fichier existe encore) ; le
        # générateur supprime le fichier temporaire une fois envoyé.
        size = os.path.getsize(tmp.name)
        return Response(
            stream_with_context(_stream_and_delete(tmp.name)),
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename={filename}',
                'Content-Length': str(size),
            },
        )

    backup_data = export_sections(section_keys)
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
    upload, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)
    try:
        info = preview_backup(upload.manifest)
        return render_template_string(_PREVIEW_TEMPLATE, info=info)
    finally:
        upload.close()


@admin_backup_bp.route('/admin/backup/import', methods=['POST'])
@require_permission('app')
def backup_import():
    """Restore sections from an uploaded backup file."""
    upload, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    try:
        requested = request.form.getlist('restore_sections')
        sections_param = requested if requested else None

        report = restore_sections(upload.manifest, sections_param, archive=upload.archive)

        # Sections effectivement demandées, pour un rapport section par section.
        # Sans sélection explicite, toutes les sections du fichier sont visées.
        if sections_param is None:
            requested = list(upload.manifest.get("sections", []))
    finally:
        upload.close()

    try:
        current_app.load_configuration(current_app)
    except Exception:
        current_app.logger.exception("Backup import: reload configuration failed")

    if report.get("errors"):
        current_app.logger.error(f"Backup import errors: {report.get('errors')}")

    # Erreur fatale (app/format invalide) : pas de rapport section par section.
    if "restored" not in report:
        record_audit(ACTION_RESTORE, "backup", outcome=OUTCOME_FAILURE,
                     details="erreur fatale de restauration")
        try:
            current_app.display_toast(success=False, message=_GENERIC_RESTORE_ERROR)
        except Exception:
            pass
        return _alert("danger", report.get("error") or _GENERIC_RESTORE_ERROR)

    n_ok = len(report.get("restored", []))
    n_total = len(requested)

    record_audit(
        ACTION_RESTORE, "backup",
        outcome=OUTCOME_SUCCESS if n_ok and n_ok == n_total else OUTCOME_FAILURE,
        details=f"{n_ok}/{n_total} section(s): {','.join(report.get('restored', []))}",
    )

    # Toast global : « succès » seulement si TOUTES les sections demandées ont
    # été restaurées ; sinon le caractère partiel (ou l'échec) est explicite.
    if n_ok == 0:
        toast_ok, toast_msg = False, _GENERIC_RESTORE_ERROR
    elif n_ok == n_total:
        toast_ok, toast_msg = True, "Restauration réussie de toutes les sections."
    else:
        toast_ok = False
        toast_msg = f"Restauration partielle : {n_ok}/{n_total} section(s) restaurée(s)."
    try:
        current_app.display_toast(success=toast_ok, message=toast_msg)
    except Exception:
        pass

    return _render_restore_report(report, requested)


@admin_backup_bp.route('/admin/backup/import_multi', methods=['POST'])
@require_permission('app')
def backup_import_multi():
    """Restore multiple sections from an uploaded backup file (for per-page restore with CSS + config)."""
    sections_param = request.form.get('sections', '')
    requested_keys = [s.strip() for s in sections_param.split(',') if s.strip()]
    if not requested_keys:
        return _alert("danger", "Aucune section spécifiée.")

    upload, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    try:
        available = upload.manifest.get("sections", [])
        keys_to_restore = [k for k in requested_keys if k in available and k in BACKUP_SECTIONS]

        if not keys_to_restore:
            return _alert("warning", "Aucune des sections demandées n'est présente dans ce fichier.")

        report = restore_sections(upload.manifest, keys_to_restore, archive=upload.archive)
    finally:
        upload.close()

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
        record_audit(ACTION_RESTORE, "backup",
                     outcome=OUTCOME_SUCCESS if not report.get("errors") else OUTCOME_FAILURE,
                     details=f"sections: {','.join(report.get('restored', []))}")
        try:
            current_app.display_toast(success=True, message=msg)
        except Exception:
            pass
        resp = make_response(_alert("success", msg))
        resp.headers['HX-Refresh'] = 'true'
        return resp

    record_audit(ACTION_RESTORE, "backup", outcome=OUTCOME_FAILURE,
                 details=f"sections demandées: {','.join(keys_to_restore)}")
    return _alert("danger", _GENERIC_RESTORE_ERROR)


@admin_backup_bp.route('/admin/backup/import_single', methods=['POST'])
@require_permission('app')
def backup_import_single():
    """Restore a single section from an uploaded backup file (for per-page restore)."""
    section_key = request.form.get('section')
    if not section_key or section_key not in BACKUP_SECTIONS:
        return _alert("danger", "Section inconnue.")

    upload, error = _read_uploaded_backup()
    if error:
        return _alert("danger", error)

    cls = BACKUP_SECTIONS.get(section_key)
    label = cls.label if cls else section_key

    try:
        if section_key not in upload.manifest.get("sections", []):
            return _alert("danger", f"La section « {label} » est absente de ce fichier.")

        report = restore_sections(upload.manifest, [section_key], archive=upload.archive)
    finally:
        upload.close()

    try:
        current_app.load_configuration(current_app)
    except Exception:
        current_app.logger.exception("Backup import_single: reload configuration failed")

    if report.get("errors"):
        current_app.logger.error(f"Backup import_single errors: {report.get('errors')}")

    if report.get("restored"):
        record_audit(ACTION_RESTORE, "backup", target_id=section_key, outcome=OUTCOME_SUCCESS)
        try:
            current_app.display_toast(success=True, message=f"Restauration de '{label}' réussie")
        except Exception:
            pass
        resp = make_response(_alert("success", f"Restauration de « {label} » réussie."))
        resp.headers['HX-Refresh'] = 'true'
        return resp

    record_audit(ACTION_RESTORE, "backup", target_id=section_key, outcome=OUTCOME_FAILURE)
    return _alert("danger", _GENERIC_RESTORE_ERROR)
