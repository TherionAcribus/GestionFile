from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_advanced_pages_share_the_editor_shell():
    for template in ("announce.html", "patient_page.html", "phone.html"):
        source = _read(f"templates/admin/{template}")
        assert 'class="advanced-editor-page"' in source
        assert 'class="row advanced-editor-workspace"' in source
        assert "Mode avancé" in source
        assert "Éditeur visuel" in source


def test_color_macro_combines_picker_and_editable_css_value():
    source = _read("templates/admin/macros.html")
    assert "data-admin-color-control" in source
    assert 'class="form-control form-control-color admin-color-picker"' in source
    assert 'class="form-control admin-color-value"' in source
    assert "Hexadécimal, couleur CSS" in source


def test_marker_macros_use_compact_accessible_chips():
    source = _read("templates/admin/macros.html")
    assert 'class="variables_calling admin-marker"' in source
    assert 'class="admin-marker-list"' in source
    assert 'aria-label="Insérer {{ token }} : {{ label }}"' in source


def test_color_javascript_validates_and_synchronizes_controls():
    source = _read("static/js/admin_colors.js")
    assert "function cssColorToHex(value)" in source
    assert "CSS.supports('color', value)" in source
    assert "valueInput.value = picker.value.toUpperCase()" in source
    assert "setCustomValidity" in source
    assert "input.value = newValue" in source
    assert "trigger('change.select2')" not in source


def test_visual_editor_can_copy_a_published_palette_into_the_draft():
    source = _read("static/js/page_editor.js")
    assert "state.palette_sources || []" in source
    assert "editor-palette-source" in source
    assert "editor-palette-copy" in source
    assert "payload.css[key] = change.color" in source
    assert "mutate(function ()" in source


def test_preview_toggles_visibility_and_conditions_via_attributes():
    source = _read("static/js/page_editor_preview.js")
    assert "data-page-editor-hidden" in source
    assert "data-config-bool" in source
    assert "data-hide-empty" in source
    assert "page-editor-initial-hidden" in source


def test_editor_keeps_in_flight_edits_and_restore_goes_to_draft():
    source = _read("static/js/page_editor.js")
    assert "let saving = false;" in source
    assert "saveQueued" in source
    assert "canonical(payload) === sent" in source
    assert "dans le brouillon" in source


def test_editor_exposes_local_backup_contrast_and_zone_labels():
    source = _read("static/js/page_editor.js")
    assert "page-editor-backup-" in source
    assert "offerLocalBackup" in source
    assert "appendContrastWarning" in source
    assert "ZONE_LABELS" in source


def test_editor_shows_shared_draft_author_and_date():
    source = _read("static/js/page_editor.js")
    assert "draftStatusText(state)" in source
    assert "state.draft_author" in source
    assert "state.draft_updated_at" in source
    assert "Brouillon partagé — modifié " in source
    assert "toLocaleString('fr-FR')" in source


def test_editor_autosaves_changes_and_supports_ctrl_s():
    source = _read("static/js/page_editor.js")
    assert "let autosaveTimer = null;" in source
    assert "function scheduleAutosave()" in source
    assert "setTimeout(function ()" in source
    assert "clearTimeout(autosaveTimer)" in source
    # L'autosave et le raccourci partagent le traitement d'erreur du bouton.
    assert "key === 's'" in source
    assert "event.preventDefault()" in source
    assert "error.status === 409 ? 'warning' : 'danger'" in source


def test_editor_span_label_tracks_slider_in_real_time():
    source = _read("static/js/page_editor.js")
    assert "querySelector('.form-label')" in source
    assert "'Largeur (' + span.value + '/12)'" in source


def test_editor_history_compares_revisions_and_marks_published():
    """Chaque révision propose « Comparer » (diff publié → révision servi par
    la route dédiée) et la révision en service porte le badge « actuelle »."""
    source = _read("static/js/page_editor.js")
    assert "renderRevisions(state.revisions || [], state.published_revision)" in source
    assert "'/revisions/' + revision + '/diff'" in source
    assert "badge.textContent = 'actuelle'" in source
    assert "compareRevision(revision.revision)" in source


def test_editor_confirmations_use_dialogs_and_zoom_is_a_select():
    """Plus de boîte de confirmation native : ``confirmDialog`` (bâti sur
    ``openDialog``) couvre tous les sites ; le zoom de l'aperçu est un
    select, pas la case « Taille réelle »."""
    source = _read("static/js/page_editor.js")
    assert "function confirmDialog(" in source
    assert "window.confirm" not in source
    assert "realSizeToggle" not in source
    assert "editor-zoom" in source
    html = _read("templates/admin/page_editor.html")
    assert 'id="editor-zoom"' in html
    assert 'id="editor-real-size"' not in html
