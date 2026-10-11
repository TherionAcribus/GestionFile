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


def test_editor_groups_config_fields_and_suffixes_int_units():
    """§3.4/§3.5 : l'inspecteur titre les sous-groupes des champs de contenu
    comme ceux de l'apparence, et suffixe l'unité des champs numériques dans
    un input-group plutôt que dans le libellé."""
    source = _read("static/js/page_editor.js")
    assert "lastConfigGroup" in source
    assert "field.unit" in source
    assert "input-group" in source
    assert "input-group-text" in source
    assert "page-editor-fieldgroup" in source


def test_screens_panel_shows_identity_and_per_screen_reload():
    """§3.8 : chaque écran du panneau affiche IP et agent en plus du nom et
    du sid tronqué, et propose « Recharger » — le sid complet sert au
    ciblage via POST /apply sans être affiché."""
    source = _read("static/js/page_editor.js")
    assert "screen.ip" in source
    assert "screen.user_agent" in source
    assert "String(screen.sid).slice(0, 8)" in source
    assert "'Recharger'" in source
    assert "watchScreenAcks(sid)" in source


def test_editor_offers_factory_resets_and_renamed_published_reset():
    """§3.12 : chaque champ de contenu/apparence propose un retour discret
    à la valeur d'usine (icône Bootstrap, visible seulement en cas
    d'écart) ; au niveau du composant, « version publiée » et « valeurs
    d'usine » sont deux actions distinctes, toutes deux via ``mutate``."""
    source = _read("static/js/page_editor.js")
    assert "state.defaults || null" in source
    assert "function factoryResetButton(section, key)" in source
    assert "factoryResetButton('config', field.key)" in source
    assert "factoryResetButton('css', field.key)" in source
    assert "page-editor-factory-reset" in source
    assert "bi bi-arrow-counterclockwise" in source
    assert "Reprendre la version publiée" in source
    assert "Reprendre les valeurs d’usine" in source
    assert "Réinitialiser ce composant" not in source


def test_theme_export_import_uses_named_format_and_rejects_foreign_page():
    """Export « gf-page-theme » par thème (nom de fichier slugifié) et
    import JSON : le client refuse un fichier d'une autre page, accepte
    enveloppe et instantané brut, et ne repose sur aucune boîte native."""
    source = _read("static/js/page_editor.js")
    assert "'gf-page-theme'" in source
    assert "'theme-' + page + '-' + slugifyThemeName(theme.name) + '.json'" in source
    assert "exportTheme(theme)" in source
    assert "editor-theme-import" in source
    assert "data.page && data.page !== page" in source
    assert "layout: snapshot.layout || {}" in source
    assert "window.prompt" not in source
    server = _read("routes/admin_page_editor.py")
    assert '"defaults": factory_payload(page)' in server
    assert "_complete_payload(page, body.get(\"payload\"))" in server


def test_editor_preview_language_and_translation_review_alert():
    """§4 : la barre de l'aperçu porte un sélecteur de langue relayé par
    ``?lang=`` (serveur et re-rendu markdown de l'iframe) et la publication
    d'un texte traduisible ouvre un dialogue « traductions à revoir » —
    toujours sans boîte native."""
    html = _read("templates/admin/page_editor.html")
    assert 'id="editor-language"' in html
    assert 'id="editor-language-field" hidden' in html
    source = _read("static/js/page_editor.js")
    assert "state.languages" in source
    assert "previewLanguages.length <= 1" in source
    assert "'&lang=' + encodeURIComponent(languageSelect.value" in source
    assert "languageSelect.addEventListener('change', loadPreview)" in source
    assert "result.translation_review" in source
    assert "showTranslationReviewDialog" in source
    assert "texte(s) traduisible(s) modifié(s)" in source
    assert "Ouvrir les traductions" in source
    assert "window.location.assign('/admin/translations')" in source
    assert "window.confirm" not in source
    assert "window.prompt" not in source
    preview_js = _read("static/js/page_editor_preview.js")
    assert "page-editor-preview-translations" in preview_js
    assert "lang: previewLang" in preview_js
    assert "location.search" in preview_js
    preview_html = _read("templates/admin/page_editor_preview.html")
    assert "preview_translations | tojson" in preview_html
    server = _read("routes/admin_page_editor.py")
    assert "_preview_language_code" in server
    assert "_translated_preview_config" in server
    assert "TRANSLATABLE_CONFIG_KEYS" in server
    assert '"translation_review": translation_review' in server
    assert '"languages"' in server
