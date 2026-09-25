from copy import deepcopy
from datetime import datetime

import markdown2
import bleach
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_security import current_user

import config_sync
from audit_log import ACTION_CREATE, ACTION_DELETE, ACTION_RESTORE, ACTION_UPDATE, OUTCOME_FAILURE
from audit_service import record_audit
from communication import communikation
from config import time_tz
from models import (
    Button,
    ConfigOption,
    Counter,
    Language,
    PageEditorRevision,
    PageEditorState,
    PageEditorTheme,
    Pharmacist,
    db,
)
from page_editor import (
    ADAPTERS,
    advanced_disabled_components,
    current_payload,
    enabled_pages,
    get_adapter,
    palette_source_data,
    payload_diff,
    payload_hash,
    public_adapter_data,
    validate_payload,
)
from params_registry import column_values_for, get_spec
from routes.admin_security import permission_error_response, user_has_permission
from utils import balise_values


admin_page_editor_bp = Blueprint("admin_page_editor", __name__)

_DEFAULT_PREVIEW_TOKENS = {
    "{P}": "Pharmacie Démonstration",
    "{D}": "24/09/2026",
    "{H}": "10:30",
    "{A}": "Ordonnances",
    "{N}": "042",
    "{M}": "Camille",
    "{C}": "Comptoir 3",
}


def _first_preview_button():
    return (
        Button.query.filter_by(is_present=True)
        .order_by(Button.sort_order, Button.id)
        .first()
    )


def _preview_tokens():
    values = balise_values()
    button = _first_preview_button()
    counter = (
        Counter.query.filter(Counter.staff_id.isnot(None))
        .order_by(Counter.id)
        .first()
        or Counter.query.order_by(Counter.id).first()
    )
    staff = counter.staff if counter and counter.staff else (
        Pharmacist.query.filter_by(is_active=True).order_by(Pharmacist.id).first()
    )
    values.update({
        "N": "042",
        "A": button.label if button else "Activité",
        "C": counter.name if counter else "Comptoir 1",
        "M": staff.name if staff else "Équipe",
    })
    return {f"{{{key}}}": str(value or "") for key, value in values.items()}


def _demo_text(value, tokens=None):
    result = str(value or "")
    for token, replacement in (tokens or _DEFAULT_PREVIEW_TOKENS).items():
        result = result.replace(token, replacement)
    return result


def _render_phone_markdown(config, tokens=None):
    rendered = {}
    for key, value in config.items():
        if not (key.startswith("phone_line") or key.startswith("phone_your_turn_line")):
            continue
        html = markdown2.markdown(_demo_text(value, tokens), safe_mode="escape")
        rendered[key] = bleach.clean(
            html,
            tags={"p", "br", "strong", "em", "ul", "ol", "li", "a", "code"},
            attributes={"a": ["href", "title"]},
            protocols={"http", "https", "mailto"},
            strip=True,
        )
    return rendered


def _patient_preview_data(scenario, tokens):
    top_level = (
        Button.query.filter_by(is_present=True, parent_button_id=None)
        .order_by(Button.sort_order, Button.id)
        .all()
    )
    buttons = top_level
    children = False
    if scenario == "children":
        parent = next(
            (
                button for button in top_level
                if button.is_parent and any(child.is_present for child in button.dependent_buttons)
            ),
            None,
        )
        if parent is not None:
            buttons = sorted(
                (child for child in parent.dependent_buttons if child.is_present),
                key=lambda child: (child.sort_order, child.id),
            )
            children = True

    empty_parent_ids = {
        button.id for button in buttons
        if button.is_parent and not any(child.is_present for child in button.dependent_buttons)
    }
    selected_button = next(
        (button for button in buttons if button.activity is not None),
        next((button for button in top_level if button.activity is not None), None),
    )
    return {
        "preview_buttons": buttons,
        "preview_buttons_children": children,
        "preview_buttons_max_length": 2 if buttons and buttons[0].shape == "square" else 4,
        "preview_empty_parent_ids": empty_parent_ids,
        "preview_languages": (
            Language.query.filter_by(is_active=True)
            .order_by(Language.sort_order, Language.id)
            .all()
        ),
        "preview_activity_label": selected_button.label if selected_button else "Activité",
        "preview_specific_message": (
            selected_button.activity.specific_message
            if selected_button and selected_button.activity else ""
        ),
        "preview_patient_interface": {
            "validate_print": current_app.config.get(
                "PAGE_PATIENT_INTERFACE_VALIDATE_PRINT", "Imprimer mon ticket"
            ),
            "validate_scan": current_app.config.get(
                "PAGE_PATIENT_INTERFACE_VALIDATE_SCAN", "Scanner le QR code"
            ),
            "validate_cancel": current_app.config.get(
                "PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL", "Annuler"
            ),
            "done_back": current_app.config.get(
                "PAGE_PATIENT_INTERFACE_DONE_BACK", "Terminer"
            ),
            "confirmation": _demo_text(
                current_app.config.get(
                    "PAGE_PATIENT_CONFIRMATION_MESSAGE", "Votre numéro : {N}"
                ),
                tokens,
            ),
        },
    }


def _page_context(page, *, api):
    adapter = get_adapter(page)
    if adapter is None:
        if api:
            return None, (jsonify({"error": "Page inconnue."}), 404)
        abort(404)
    refusal = permission_error_response(adapter["permission"], api=api)
    return adapter, refusal


def _locked_state(page, *, create=False):
    state = (
        PageEditorState.query.with_for_update()
        .filter_by(page_key=page)
        .first()
    )
    if state is None and create:
        state = PageEditorState(page_key=page)
        db.session.add(state)
        db.session.flush()
    return state


def _revision_document(revision):
    return {
        "revision": revision.revision,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
        "author": revision.published_by.username if revision.published_by else None,
    }


def _state_document(page, adapter):
    state = PageEditorState.query.filter_by(page_key=page).first()
    published = current_payload(page)
    draft = deepcopy(state.draft_json) if state and state.draft_json else None
    revisions = (
        PageEditorRevision.query.filter_by(page_key=page)
        .order_by(PageEditorRevision.revision.desc())
        .limit(10)
        .all()
    )
    return {
        "adapter": public_adapter_data(page),
        "published": published,
        "draft": draft,
        "disabled_components": advanced_disabled_components(page),
        "draft_version": state.draft_version if state else 0,
        "draft_updated_at": (
            state.draft_updated_at.isoformat()
            if state and state.draft_updated_at else None
        ),
        "draft_author": (
            state.draft_updated_by.username
            if state and state.draft_updated_by else None
        ),
        "published_revision": state.published_revision if state else 0,
        "revisions": [_revision_document(item) for item in revisions],
        "palette_sources": [
            palette_source_data(source_page)
            for source_page in sorted(enabled_pages())
            if source_page != page
            and source_page in ADAPTERS
            and user_has_permission(current_user, ADAPTERS[source_page]["permission"])
        ],
    }


def _stage_config(key, value):
    option = ConfigOption.query.filter_by(config_key=key).first()
    if option is None:
        option = ConfigOption(config_key=key)
        db.session.add(option)
    for column, column_value in column_values_for(key, value).items():
        setattr(option, column, column_value)


def _publish(page, adapter, state, payload, *, require_base_match):
    normalized = validate_payload(page, payload)
    current = current_payload(page)
    if require_base_match and state.draft_base_hash != payload_hash(current):
        return None, "La configuration avancée a changé depuis la création du brouillon."

    for key, value in normalized["config"].items():
        _stage_config(key, value)
    _stage_config(adapter["layout_key"], normalized["layout"])

    css_manager = current_app.css_variable_manager
    for key, value in normalized["css"].items():
        css_manager.stage_variable(adapter["css_source"], key, value)

    normalized["base_hash"] = payload_hash(normalized)
    revision_number = state.published_revision + 1
    revision = PageEditorRevision(
        page_key=page,
        revision=revision_number,
        snapshot_json=deepcopy(normalized),
        published_by_id=current_user.id,
    )
    db.session.add(revision)

    state.published_revision = revision_number
    state.published_by_id = current_user.id
    state.published_at = datetime.now(time_tz)
    state.draft_json = None
    state.draft_base_hash = None
    state.draft_version += 1
    state.draft_updated_by_id = None
    state.draft_updated_at = None
    config_sync.bump_generation(db=db, ConfigOption=ConfigOption)
    db.session.flush()

    obsolete = (
        PageEditorRevision.query.filter_by(page_key=page)
        .order_by(PageEditorRevision.revision.desc())
        .offset(10)
        .all()
    )
    for old_revision in obsolete:
        db.session.delete(old_revision)

    db.session.commit()

    try:
        for key, value in normalized["config"].items():
            current_app.config[get_spec(key).config_name] = value
        current_app.config[adapter["config_name"]] = deepcopy(normalized["layout"])
        for key, value in normalized["css"].items():
            css_manager.set_cached_variable(adapter["css_source"], key, value)
        current_app.css_manager.generate_css(
            css_manager.get_all_variables(adapter["css_source"]),
            mode=adapter["css_source"],
        )
    except Exception:
        current_app.logger.exception(
            "Publication persistée mais rafraîchissement du cache local impossible (%s)",
            page,
        )
    return revision_number, None


@admin_page_editor_bp.get("/admin/page-editor/<page>")
def editor(page):
    adapter, refusal = _page_context(page, api=False)
    if refusal is not None:
        return refusal
    return render_template(
        "admin/page_editor.html",
        page=page,
        page_label=adapter["label"],
    )


@admin_page_editor_bp.get("/admin/page-editor/<page>/state")
def state(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    return jsonify(_state_document(page, adapter))


@admin_page_editor_bp.put("/admin/page-editor/<page>/draft")
def save_draft(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("draft_version"), int):
        return jsonify({"error": "Requête de brouillon invalide."}), 400
    try:
        normalized = validate_payload(page, body.get("payload"))
        state_row = _locked_state(page, create=True)
        if body["draft_version"] != state_row.draft_version:
            db.session.rollback()
            return jsonify({"error": "Le brouillon a été modifié par un autre utilisateur."}), 409

        # L'empreinte de base reflète la configuration que l'utilisateur avait
        # sous les yeux : pour un premier brouillon c'est celle envoyée par le
        # client (le "published" chargé dans l'éditeur) ; ensuite on conserve
        # celle du brouillon partagé. Écraser systématiquement avec l'état
        # courant masquait un changement fait en mode avancé entre l'ouverture
        # de l'éditeur et la première sauvegarde.
        if state_row.draft_json is not None:
            base_hash = state_row.draft_base_hash or payload_hash(current_payload(page))
        else:
            base_hash = normalized["base_hash"]
        normalized["base_hash"] = base_hash
        state_row.draft_json = normalized
        state_row.draft_base_hash = base_hash
        state_row.draft_version += 1
        state_row.draft_updated_by_id = current_user.id
        state_row.draft_updated_at = datetime.now(time_tz)
        db.session.commit()
        return jsonify({
            "success": True,
            "draft": normalized,
            "draft_version": state_row.draft_version,
        })
    except ValueError as error:
        db.session.rollback()
        return jsonify({"error": str(error)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Enregistrement du brouillon visuel impossible (%s)", page)
        return jsonify({"error": "Enregistrement du brouillon impossible."}), 500


@admin_page_editor_bp.delete("/admin/page-editor/<page>/draft")
def delete_draft(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        state_row = _locked_state(page)
        if state_row is None:
            return jsonify({"success": True, "draft_version": 0})
        expected = body.get("draft_version")
        if expected is not None and expected != state_row.draft_version:
            db.session.rollback()
            return jsonify({"error": "Le brouillon a été modifié par un autre utilisateur."}), 409
        state_row.draft_json = None
        state_row.draft_base_hash = None
        state_row.draft_version += 1
        state_row.draft_updated_by_id = None
        state_row.draft_updated_at = None
        db.session.commit()
        record_audit(ACTION_DELETE, "page_design_draft", target_id=page)
        return jsonify({"success": True, "draft_version": state_row.draft_version})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Suppression du brouillon visuel impossible (%s)", page)
        return jsonify({"error": "Suppression du brouillon impossible."}), 500


@admin_page_editor_bp.get("/admin/page-editor/<page>/preview")
def preview(page):
    adapter, refusal = _page_context(page, api=False)
    if refusal is not None:
        return refusal
    scenario_ids = {item["id"] for item in adapter["scenarios"]}
    scenario = request.args.get("scenario", adapter["scenarios"][0]["id"])
    if scenario not in scenario_ids:
        scenario = adapter["scenarios"][0]["id"]
    state_row = PageEditorState.query.filter_by(page_key=page).first()
    payload = (
        deepcopy(state_row.draft_json)
        if state_row and state_row.draft_json else current_payload(page)
    )
    tokens = _preview_tokens()
    preview_config = {
        key: _demo_text(value, tokens) if isinstance(value, str) else value
        for key, value in payload["config"].items()
    }
    phone_html = {}
    if page == "phone":
        phone_html = _render_phone_markdown(payload["config"], tokens)
    patient_preview = _patient_preview_data(scenario, tokens) if page == "patient" else {}
    second_call_tokens = dict(tokens)
    second_call_tokens["{N}"] = "057"
    preview_call_texts = [
        _demo_text(payload["config"].get("announce_call_text", ""), tokens),
        _demo_text(payload["config"].get("announce_call_text", ""), second_call_tokens),
    ]
    preview_button = _first_preview_button()
    announce_flags = {}
    if page == "announce":
        announce_flags = {
            "ongoing": bool(current_app.config.get(
                get_spec("announce_ongoing_display").config_name, True)),
            "next": bool(current_app.config.get(
                get_spec("announce_next_patients_display").config_name, True)),
            "text_up": current_app.config.get(
                get_spec("announce_text_up_patients_display").config_name) != "never",
            "text_down": current_app.config.get(
                get_spec("announce_text_down_patients_display").config_name) != "never",
        }
    hidden_components = [
        component_id
        for component_id, item in payload["layout"].items()
        if not item.get("visible", True)
    ]
    return render_template(
        "admin/page_editor_preview.html",
        page=page,
        scenario=scenario,
        payload=payload,
        preview_config=preview_config,
        preview_announce_flags=announce_flags,
        preview_hidden_components=hidden_components,
        preview_phone_center=bool(current_app.config.get("PHONE_CENTER", False)),
        preview_tokens=tokens,
        preview_call_texts=preview_call_texts,
        preview_phone_specific_message=(
            preview_button.activity.specific_message
            if preview_button and preview_button.activity else ""
        ),
        preview_phone_display_specific_message=current_app.config.get(
            "PHONE_DISPLAY_SPECIFIC_MESSAGE", True
        ),
        adapter=public_adapter_data(page),
        phone_confirmation_lines=[phone_html.get(f"phone_line{index}", "") for index in range(1, 7)],
        phone_your_turn_lines=[phone_html.get(f"phone_your_turn_line{index}", "") for index in range(1, 7)],
        **patient_preview,
    )


@admin_page_editor_bp.post("/admin/page-editor/<page>/preview/render")
def render_preview_content(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    if page != "phone":
        return jsonify({"html": {}})
    body = request.get_json(silent=True) or {}
    try:
        normalized = validate_payload(page, body.get("payload"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"html": _render_phone_markdown(normalized["config"], _preview_tokens())})


@admin_page_editor_bp.post("/admin/page-editor/<page>/diff")
def diff(page):
    """Compare le payload envoyé à la version publiée (panneau « Comparer »
    et récapitulatif avant publication)."""
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        normalized = validate_payload(page, body.get("payload"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(payload_diff(page, normalized))


@admin_page_editor_bp.post("/admin/page-editor/<page>/publish")
def publish(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        state_row = _locked_state(page)
        if state_row is None or state_row.draft_json is None:
            return jsonify({"error": "Aucun brouillon à publier."}), 400
        if body.get("draft_version") != state_row.draft_version:
            db.session.rollback()
            return jsonify({"error": "Le brouillon a été modifié par un autre utilisateur."}), 409
        revision, conflict = _publish(
            page, adapter, state_row, state_row.draft_json,
            require_base_match=True,
        )
        if conflict:
            db.session.rollback()
            return jsonify({"error": conflict}), 409
        record_audit(ACTION_UPDATE, "page_design", target_id=page,
                     details=f"revision={revision}")
        return jsonify({"success": True, "revision": revision,
                        "draft_version": state_row.draft_version})
    except ValueError as error:
        db.session.rollback()
        return jsonify({"error": str(error)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Publication visuelle impossible (%s)", page)
        record_audit(ACTION_UPDATE, "page_design", target_id=page,
                     outcome=OUTCOME_FAILURE)
        return jsonify({"error": "Publication impossible."}), 500


def _theme_document(theme):
    return {
        "id": theme.id,
        "name": theme.name,
        "description": theme.description or "",
        "updated_at": theme.updated_at.isoformat() if theme.updated_at else None,
        "author": theme.created_by.username if theme.created_by else None,
        "snapshot": deepcopy(theme.snapshot_json),
    }


@admin_page_editor_bp.get("/admin/page-editor/<page>/themes")
def list_themes(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    themes = (
        PageEditorTheme.query.filter_by(page_key=page)
        .order_by(PageEditorTheme.name)
        .all()
    )
    return jsonify({"themes": [_theme_document(theme) for theme in themes]})


@admin_page_editor_bp.post("/admin/page-editor/<page>/themes")
def save_theme(page):
    """Enregistre le brouillon courant comme thème nommé réutilisable.

    Le snapshot est normalisé par ``validate_payload`` : un thème ne peut donc
    pas contenir de valeur qui serait refusée à la publication.
    """
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify({"error": "Le nom du thème doit faire entre 1 et 80 caractères."}), 400
    description = str(body.get("description") or "").strip()[:300]
    try:
        normalized = validate_payload(page, body.get("payload"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        theme = PageEditorTheme.query.filter_by(page_key=page, name=name).first()
        if theme is not None and not body.get("overwrite"):
            return jsonify({"error": "Un thème porte déjà ce nom.", "exists": True}), 409
        if theme is None:
            theme = PageEditorTheme(page_key=page, name=name)
            db.session.add(theme)
        theme.description = description
        theme.snapshot_json = normalized
        theme.created_by_id = current_user.id
        theme.created_at = theme.created_at or datetime.now(time_tz)
        theme.updated_at = datetime.now(time_tz)
        db.session.commit()
        record_audit(ACTION_CREATE, "page_design_theme", target_id=page,
                     details=f"theme={name}")
        return jsonify({"success": True, "theme": _theme_document(theme)})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Enregistrement du thème impossible (%s)", page)
        return jsonify({"error": "Enregistrement du thème impossible."}), 500


@admin_page_editor_bp.delete("/admin/page-editor/<page>/themes/<int:theme_id>")
def delete_theme(page, theme_id):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    try:
        theme = PageEditorTheme.query.filter_by(id=theme_id, page_key=page).first()
        if theme is None:
            return jsonify({"error": "Thème inconnu."}), 404
        name = theme.name
        db.session.delete(theme)
        db.session.commit()
        record_audit(ACTION_DELETE, "page_design_theme", target_id=page,
                     details=f"theme={name}")
        return jsonify({"success": True})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Suppression du thème impossible (%s)", page)
        return jsonify({"error": "Suppression du thème impossible."}), 500


@admin_page_editor_bp.post("/admin/page-editor/<page>/revisions/<int:revision>/restore")
def restore(page, revision):
    """Charge une ancienne révision dans le brouillon (sans publier).

    Publier directement la révision supprimait le brouillon partagé en cours
    sans avertissement : la restauration produit un brouillon que
    l'utilisateur prévisualise puis publie explicitement.
    """
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        state_row = _locked_state(page, create=True)
        source = PageEditorRevision.query.filter_by(
            page_key=page, revision=revision
        ).first()
        if source is None:
            db.session.rollback()
            return jsonify({"error": "Révision inconnue."}), 404
        expected = body.get("draft_version")
        if state_row.draft_json is not None and expected != state_row.draft_version:
            db.session.rollback()
            return jsonify({"error": "Le brouillon a été modifié par un autre utilisateur."}), 409
        restored_payload = deepcopy(source.snapshot_json)
        restored_payload["base_hash"] = payload_hash(current_payload(page))
        state_row.draft_json = restored_payload
        state_row.draft_base_hash = restored_payload["base_hash"]
        state_row.draft_version += 1
        state_row.draft_updated_by_id = current_user.id
        state_row.draft_updated_at = datetime.now(time_tz)
        db.session.commit()
        record_audit(ACTION_RESTORE, "page_design", target_id=page,
                     details=f"source={revision} -> brouillon")
        return jsonify({"success": True, "revision": revision,
                        "draft_version": state_row.draft_version})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Restauration visuelle impossible (%s)", page)
        record_audit(ACTION_RESTORE, "page_design", target_id=page,
                     outcome=OUTCOME_FAILURE)
        return jsonify({"error": "Restauration impossible."}), 500


@admin_page_editor_bp.get("/admin/page-editor/<page>/screens")
def screens(page):
    """Écrans connectés à la page et révision qu'ils déclarent afficher.

    Un écran connecté mais muet (client d'une version antérieure sans émission
    d'accusé) apparaît avec ``revision: null`` et le statut ``pending``.
    """
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    from sockets import screen_status_for
    state_row = PageEditorState.query.filter_by(page_key=page).first()
    published_revision = state_row.published_revision if state_row else 0
    items = []
    for screen in screen_status_for(page):
        revision = screen["revision"]
        items.append({
            **screen,
            "status": (
                "pending" if revision is None
                else "current" if revision >= published_revision
                else "stale"
            ),
        })
    return jsonify({
        "page": page,
        "published_revision": published_revision,
        "screens": items,
    })


@admin_page_editor_bp.post("/admin/page-editor/<page>/apply")
def apply_to_screens(page):
    adapter, refusal = _page_context(page, api=True)
    if refusal is not None:
        return refusal
    if page == "announce":
        communikation("update_screen", event="refresh")
    elif page == "patient":
        communikation("patient", event="refresh")
    else:
        communikation("phone", event="refresh")
    record_audit(ACTION_UPDATE, "page_design_screens", target_id=page)
    return jsonify({"success": True})
