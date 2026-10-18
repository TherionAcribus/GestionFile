import json
import re
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager

from css_manager import CSSManager
from models import (
    Activity, ActivitySchedule, Button, ConfigOption, PageEditorRevision,
    PageEditorState, PageEditorTheme, Role, User, db,
)
from page_editor import (
    ADAPTERS,
    _PARENT_CSS_REPRESENTATIVES,
    _normalize_css_value,
    complete_config,
    complete_css,
    complete_layout,
    current_payload,
    default_layout,
    factory_payload,
    layout_style,
    palette_source_data,
    payload_hash,
    public_adapter_data,
    validate_payload,
)
from params_registry import get_spec
from routes.admin_page_editor import (
    _draft_base_reference,
    _patient_preview_data,
    _preview_tokens,
    _render_phone_markdown,
    admin_page_editor_bp,
)
from routes.admin_patient import _sync_patient_button_shape_mode
from routes.admin_config import (
    COLOR_PAGE_ROLES,
    admin_config_bp,
    authorize_config_change,
)


@pytest.fixture(autouse=True)
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


def make_payload(page):
    adapter = ADAPTERS[page]
    config = {}
    css = {}
    for component in adapter["components"].values():
        for field in component["config"]:
            spec = get_spec(field["key"])
            if spec.value_type == "value_bool":
                config[field["key"]] = False
            elif spec.value_type == "value_int":
                # Respecte le minimum déclaré (ex. largeur de galerie ≥ 50).
                config[field["key"]] = max(5, field.get("min") or 0)
            elif spec.allowed_values:
                config[field["key"]] = sorted(spec.allowed_values)[0]
            elif field.get("type") == "image":
                # Nom de fichier simple attendu (pas de chemin).
                config[field["key"]] = "image.png"
            else:
                config[field["key"]] = "Texte de démonstration"
        for field in component["css"]:
            css[field["key"]] = (
                "#008b8b" if field["type"] == "color"
                else "400" if field["type"] == "number"
                else "32px"
            )
    payload = {
        "schema_version": 1,
        "page": page,
        "base_hash": "0" * 64,
        "layout": default_layout(page),
        "config": config,
        "css": css,
    }
    payload["base_hash"] = payload_hash(payload)
    return payload


def _normalized_css(page, css):
    """Variables d'apparence passées par la normalisation de
    ``validate_payload`` (préfixe ``#``, casse hex en minuscules) — pour
    fusionner dans un payload de test un instantané de thème écrit en
    majuscules, comme le ferait une sauvegarde de brouillon."""
    field_types = {
        field["key"]: field["type"]
        for component in ADAPTERS[page]["components"].values()
        for field in component["css"]
    }
    return {
        key: _normalize_css_value(value, field_types[key])
        for key, value in css.items()
    }


class DummyCssVariableManager:
    def __init__(self, values):
        self.values = values

    def get_variable(self, source, key):
        return self.values[source][key]

    def stage_variable(self, source, key, value):
        self.values[source][key] = value

    def set_cached_variable(self, source, key, value):
        self.values[source][key] = value

    def get_all_variables(self, source):
        return self.values[source]


class DummyCssManager:
    def generate_css(self, variables, mode="patient"):
        return "/static/css/custom.css"


@pytest.fixture
def editor_app():
    app = Flask("page-editor-api")
    app.config.update(
        SECRET_KEY="test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PAGE_EDITOR_ENABLED_PAGES="announce,patient,phone",
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    css_values = {name: {} for name in ADAPTERS}
    for page_name, adapter in ADAPTERS.items():
        initial = make_payload(page_name)
        for key, value in initial["config"].items():
            app.config[get_spec(key).config_name] = value
        app.config[adapter["config_name"]] = deepcopy(initial["layout"])
        css_values[adapter["css_source"]].update(initial["css"])
    for definition in COLOR_PAGE_ROLES.values():
        source_values = css_values[definition["source"]]
        for role, variable in definition["roles"].items():
            source_values.setdefault(variable, {
                "main": "#008B8B",
                "secondary": "#B6F5F5",
                "text": "#FFFFFF",
                "border": "#000000",
            }[role])
    app.css_variable_manager = DummyCssVariableManager(css_values)
    app.css_manager = DummyCssManager()
    app.register_blueprint(admin_page_editor_bp)
    app.register_blueprint(admin_config_bp)

    with app.app_context():
        # L'extension db est partagée par toute la suite et peut conserver des
        # métadonnées de binds ajoutées par d'autres modules de tests.
        db.create_all(bind_key=None)
        role = Role(
            name="page-editor",
            admin_announce=True,
            admin_patient=True,
            admin_phone=True,
        )
        user = User(username="editor", password="unused", active=True)
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    yield app, user_id

    with app.app_context():
        db.session.remove()
        db.drop_all(bind_key=None)


def authenticated_client(editor_app):
    app, user_id = editor_app
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_preview_uses_real_pharmacy_and_button_configuration(editor_app):
    app, _ = editor_app
    with app.app_context():
        app.config["PHARMACY_NAME"] = "Pharmacie du Centre"
        activity = Activity(name="Ordonnances", letter="O", specific_message="Préparez votre ordonnance")
        button = Button(
            label="Déposer une ordonnance",
            activity=activity,
            is_present=True,
            is_active=True,
            shape="square",
            sort_order=1,
        )
        db.session.add_all([activity, button])
        db.session.commit()

        tokens = _preview_tokens()
        preview = _patient_preview_data("home", tokens)

        assert tokens["{P}"] == "Pharmacie du Centre"
        assert tokens["{A}"] == "Déposer une ordonnance"
        assert preview["preview_buttons"] == [button]
        assert preview["preview_activity_label"] == "Déposer une ordonnance"
        assert preview["preview_specific_message"] == "Préparez votre ordonnance"


@pytest.mark.parametrize("page", ["announce", "patient", "phone"])
def test_each_adapter_accepts_its_complete_payload(page):
    payload = make_payload(page)
    assert validate_payload(page, payload) == payload


@pytest.mark.parametrize("page", ["announce", "patient", "phone"])
def test_palette_only_groups_registered_color_variables(page):
    adapter = ADAPTERS[page]
    registered_colors = {
        field["key"]
        for component in adapter["components"].values()
        for field in component["css"]
        if field["type"] == "color"
    }
    palette = public_adapter_data(page)["palette"]
    keys = {role["id"]: set(role["keys"]) for role in palette}
    assert set(keys) <= {"primary", "surface", "secondary", "border", "accent"}
    assert {"primary", "surface", "secondary", "border"} <= set(keys)
    assert all(keys[role] for role in keys)
    assert all(keys[role] <= registered_colors for role in keys)

    # Les rôles sont portés par le champ « role » des variables, pas devinés
    # par le nom de clé : les fonds de page et zones claires relèvent de
    # « surface », les textes de « secondary », les contours de « border ».
    assert f"{page}_secondary_color" in keys["surface"]
    assert all(key.endswith("_border_color") for key in keys["border"])
    assert all(
        "font_color" in key or "text_color" in key for key in keys["secondary"]
    )
    for component in adapter["components"].values():
        for field in component["css"]:
            if field["type"] == "color":
                assert (field.get("role") or None) == next(
                    (role for role, role_keys in keys.items() if field["key"] in role_keys),
                    None,
                )


def test_palette_excludes_transparent_button_text_backgrounds():
    """Les fonds de texte des boutons patient (``transparent`` par défaut)
    n'appartiennent à aucun rôle : appliquer la « Couleur principale » y
    poserait un pavé coloré derrière chaque libellé."""
    keys = {
        role["id"]: set(role["keys"])
        for role in public_adapter_data("patient")["palette"]
    }
    for key in (
        "circle_button_text_background_color",
        "square_button_text_background_color",
        "square_cancel_button_text_background_color",
        "validation_button_text_background_color",
    ):
        assert all(key not in role_keys for role_keys in keys.values())
    # Les vrais fonds de boutons relèvent bien de « primary » et les zones
    # claires du fond de page de « surface ».
    assert {
        "patient_title_background_color",
        "circle_button_color",
        "square_button_color",
        "square_cancel_button_color",
        "validation_button_color",
    } <= keys["primary"]
    assert {
        "patient_secondary_color",
        "validation_text_background_color",
        "confirmation_text_background_color",
        "scan_explanation_background_color",
        "print_error_background_color",
    } <= keys["surface"]


@pytest.mark.parametrize("page", ["announce", "patient", "phone"])
def test_contrast_pairs_reference_declared_color_fields(page):
    """Chaque paire de contraste pointe vers des champs ``color`` déclarés de
    la page : une clé de texte et une clé de fond ou de surface."""
    adapter = ADAPTERS[page]
    color_fields = {
        field["key"]
        for component in adapter["components"].values()
        for field in component["css"]
        if field["type"] == "color"
    }
    # Une clé de fond est un champ « background » ou une surface/fill de la
    # palette (rôles « surface » et « primary » couvrent les boutons).
    fill_keys = {
        field["key"]
        for component in adapter["components"].values()
        for field in component["css"]
        if field["type"] == "color" and field.get("role") in {"surface", "primary"}
    }
    for component in adapter["components"].values():
        for font_key, background_key in component["contrast_pairs"]:
            assert font_key in color_fields
            assert background_key in color_fields
            assert "font_color" in font_key or "text_color" in font_key
            assert "background" in background_key or background_key in fill_keys


def test_palette_source_contains_only_published_role_values(editor_app):
    app, _ = editor_app
    with app.app_context():
        palette = public_adapter_data("patient")["palette"]
        expected = {}
        for index, role in enumerate(palette):
            expected[role["id"]] = f"#12345{index}"
            for key in role["keys"]:
                app.css_variable_manager.values["patient"][key] = expected[role["id"]]
        source = palette_source_data("patient")

    assert source["page"] == "patient"
    for role in source["roles"]:
        assert role["values"] == [expected[role["id"]]] * len(role["values"])
    assert "config" not in source


def test_editor_state_exposes_only_authorized_palette_sources(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    response = client.get("/admin/page-editor/announce/state")
    assert response.status_code == 200
    assert {source["page"] for source in response.get_json()["palette_sources"]} == {
        "patient", "phone"
    }

    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        role.admin_phone = False
        db.session.commit()
    filtered = client.get("/admin/page-editor/announce/state")
    assert {source["page"] for source in filtered.get_json()["palette_sources"]} == {"patient"}


def _advanced_palette_request(source_page="patient", target_page="announce"):
    source = COLOR_PAGE_ROLES[source_page]
    target = COLOR_PAGE_ROLES[target_page]
    mappings = []
    for role in source["roles"].keys() & target["roles"].keys():
        target_var = target["roles"][role]
        dependency = next(
            (
                key for key in ADAPTERS[target_page]["components"]["title"]["css"]
                if key["type"] == "color" and role in {
                    "main": "background",
                    "text": "font",
                    "border": "border",
                } and {
                    "main": "background",
                    "text": "font",
                    "border": "border",
                }[role] in key["key"]
            ),
            None,
        )
        mappings.append({
            "source_var": source["roles"][role],
            "target_var": target_var,
            "source_source": source["source"],
            "target_source": target["source"],
            "dependencies": [dependency["key"]] if dependency else [],
        })
    return {
        "source_page": source_page,
        "target_page": target_page,
        "mappings": mappings,
    }


def test_advanced_palette_copy_updates_registered_roles_atomically(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    source_colors = {
        "main": "#112233",
        "secondary": "#223344",
        "text": "#334455",
        "border": "#445566",
    }
    with app.app_context():
        for role, value in source_colors.items():
            variable = COLOR_PAGE_ROLES["patient"]["roles"][role]
            app.css_variable_manager.values["patient"][variable] = value

    request_body = _advanced_palette_request()
    response = client.post("/admin/copy_colors", json=request_body)
    assert response.status_code == 200

    with app.app_context():
        for role, expected in source_colors.items():
            target_var = COLOR_PAGE_ROLES["announce"]["roles"][role]
            assert app.css_variable_manager.values["announce"][target_var] == expected
        for mapping in request_body["mappings"]:
            for dependency in mapping["dependencies"]:
                assert app.css_variable_manager.values["announce"][dependency] == (
                    app.css_variable_manager.values["patient"][mapping["source_var"]]
                )


def test_advanced_palette_copy_rejects_forged_mapping(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    request_body = _advanced_palette_request()
    request_body["mappings"][0]["source_var"] = "patient_title_font_size"
    response = client.post("/admin/copy_colors", json=request_body)
    assert response.status_code == 400
    assert "interdite" in response.get_json()["message"]


def test_advanced_palette_copy_requires_source_permission(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        role.admin_patient = False
        db.session.commit()

    response = client.post("/admin/copy_colors", json=_advanced_palette_request())
    assert response.status_code == 403


def test_advanced_client_palette_registry_matches_server():
    javascript = (Path(__file__).resolve().parents[1] / "static/js/admin_colors.js").read_text(
        encoding="utf-8"
    )
    for page, definition in COLOR_PAGE_ROLES.items():
        assert f"'{page}':" in javascript
        for variable in definition["roles"].values():
            assert f"'{variable}'" in javascript


@pytest.mark.parametrize(
    ("page", "key", "expected"),
    [
        ("announce", "announce_title", ["{P}", "{D}", "{H}"]),
        ("announce", "announce_call_text", ["{P}", "{N}", "{A}", "{M}", "{C}", "{D}", "{H}"]),
        ("patient", "page_patient_title", ["{P}", "{D}", "{H}"]),
        ("phone", "phone_line1", ["{P}", "{N}", "{A}", "{D}", "{H}"]),
    ],
)
def test_text_fields_expose_only_their_allowed_markers(page, key, expected):
    components = public_adapter_data(page)["components"]
    field = next(
        field
        for component in components.values()
        for field in component["config"]
        if field["key"] == key
    )
    assert [marker["token"] for marker in field["markers"]] == expected


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["config"].update({"unknown_key": "x"}),
        lambda payload: payload["css"].update({"unknown_variable": "red"}),
        lambda payload: payload["layout"].update({"unknown_component": {}}),
        lambda payload: payload["layout"]["title"].update({"position": "absolute"}),
    ],
)
def test_schema_rejects_unknown_keys_and_components(mutation):
    payload = make_payload("announce")
    mutation(payload)
    with pytest.raises(ValueError):
        validate_payload("announce", payload)


def test_schema_rejects_html_and_css_injection():
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "<img src=x onerror=alert(1)>"
    with pytest.raises(ValueError, match="HTML"):
        validate_payload("announce", payload)

    payload = make_payload("announce")
    payload["css"]["title_font_color"] = "red; background:url(https://example.test/x)"
    with pytest.raises(ValueError, match="CSS"):
        validate_payload("announce", payload)


def test_legacy_hex_color_is_normalized():
    payload = make_payload("announce")
    payload["css"]["calling_background_color"] = "008B8B"
    normalized = validate_payload("announce", payload)
    assert normalized["css"]["calling_background_color"] == "#008b8b"

    # La casse hexadécimale est normalisée : une couleur saisie en
    # majuscules et la même en minuscules ne doivent pas ressortir comme
    # un changement dans le diff.
    payload = make_payload("announce")
    payload["css"]["calling_background_color"] = "#008B8B"
    normalized = validate_payload("announce", payload)
    assert normalized["css"]["calling_background_color"] == "#008b8b"

def test_phone_markdown_preview_is_sanitized():
    html = _render_phone_markdown({
        "phone_line1": "**Numéro {N}** [lien](javascript:alert(1))"
    })["phone_line1"]
    assert "<strong>Numéro 042</strong>" in html
    assert "javascript:" not in html


# --- Téléphone : écrans d'attente, de fin de parcours et d'activité fermée ---

def test_phone_adapter_covers_waiting_ended_and_closed_screens():
    adapter = ADAPTERS["phone"]
    assert [item["id"] for item in adapter["scenarios"]] == [
        "waiting", "confirmation", "your-turn", "ended", "closed"]
    prefixes = {
        "waiting_text": ("#phone_waiting", "waiting", "phone_waiting"),
        "journey_end": ("#phone_journey_end", "ended", "phone_journey_end"),
        "closed": ("#phone_closed", "closed", "phone_closed"),
    }
    for component_id, (selector, scenario, prefix) in prefixes.items():
        component = adapter["components"][component_id]
        assert component["selector"] == selector
        assert component["scenarios"] == [scenario]
        assert component["layout_controls"] == ["visible"]
        assert {field["key"] for field in component["css"]} == {
            f"{prefix}_{suffix}" for suffix in (
                "font_size", "font_color", "font_weight", "border_size",
                "border_color", "background_color", "background_height")}


def test_phone_waiting_and_terminal_keys_validate():
    """Le payload téléphone complet porte les nouvelles clés de contenu et
    d'apparence ; les textes d'attente acceptent {N} mais refusent {X}."""
    payload = make_payload("phone")
    for key in ("phone_waiting_line1", "phone_waiting_line2",
                "phone_journey_end_message",
                "page_patient_disable_default_message"):
        assert key in payload["config"]
    for prefix in ("phone_waiting", "phone_journey_end", "phone_closed"):
        for suffix in ("font_size", "font_color", "font_weight", "border_size",
                       "border_color", "background_color", "background_height"):
            assert f"{prefix}_{suffix}" in payload["css"]
    assert validate_payload("phone", payload) == payload

    payload["config"]["phone_waiting_line1"] = "Numéro {N} à la {P}"
    assert validate_payload("phone", payload)["config"]["phone_waiting_line1"] \
        == "Numéro {N} à la {P}"
    payload["config"]["phone_waiting_line1"] = "Patient {X}"
    with pytest.raises(ValueError):
        validate_payload("phone", payload)


def test_phone_waiting_lines_resolve_url_number(editor_app):
    """{N} vient du numéro porté par l'URL (pas d'objet Patient), {P} et les
    autres balises « welcome » sont résolues comme sur la page."""
    from routes.patient import _phone_waiting_lines

    app, _ = editor_app
    with app.app_context():
        app.config["PHARMACY_NAME"] = "Pharmacie du Centre"
        app.config["PHONE_WAITING_LINE1"] = "Attente de validation pour {N} — {P}"
        app.config["PHONE_WAITING_LINE2"] = "Veuillez patienter"
        line1, line2 = _phone_waiting_lines("A42", "fr")
    assert line1 == "Attente de validation pour A42 — Pharmacie du Centre"
    assert line2 == "Veuillez patienter"


def test_phone_waiting_lines_use_translation_for_foreign_language(editor_app):
    from models import Translation
    from routes.patient import _phone_waiting_lines

    app, _ = editor_app
    with app.app_context():
        app.config["PHONE_WAITING_LINE1"] = "Attente {N}"
        db.session.add(Translation(
            table_name="ConfigOption", column_name="value_str", row_id=1,
            key_name="phone_waiting_line1", language_code="en",
            translated_text="Waiting for {N}"))
        db.session.commit()
        line1, _ = _phone_waiting_lines("B7", "en")
    assert line1 == "Waiting for B7"


def test_phone_preview_waiting_ended_and_closed_scenarios(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        app.config["PHONE_WAITING_LINE1"] = \
            "En attente de validation pour le numéro {N}"
        app.config["PHONE_WAITING_LINE2"] = "Veuillez patienter...."
        app.config["PHONE_JOURNEY_END_MESSAGE"] = "Passage terminé pour {N}"
        app.config["PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE"] = "Activité fermée"

    waiting = client.get(
        "/admin/page-editor/phone/preview?scenario=waiting"
    ).get_data(as_text=True)
    assert 'id="phone_waiting" data-page-editor-component="waiting_text"' in waiting
    assert 'data-config-key="phone_waiting_line1"' in waiting
    assert "En attente de validation pour le numéro 042" in waiting
    assert "Veuillez patienter...." in waiting

    ended = client.get(
        "/admin/page-editor/phone/preview?scenario=ended"
    ).get_data(as_text=True)
    assert 'id="phone_journey_end" class="text-center" ' \
           'data-page-editor-component="journey_end"' in ended
    assert "Passage terminé pour 042" in ended

    closed = client.get(
        "/admin/page-editor/phone/preview?scenario=closed"
    ).get_data(as_text=True)
    assert 'id="phone_closed" class="text-center" ' \
           'data-page-editor-component="closed"' in closed
    assert "Activité fermée" in closed


def test_layout_rejects_forbidden_zone_and_values():
    payload = make_payload("announce")
    payload["layout"]["title"]["zone"] = "footer"
    with pytest.raises(ValueError, match="Zone"):
        validate_payload("announce", payload)

    payload = make_payload("announce")
    payload["layout"]["title"]["span"] = 13
    with pytest.raises(ValueError, match="Largeur"):
        validate_payload("announce", payload)


def test_hash_only_covers_managed_payload_fields():
    payload = make_payload("announce")
    original = payload_hash(payload)
    payload["base_hash"] = "f" * 64
    assert payload_hash(payload) == original
    payload["config"]["announce_title"] = "Autre titre"
    assert payload_hash(payload) != original


@pytest.mark.parametrize(
    ("page", "key"),
    [
        ("announce", "announce_title"),
        ("patient", "page_patient_title"),
        ("phone", "phone_line1"),
    ],
)
def test_text_fields_are_bounded_by_their_storage_column(page, key):
    """Les clés texte de l'éditeur vivent toutes en ``value_str``
    (``String(200)``) : une limite de 6000 caractères passait la validation
    puis la publication échouait sur MySQL strict."""
    payload = make_payload(page)
    payload["config"][key] = "a" * 200
    assert validate_payload(page, payload)["config"][key] == "a" * 200

    payload = make_payload(page)
    payload["config"][key] = "a" * 201
    with pytest.raises(ValueError, match="maximum 200"):
        validate_payload(page, payload)


@pytest.mark.parametrize(
    ("page", "key", "value", "ok"),
    [
        ("announce", "announce_infos_display_time", 0, False),
        ("announce", "announce_infos_display_time", 3600, True),
        ("announce", "announce_infos_display_time", 3601, False),
        ("announce", "announce_infos_width", 49, False),
        ("announce", "announce_infos_width", 50, True),
        ("announce", "announce_infos_width", 10001, False),
        ("patient", "page_patient_end_timer", -1, False),
        ("patient", "page_patient_end_timer", 0, True),
        ("patient", "page_patient_timer_activity_inactive", -1, False),
        ("patient", "page_patient_print_fail_abandon_timer", -1, False),
        # « Impression incertaine » : jamais « 0 = jamais » (borne bloquée).
        ("patient", "page_patient_print_uncertain_timer", 0, False),
        ("patient", "page_patient_print_uncertain_timer", 5, True),
        ("patient", "page_patient_print_uncertain_timer", 601, False),
    ],
)
def test_int_fields_are_bounded_by_field_and_registry_limits(page, key, value, ok):
    """Les champs numériques respectent les bornes « min »/« max » de
    l'adaptateur croisées avec celles du registre (délais ≥ 0, dimensions
    de galerie bornées)."""
    payload = make_payload(page)
    payload["config"][key] = value
    if ok:
        assert validate_payload(page, payload)["config"][key] == value
    else:
        with pytest.raises(ValueError, match="trop"):
            validate_payload(page, payload)


def test_public_adapter_data_exposes_field_limits():
    """Les bornes remontent côté client : ``max_length`` des zones de texte
    et ``min``/``max`` des champs numériques de l'inspecteur."""
    components = public_adapter_data("announce")["components"]
    title = {field["key"]: field for field in components["title"]["config"]}
    gallery = {field["key"]: field for field in components["gallery"]["config"]}
    assert title["announce_title"]["max_length"] == 200
    assert gallery["announce_infos_width"]["min"] == 50
    assert gallery["announce_infos_width"]["max"] == 10000
    assert gallery["announce_infos_display_time"]["min"] == 1
    assert gallery["announce_infos_display_time"]["max"] == 3600


def test_current_payload_merges_legacy_or_partial_layout():
    app = Flask("legacy-layout")
    app.config["PAGE_EDITOR_ENABLED_PAGES"] = "announce"
    initial = make_payload("announce")
    for key, value in initial["config"].items():
        app.config[get_spec(key).config_name] = value
    app.config["ANNOUNCE_LAYOUT"] = {"title": {"span": 6}, "removed_component": {"span": 1}}
    app.css_variable_manager = DummyCssVariableManager({"announce": initial["css"]})
    with app.app_context():
        merged = current_payload("announce")
    assert set(merged["layout"]) == set(ADAPTERS["announce"]["components"])
    assert merged["layout"]["title"]["span"] == 6
    assert merged["layout"]["title"]["zone"] == "header"


def test_layout_style_uses_server_selectors_and_safe_alignment():
    app = Flask(__name__)
    with app.app_context():
        layout = deepcopy(default_layout("announce"))
        layout["title"]["alignment"] = "left"
        css = str(layout_style("announce", layout))
    assert "#text_title{" in css
    assert "align-self:flex-start" in css
    assert "position:absolute" not in css


def test_layout_style_preview_hides_via_toggleable_attribute():
    app = Flask(__name__)
    with app.app_context():
        layout = deepcopy(default_layout("announce"))
        layout["title"]["visible"] = False
        preview_css = str(layout_style("announce", layout, preview=True))
        real_css = str(layout_style("announce", layout))
    # En aperçu, pas de règle figée : la visibilité se bascule via l'attribut
    # data-page-editor-hidden piloté par page_editor_preview.js.
    assert "#text_title{display:none" not in preview_css
    assert "data-page-editor-hidden" in preview_css
    assert "#text_title{display:none!important}" in real_css


def test_phone_layout_orders_zones_before_components():
    """Sur le téléphone, toutes les zones partagent un conteneur flex : le
    titre précède le contenu et le message spécifique (pied) suit les lignes,
    alors qu'ils portent le même ordre 10 dans la disposition par défaut."""
    app = Flask(__name__)
    with app.app_context():
        css = str(layout_style("phone", deepcopy(default_layout("phone"))))

    def order_of(selector):
        return int(re.search(re.escape(selector) + r"\{order:(\d+)", css).group(1))

    assert order_of("#div_title_area") < order_of("#div_infos")
    assert order_of("#phone_line6") < order_of("#specific_message")
    assert order_of("#phone_your_turn_line6") < order_of("#specific_message")


def test_custom_css_is_replaced_atomically(tmp_path):
    manager = CSSManager()
    manager.css_dir = str(tmp_path)
    manager.css_configs = {
        "announce": {"source": "display.css", "custom": "custom_display.css"}
    }
    url = manager.generate_css({"title_font_size": "42px"}, mode="announce")
    assert url == "/static/css/custom_display.css"
    assert (tmp_path / "custom_display.css").read_text(encoding="utf-8") == (
        ":root {\n    --title_font_size: 42px;\n}\n"
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_api_requires_page_permission(editor_app):
    app, _ = editor_app
    response = app.test_client().get("/admin/page-editor/announce/state")
    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_layout_json_cannot_bypass_editor_validation(editor_app):
    app, user_id = editor_app
    with app.test_request_context("/admin/update_input", method="POST"):
        from flask_login import login_user
        login_user(db.session.get(User, int(user_id)))
        _, error = authorize_config_change("announce_layout")
        assert error[1] == 400


def test_shared_draft_uses_optimistic_version(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Nouveau titre"

    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200
    assert saved.get_json()["draft_version"] == 1
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").first() is None
        assert app.config["ANNOUNCE_TITLE"] == "Texte de démonstration"

    stale = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert stale.status_code == 409


def test_diff_reports_labeled_changes(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    working = make_payload("announce")
    identical = client.post("/admin/page-editor/announce/diff", json={"payload": working})
    assert identical.status_code == 200
    assert identical.get_json() == {
        "identical": True,
        "counts": {"config": 0, "css": 0, "layout": 0},
        "changes": [],
    }

    working["config"]["announce_title"] = "Titre modifié"
    working["css"]["title_font_size"] = "48px"
    working["layout"]["title"]["visible"] = False
    response = client.post("/admin/page-editor/announce/diff", json={"payload": working})
    assert response.status_code == 200
    diff = response.get_json()
    assert diff["identical"] is False
    assert diff["counts"] == {"config": 1, "css": 1, "layout": 1}
    config_change = next(c for c in diff["changes"] if c["section"] == "config")
    assert config_change["key"] == "announce_title"
    assert config_change["component"] == "Titre"
    assert config_change["new"] == "Titre modifié"
    layout_change = next(c for c in diff["changes"] if c["section"] == "layout")
    assert layout_change["key"] == "visible"
    assert layout_change["old"] is True and layout_change["new"] is False


def test_diff_validates_payload_and_requires_permission(editor_app):
    app, _ = editor_app
    anonymous = app.test_client()
    working = make_payload("announce")
    assert anonymous.post(
        "/admin/page-editor/announce/diff", json={"payload": working}
    ).status_code == 401

    client = authenticated_client(editor_app)
    working["css"]["title_font_size"] = "banane"
    response = client.post("/admin/page-editor/announce/diff", json={"payload": working})
    assert response.status_code == 400
    assert "title_font_size" in response.get_json()["error"]


def test_record_screen_ack_validates_input():
    from types import SimpleNamespace

    from sockets import page_screen_status, record_screen_ack

    req = SimpleNamespace(sid="s-test")
    record_screen_ack("announce", req, {"page": "announce", "revision": 3})
    assert page_screen_status["announce"]["s-test"]["revision"] == 3

    # Mauvaise page, révision invalide ou corps non dict : ignorés.
    record_screen_ack("announce", req, {"page": "patient", "revision": 9})
    record_screen_ack("announce", req, {"page": "announce", "revision": "banane"})
    record_screen_ack("announce", req, "pas un dict")
    record_screen_ack("inconnue", req, {"page": "inconnue", "revision": 1})
    assert page_screen_status["announce"]["s-test"]["revision"] == 3
    assert "inconnue" not in page_screen_status
    page_screen_status["announce"].pop("s-test", None)


def test_screens_endpoint_reports_acks_and_pending(editor_app, monkeypatch):
    from types import SimpleNamespace

    from extensions import socketio
    from sockets import page_screen_status

    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        db.session.add(PageEditorState(page_key="announce", published_revision=2))
        db.session.commit()

    # Deux écrans ont accusé ; un troisième est connecté sans accusé (client
    # plus ancien) via la table des rooms du serveur Socket.IO simulée.
    page_screen_status["announce"]["s-ok"] = {
        "username": "écran hall", "revision": 2, "at": 1700000000.0}
    page_screen_status["announce"]["s-vieux"] = {
        "username": "écran salle", "revision": 1, "at": 1700000001.0}
    fake_manager = SimpleNamespace(
        rooms={"/socket_update_screen": {None: {"s-ok", "s-vieux", "s-muet"}}}
    )
    monkeypatch.setattr(socketio, "server", SimpleNamespace(manager=fake_manager))
    try:
        response = client.get("/admin/page-editor/announce/screens")
        assert response.status_code == 200
        data = response.get_json()
        assert data["published_revision"] == 2
        by_sid = {item["sid"]: item for item in data["screens"]}
        assert by_sid["s-ok"]["status"] == "current"
        assert by_sid["s-vieux"]["status"] == "stale"
        assert by_sid["s-muet"]["status"] == "pending"
        assert by_sid["s-muet"]["revision"] is None
    finally:
        page_screen_status["announce"].clear()

    anonymous = app.test_client()
    assert anonymous.get("/admin/page-editor/announce/screens").status_code == 401


@pytest.mark.parametrize("page", list(ADAPTERS))
def test_builtin_themes_are_read_only_and_do_not_write_state(editor_app, page):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    before = client.get(f"/admin/page-editor/{page}/state").get_json()
    response = client.get(f"/admin/page-editor/{page}/themes")
    assert response.status_code == 200
    data = response.get_json()
    assert data["themes"] == []
    expected = ["Officine", "Lisibilité renforcée", "Sauge & Lin", "Bleu Horizon", "Ardoise",
                "Classique"]
    assert [theme["name"] for theme in data["builtins"]] == expected
    for theme in data["builtins"]:
        assert theme["builtin"] is True
        assert theme["snapshot"]["config"] == {}
        assert theme["page"] == page
        assert client.delete(f"/admin/page-editor/{page}/themes/{theme['id']}").status_code == 404
    after = client.get(f"/admin/page-editor/{page}/state").get_json()
    assert before == after
    assert app.test_client().get(f"/admin/page-editor/{page}/themes").status_code == 401
    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        setattr(role, f"admin_{page}", False)
        db.session.commit()
    assert client.get(f"/admin/page-editor/{page}/themes").status_code == 403


@pytest.mark.parametrize("page", list(ADAPTERS))
@pytest.mark.parametrize("theme_index", range(6))
def test_builtin_theme_payloads_are_valid_and_have_contrast(page, theme_index):
    from page_editor import builtin_themes

    themes = builtin_themes(page)
    if theme_index >= len(themes):
        pytest.skip("thème non disponible sur cette page")
    theme = themes[theme_index]
    payload = make_payload(page)
    original_config = deepcopy(payload["config"])
    original_hash = payload["base_hash"]
    payload["css"].update(_normalized_css(page, theme["snapshot"]["css"]))
    for component_id, values in theme["snapshot"]["layout"].items():
        payload["layout"][component_id].update(values)
    assert validate_payload(page, payload) == payload
    assert payload["config"] == original_config
    assert payload["base_hash"] == original_hash
    assert set(theme["snapshot"]["css"]) == set(payload["css"])
    assert not any("visible" in values for component_id, values in theme["snapshot"]["layout"].items()
                   if not (page == "announce" and theme_index == 1 and component_id == "gallery"))

    def luminance(color):
        rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
        return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))

    if theme["id"] == "builtin-classique":
        # Reproduction fidèle de la configuration en service : certaines paires
        # (grand texte blanc sur #5FB4B4) n'atteignent pas 4,5:1 — ce thème
        # n'est pas un préréglage recommandé, mais une sauvegarde des réglages.
        return
    css = payload["css"]
    for key, foreground in css.items():
        suffix = next((suffix for suffix in ("_font_color", "_text_color") if key.endswith(suffix)), None)
        if suffix is None:
            continue
        prefix = key.removesuffix(suffix)
        background = css.get(prefix + "_background_color", css.get(prefix + "_color", css[page + "_secondary_color"]))
        if page == "announce" and key == "subtitle_font_color":
            background = css["title_background_color"]
        if key == "circle_button_text_color":
            background = css["patient_secondary_color"]
        if background == "transparent":
            # Fond transparent : le contraste se mesure contre le fond de
            # page, qui est ce que le patient voit derrière le texte.
            background = css[page + "_secondary_color"]
        values = sorted((luminance(foreground), luminance(background)))
        assert (values[1] + 0.05) / (values[0] + 0.05) >= 4.5, (theme["name"], page, key)
    if page == "patient":
        assert int(payload["css"]["square_button_height"].removesuffix("px")) >= 56
    if page == "announce" and theme_index == 1:
        assert payload["layout"]["gallery"]["visible"] is False
        assert "#div_center_divided{grid-template-columns:1fr}" in layout_style(page, payload["layout"])


def test_builtin_announce_themes_set_calls_apart_from_title():
    """Les appels en cours ont leur propre couleur d'accent : avec le fond
    du bandeau titre, ils se confondaient avec le titre et le sous-titre."""
    from page_editor import builtin_themes

    for theme in builtin_themes("announce"):
        css = theme["snapshot"]["css"]
        assert css["calling_background_color"] != css["title_background_color"], theme["name"]
        assert css["calling_background_color"] in theme["colors"], theme["name"]


def _contrast(first, second):
    def luminance(color):
        rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
        return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_builtin_announce_themes_offer_discreet_and_vivid_calls():
    """Chaque thème d'annonce (sauf Classique, reproduction exacte) propose
    deux styles d'appel : la teinte discrète de la palette, présélectionnée
    et identique à l'instantané, et une couleur complémentaire bien visible.
    Les deux restent lisibles et se distinguent du bandeau titre."""
    from page_editor import builtin_themes

    for theme in builtin_themes("announce"):
        if theme["id"] == "builtin-classique":
            assert "call_styles" not in theme
            continue
        styles = {style["id"]: style["css"] for style in theme["call_styles"]}
        assert list(styles) == ["discreet", "vivid"], theme["name"]
        assert theme["default_call_style"] == "discreet"
        css = theme["snapshot"]["css"]
        assert styles["discreet"] == {key: css[key] for key in styles["discreet"]}
        assert styles["vivid"]["calling_background_color"] != styles["discreet"]["calling_background_color"]
        for style in styles.values():
            assert set(style) == {"calling_background_color", "calling_font_color", "calling_font_border_size"}
            # Contour foncé uniquement autour d'un texte blanc.
            white = style["calling_font_color"].upper() == "#FFFFFF"
            assert style["calling_font_border_size"] == ("5px" if white else "0px"), theme["name"]
            assert css["calling_font_border_color"] == theme["colors"][2], theme["name"]
            assert style["calling_background_color"] != css["title_background_color"], theme["name"]
            assert _contrast(style["calling_font_color"], style["calling_background_color"]) >= 4.5, theme["name"]


@pytest.mark.parametrize("page", ["patient", "phone"])
def test_call_styles_are_announce_only(page):
    from page_editor import builtin_themes

    assert all("call_styles" not in theme for theme in builtin_themes(page))


@pytest.mark.parametrize("page", list(ADAPTERS))
def test_builtin_theme_can_be_saved_and_published_without_changing_content(editor_app, page):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    initial = client.get(f"/admin/page-editor/{page}/state").get_json()["published"]
    theme = client.get(f"/admin/page-editor/{page}/themes").get_json()["builtins"][4]
    working = deepcopy(initial)
    working["css"].update(_normalized_css(page, theme["snapshot"]["css"]))
    for component_id, layout in theme["snapshot"]["layout"].items():
        working["layout"][component_id].update(layout)
    saved = client.put(f"/admin/page-editor/{page}/draft", json={"draft_version": 0, "payload": working})
    assert saved.status_code == 200
    state = client.get(f"/admin/page-editor/{page}/state").get_json()
    assert state["published"] == initial
    response = client.post(f"/admin/page-editor/{page}/publish", json={"draft_version": saved.get_json()["draft_version"]})
    assert response.status_code == 200
    published = client.get(f"/admin/page-editor/{page}/state").get_json()["published"]
    assert published["css"] == working["css"]
    assert published["config"] == initial["config"]
    with app.app_context():
        assert PageEditorRevision.query.filter_by(page_key=page).one().snapshot_json["css"] == working["css"]


@pytest.mark.parametrize("page", list(ADAPTERS))
def test_old_theme_css_still_validates(page):
    payload = make_payload(page)
    for component in ADAPTERS[page]["components"].values():
        for field in component["css"]:
            if field.get("optional"):
                payload["css"].pop(field["key"])
    assert validate_payload(page, payload) == payload
    payload["css"]["unregistered_background_color"] = "#FFFFFF"
    with pytest.raises(ValueError, match="apparence"):
        validate_payload(page, payload)


@pytest.fixture
def playwright_driver(request):
    """Instance Playwright de la session (fixture ``playwright`` de
    pytest-playwright). Démarrer un second ``sync_playwright()`` échoue dès
    qu'un autre module l'utilise déjà (« Sync API inside the asyncio loop ») :
    ces tests ne passaient alors que lancés seuls."""
    return request.getfixturevalue("playwright")


@pytest.mark.e2e
@pytest.mark.parametrize("page_key", list(ADAPTERS))
def test_builtin_themes_browser_flow(editor_app, page_key, playwright_driver):
    from threading import Thread

    from jinja2 import FileSystemLoader
    from werkzeug.serving import make_server

    from page_editor import preview_vars_style

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    root = Path(__file__).resolve().parents[1]
    app.static_folder = str(root / "static")
    app.jinja_loader = FileSystemLoader(root / "templates")
    app.jinja_env.globals.update(
        csrf_token=lambda: "test", get_css_url=lambda mode=None: "/static/css/" + {"announce": "display", "patient": "patient", "phone": "phone"}[mode] + ".css",
        page_layout_style=layout_style, preview_vars_style=preview_vars_style,
        user_has_permission=lambda *args, **kwargs: True, page_editor_enabled=lambda page: True,
    )
    if page_key == "patient":
        with app.app_context():
            activity = Activity(name="Accueil E2E", letter="E")
            child_activity = Activity(name="Enfant E2E", letter="F")
            parent = Button(
                label="Bouton E2E", activity=activity, is_parent=True,
                is_present=True, is_active=True, shape="circle", sort_order=1,
            )
            db.session.add_all([
                activity,
                child_activity,
                parent,
                Button(
                    label="Sous-bouton E2E", activity=child_activity,
                    parent_button=parent, is_present=True, is_active=True,
                    shape="circle", sort_order=1,
                ),
            ])
            db.session.commit()
    client = authenticated_client(editor_app)
    before = client.get(f"/admin/page-editor/{page_key}/state").get_json()
    baseline = before["published"]
    theme_data = client.get(f"/admin/page-editor/{page_key}/themes").get_json()["builtins"]
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"{url}/admin/page-editor/{page_key}")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
                if page_key == "patient":
                    # La borne s'ouvre en mode « Parcours » : composants et
                    # scénarios d'aperçu vivent dans « Design des écrans ».
                    page.locator("#editor-mode-design").click()

                def working():
                    return page.evaluate("key => JSON.parse(localStorage.getItem('page-editor-backup-' + key)).payload", page_key)

                def confirm_theme_if_asked():
                    """Un thème intégré n'écrase jamais les textes (« Contenu »
                    est ignoré) : la confirmation ne s'ouvre que si le
                    brouillon a des changements non enregistrés — ce qui
                    dépend de la sauvegarde automatique (3 s). On attend donc
                    l'une des deux issues : confirmation ouverte par-dessus
                    « Thèmes », ou boîte « Thèmes » refermée."""
                    page.wait_for_function("""() => {
                        const dialogs = document.querySelectorAll('.page-editor-dialog');
                        if (!dialogs.length) return true;
                        return Array.from(dialogs[dialogs.length - 1].querySelectorAll('button'))
                            .some(button => button.textContent.trim() === 'Appliquer'
                                && !button.hasAttribute('aria-label'));
                    }""")
                    if page.locator(".page-editor-dialog").count():
                        page.locator(".page-editor-dialog").last.get_by_role(
                            "button", name="Appliquer", exact=True).click()
                    playwright.expect(page.locator(".page-editor-dialog")).to_have_count(0)

                for theme in theme_data:
                    page.locator("#editor-themes").click()
                    row = page.locator(f"[data-theme-id='{theme['id']}']")
                    playwright.expect(row.locator(".btn-outline-danger")).to_have_count(0)
                    playwright.expect(row.locator(".page-editor-theme-swatches span")).to_have_count(6)
                    page.locator("#editor-theme-section-layout").uncheck()
                    page.locator("#editor-theme-section-content").check()
                    row.locator(".btn-primary").click()
                    confirm_theme_if_asked()
                    applied = working()
                    assert applied["config"] == baseline["config"]
                    assert applied["layout"] == baseline["layout"]
                    assert applied["base_hash"] == baseline["base_hash"]
                    assert applied["css"] == theme["snapshot"]["css"]
                    preview = page.frame_locator("#editor-preview").locator("html")
                    playwright.expect(preview).to_have_css("--" + page_key + "_secondary_color", theme["snapshot"]["css"][page_key + "_secondary_color"])
                    if page_key == "patient" and theme["id"] == "builtin-officine":
                        geometry = page.frame_locator("#editor-preview").locator("#main").evaluate("""main => {
                            const footer = document.querySelector('#div_buttons_children').getBoundingClientRect();
                            const center = document.querySelector('#div_buttons_parents').getBoundingClientRect();
                            return {
                                mainHeight: main.clientHeight,
                                mainScrollHeight: main.scrollHeight,
                                centerHeight: center.height,
                                footerBottom: footer.bottom,
                            };
                        }""")
                        assert geometry["mainScrollHeight"] <= geometry["mainHeight"] + 1
                        assert geometry["centerHeight"] > 0
                        assert geometry["footerBottom"] <= 801
                    page.locator("#editor-undo").click()
                    assert working() == baseline

                page.locator("#editor-themes").click()
                page.locator("#editor-theme-section-appearance").uncheck()
                page.locator("#editor-theme-section-layout").uncheck()
                page.locator("#editor-theme-section-content").check()
                row = page.locator("[data-theme-id='builtin-lisibilite']")
                playwright.expect(row.locator(".btn-primary")).to_be_disabled()
                page.locator("#editor-theme-section-layout").check()
                row.locator(".btn-primary").click()
                confirm_theme_if_asked()
                assert working()["css"] == baseline["css"]
                assert working()["config"] == baseline["config"]
                if page_key == "announce":
                    assert working()["layout"]["gallery"]["visible"] is False
                page.locator("#editor-themes").click()
                page.locator("#editor-theme-name").fill("Ma variante")
                page.locator("#editor-theme-save").click()
                playwright.expect(page.locator(".page-editor-theme-item")).to_have_count(len(theme_data) + 1)
                playwright.expect(page.locator(".page-editor-theme-item .btn-outline-danger")).to_have_count(1)
                page.locator(".page-editor-dialog-footer .btn").click()

                # Aperçu épinglé : il reste visible quand l'inspecteur défile.
                page.set_viewport_size({"width": 1280, "height": 620})
                page.locator("[data-component-id]").first.click()
                rects = page.evaluate("""() => {
                    window.scrollTo({top: 800, behavior: 'instant'});
                    return {
                        canvas: document.querySelector('.page-editor-canvas').getBoundingClientRect(),
                        toolbar: document.querySelector('.page-editor-toolbar').getBoundingClientRect(),
                        preview: document.querySelector('#editor-preview').getBoundingClientRect(),
                    };
                }""")
                assert rects["canvas"]["y"] <= rects["toolbar"]["y"] + rects["toolbar"]["height"] + 8
                assert rects["preview"]["y"] < rects["canvas"]["y"] + rects["canvas"]["height"]
                page.evaluate("window.scrollTo(0, 0)")

                if page_key == "patient":
                    def assert_patient_center_fits():
                        geometry = page.frame_locator("#editor-preview").locator(
                            "#div_buttons_parents"
                        ).evaluate("""center => {
                            const bounds = center.getBoundingClientRect();
                            const visibleChildren = Array.from(center.children).filter(child => {
                                const style = getComputedStyle(child);
                                return !child.hidden && style.display !== 'none' && style.visibility !== 'hidden';
                            });
                            return {
                                clientHeight: center.clientHeight,
                                scrollHeight: center.scrollHeight,
                                childTop: Math.min(...visibleChildren.map(child => child.getBoundingClientRect().top)),
                                childBottom: Math.max(...visibleChildren.map(child => child.getBoundingClientRect().bottom)),
                                centerTop: bounds.top,
                                centerBottom: bounds.bottom,
                            };
                        }""")
                        assert geometry["scrollHeight"] <= geometry["clientHeight"] + 1
                        assert geometry["childTop"] >= geometry["centerTop"] - 1
                        assert geometry["childBottom"] <= geometry["centerBottom"] + 1

                    page.locator("#editor-scenario").select_option("home")
                    page.locator("[data-component-id='buttons']").click()
                    shape_select = page.locator(
                        "#editor-config-page_patient_button_shape"
                    )
                    playwright.expect(shape_select).to_be_visible()
                    shape_select.select_option("circle")
                    playwright.expect(page.locator(
                        "#editor-css-circle_button_size"
                    )).to_have_count(1)
                    playwright.expect(page.locator(
                        "#editor-css-square_button_width"
                    )).to_have_count(0)
                    preview_frame = page.frame_locator("#editor-preview")
                    playwright.expect(preview_frame.locator(
                        ".page-editor-button-shape-preview:visible"
                    )).to_have_count(1)
                    playwright.expect(preview_frame.locator(
                        ".page-editor-button-shape-preview:visible .div_circle_buttons"
                    )).to_have_count(1)

                    shape_select.select_option("square")
                    playwright.expect(page.locator(
                        "#editor-css-square_button_width"
                    )).to_have_count(1)
                    playwright.expect(page.locator(
                        "#editor-css-circle_button_size"
                    )).to_have_count(0)
                    playwright.expect(preview_frame.locator(
                        ".page-editor-button-shape-preview:visible .div_square_bloc_activity"
                    )).to_have_count(1)
                    assert_patient_center_fits()

                    # Sous-activités : forme propre, indépendante de l'accueil
                    # (resté en rectangles ci-dessus).
                    page.locator("#editor-scenario").select_option("children")
                    page.locator("[data-component-id='buttons']").click()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_button_shape"
                    )).to_have_count(0)
                    children_shape_select = page.locator(
                        "#editor-config-page_patient_children_button_shape"
                    )
                    playwright.expect(children_shape_select).to_have_value("circle")
                    playwright.expect(page.locator(
                        "#editor-css-circle_button_size"
                    )).to_have_count(1)
                    playwright.expect(page.locator(
                        "#editor-css-square_button_width"
                    )).to_have_count(0)
                    preview_frame = page.frame_locator("#editor-preview")
                    playwright.expect(preview_frame.locator(
                        ".page-editor-button-shape-preview:visible .div_circle_buttons"
                    )).to_have_count(1)
                    children_shape_select.select_option("square")
                    playwright.expect(page.locator(
                        "#editor-css-square_button_width"
                    )).to_have_count(1)
                    playwright.expect(page.locator(
                        "#editor-css-circle_button_size"
                    )).to_have_count(0)
                    playwright.expect(preview_frame.locator(
                        ".page-editor-button-shape-preview:visible .div_square_bloc_activity"
                    )).to_have_count(1)
                    children_shape_select.select_option("circle")
                    page.locator("[data-component-id='back_button']").click()
                    back_text = page.locator(
                        "#editor-config-page_patient_interface_children_back"
                    )
                    playwright.expect(back_text).to_be_visible()
                    playwright.expect(page.locator(
                        "#editor-css-square_cancel_button_width"
                    )).to_have_count(1)
                    back_text.fill("Revenir E2E")
                    playwright.expect(preview_frame.locator(
                        "[data-config-key='page_patient_interface_children_back']:visible"
                    )).to_have_text("Revenir E2E")
                    back_geometry = preview_frame.locator(
                        "[data-page-editor-component='back_button']:visible"
                    ).evaluate("""back => {
                        const center = document.querySelector('#div_buttons_parents').getBoundingClientRect();
                        const bounds = back.getBoundingClientRect();
                        return {
                            top: bounds.top,
                            right: bounds.right,
                            bottom: bounds.bottom,
                            centerTop: center.top,
                            centerRight: center.right,
                            centerBottom: center.bottom,
                        };
                    }""")
                    assert back_geometry["top"] >= back_geometry["centerTop"] - 1
                    assert back_geometry["right"] <= back_geometry["centerRight"] + 1
                    assert back_geometry["bottom"] <= back_geometry["centerBottom"] + 1

                    page.locator("#editor-scenario").select_option("validation")
                    page.locator("[data-component-id='buttons']").click()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_validation_message"
                    )).to_be_visible()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_confirmation_message"
                    )).to_have_count(0)
                    scan_toggle = page.locator(
                        "#editor-config-page_patient_display_button_scan"
                    )
                    scan_button = page.frame_locator("#editor-preview").locator(
                        "[data-config-bool='page_patient_display_button_scan']"
                    )
                    playwright.expect(scan_button).not_to_be_visible()
                    scan_toggle.check()
                    playwright.expect(scan_button).to_be_visible()
                    validation_message = page.locator(
                        "#editor-config-page_patient_validation_message"
                    )
                    validation_message.fill("Confirmez votre choix E2E")
                    playwright.expect(page.frame_locator("#editor-preview").locator(
                        "[data-config-key='page_patient_validation_message']"
                    )).to_have_text("Confirmez votre choix E2E")
                    assert_patient_center_fits()

                    page.locator("#editor-scenario").select_option("conclusion")
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_confirmation_message"
                    )).to_be_visible()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_validation_message"
                    )).to_have_count(0)
                    playwright.expect(page.frame_locator("#editor-preview").locator(
                        "#div_conclusion"
                    )).to_be_visible()
                    assert_patient_center_fits()

                    for print_scenario in (
                        "print_error", "printing", "print_uncertain", "staff_called"
                    ):
                        page.locator("#editor-scenario").select_option(print_scenario)
                        playwright.expect(page.frame_locator("#editor-preview").locator(
                            "#print_status_overlay"
                        )).to_be_visible()
                        assert_patient_center_fits()

                    page.locator("#editor-scenario").select_option(
                        "activity_inactive"
                    )
                    page.locator("[data-component-id='buttons']").click()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_button_shape"
                    )).to_have_count(0)
                    playwright.expect(page.locator(
                        "#editor-css-square_button_width"
                    )).to_have_count(0)
                    playwright.expect(page.locator(
                        "#editor-css-square_button_color"
                    )).to_have_count(0)
                    playwright.expect(page.locator(
                        "#editor-css-circle_button_size"
                    )).to_have_count(0)
                    page.locator(
                        "[data-component-id='activity_status']"
                    ).click()
                    inactive_toggle = page.locator(
                        "#editor-config-page_patient_disable_button"
                    )
                    playwright.expect(inactive_toggle).to_be_visible()
                    inactive_message = page.locator(
                        "#editor-config-page_patient_disable_default_message"
                    )
                    playwright.expect(inactive_message).to_be_visible()
                    playwright.expect(page.locator(
                        "#editor-config-page_patient_subtitle"
                    )).to_have_count(0)
                    inactive_button = page.frame_locator(
                        "#editor-preview"
                    ).locator(
                        ".page-editor-button-shape-preview:visible "
                        ".div_square_button_inactive, "
                        ".page-editor-button-shape-preview:visible "
                        ".div_button_inactive"
                    )
                    playwright.expect(inactive_button).to_have_count(1)
                    inactive_toggle.uncheck()
                    playwright.expect(inactive_button).not_to_be_visible()
                    playwright.expect(page.frame_locator("#editor-preview").locator(
                        "#div_buttons_children_default"
                    )).to_be_visible()
                    inactive_toggle.check()
                    playwright.expect(inactive_button).to_be_visible()
                    inactive_message.fill("Cette activité est fermée pour le test E2E")
                    playwright.expect(page.frame_locator("#editor-preview").locator(
                        "[data-config-key='page_patient_disable_default_message']"
                    )).to_have_text("Cette activité est fermée pour le test E2E")
                    assert_patient_center_fits()

                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#editor-themes").click()
                assert page.locator(".page-editor-dialog-body").evaluate("el => el.scrollWidth <= el.clientWidth")
                assert not errors
                assert client.get(f"/admin/page-editor/{page_key}/state").get_json() == before
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.e2e
def test_editor_enables_advanced_disabled_next_patients(editor_app, playwright_driver):
    """Parcours réel : « Prochains patients » désactivé dans la configuration
    reste sélectionnable, sa case « Afficher » apparaît dans l'inspecteur et
    la cocher le réaffiche dans l'aperçu."""
    from threading import Thread

    from werkzeug.serving import make_server

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    _preview_ready_app(app)
    with app.app_context():
        app.config["ANNOUNCE_NEXT_PATIENTS_DISPLAY"] = False
    client = authenticated_client(editor_app)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{url}/admin/page-editor/announce")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")

                next_button = page.locator("[data-component-id='next']")
                assert "is-disabled" in (next_button.get_attribute("class") or "")
                next_button.click()

                toggle = page.locator("#editor-config-announce_next_patients_display")
                playwright.expect(toggle).to_be_visible()
                assert not toggle.is_checked()
                preview = page.frame_locator("#editor-preview").locator("#div_next_patients")
                assert not preview.is_visible()

                toggle.check()
                playwright.expect(preview).to_be_visible()
                assert "is-disabled" not in (next_button.get_attribute("class") or "")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.e2e
def test_patient_single_circle_button_row_stays_vertically_centered(editor_app, playwright_driver):
    """Régression : une seule ligne de boutons ronds doit rester centrée
    verticalement dans la zone centrale (constaté sur « Sous-activités »),
    au lieu d'être ancrée en bas."""
    from threading import Thread

    from werkzeug.serving import make_server

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    _preview_ready_app(app)
    with app.app_context():
        app.config["PAGE_PATIENT_BUTTON_SHAPE"] = "circle"
        # Le mode « image au-dessus » réserve de la place en haut de cellule
        # (débordement de l'image) : désactivé ici pour mesurer le centrage
        # strict du groupe cercle + étiquette.
        app.config["PAGE_PATIENT_CIRCLE_IMAGE_ABOVE"] = False
        parent_activity = Activity(name="Parent centré", letter="P")
        child_activity = Activity(name="Enfant centré", letter="E")
        parent = Button(
            label="Parent E2E", activity=parent_activity, is_parent=True,
            is_present=True, is_active=True, shape="circle", sort_order=1,
        )
        db.session.add_all([parent_activity, child_activity, parent])
        db.session.add_all([
            Button(
                label=f"Sous-bouton {index}", activity=child_activity,
                parent_button=parent, is_present=True, is_active=True,
                shape="circle", sort_order=index,
            )
            for index in (1, 2)
        ])
        db.session.commit()
    client = authenticated_client(editor_app)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                page.goto(f"{url}/admin/page-editor/patient")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
                page.locator("#editor-mode-design").click()

                page.locator("#editor-scenario").select_option("children")
                cells = page.frame_locator("#editor-preview").locator(
                    ".page-editor-button-shape-preview:visible .div_bloc_activity"
                )
                playwright.expect(cells).to_have_count(2)

                gaps = cells.evaluate_all("""cells => cells.map(cell => {
                    const box = cell.getBoundingClientRect();
                    const circle = cell.querySelector('.div_circle_buttons').getBoundingClientRect();
                    const label = cell.querySelector('.div_text_button').getBoundingClientRect();
                    return {
                        cellTop: box.top,
                        cellBottom: box.bottom,
                        top: circle.top,
                        bottom: label.bottom,
                    };
                })""")
                for gap in gaps:
                    above = gap["top"] - gap["cellTop"]
                    below = gap["cellBottom"] - gap["bottom"]
                    # Centré : les marges au-dessus et en dessous du groupe
                    # cercle + étiquette sont équivalentes (le padding-bottom
                    # de la cellule décale le bas d'environ 10 px).
                    assert above > 0 and below > 0
                    assert abs(above - below) <= 15
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.e2e
def test_patient_circle_button_row_spacing_stays_homogeneous(editor_app, playwright_driver):
    """Régression : les étiquettes s'enroulent dans leur colonne (pas de
    chevauchement entre boutons voisins) et tous les cercles d'une rangée
    restent alignés verticalement, que le libellé fasse une ou deux
    lignes."""
    from threading import Thread

    from werkzeug.serving import make_server

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    _preview_ready_app(app)
    with app.app_context():
        app.config["PAGE_PATIENT_BUTTON_SHAPE"] = "circle"
        parent_activity = Activity(name="Parent espacement", letter="P")
        child_activity = Activity(name="Enfant espacement", letter="E")
        parent = Button(
            label="Parent E2E", activity=parent_activity, is_parent=True,
            is_present=True, is_active=True, shape="circle", sort_order=1,
        )
        db.session.add_all([parent_activity, child_activity, parent])
        db.session.add_all([
            Button(
                label=label, activity=child_activity,
                parent_button=parent, is_present=True, is_active=True,
                shape="circle", sort_order=index,
            )
            for index, label in enumerate(
                ["Oui", "Attestation de présence", "Non", "Demande de conseil"],
                start=1,
            )
        ])
        db.session.commit()
    client = authenticated_client(editor_app)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                page.goto(f"{url}/admin/page-editor/patient")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
                page.locator("#editor-mode-design").click()

                page.locator("#editor-scenario").select_option("children")
                cells = page.frame_locator("#editor-preview").locator(
                    ".page-editor-button-shape-preview:visible .div_bloc_activity"
                )
                playwright.expect(cells).to_have_count(4)

                boxes = cells.evaluate_all("""cells => cells.map(cell => {
                    const box = cell.getBoundingClientRect();
                    const circle = cell.querySelector('.div_circle_buttons').getBoundingClientRect();
                    const label = cell.querySelector('.div_text_button').getBoundingClientRect();
                    return {
                        cellLeft: box.left, cellRight: box.right,
                        circleTop: circle.top,
                        labelLeft: label.left, labelRight: label.right,
                    };
                })""")
                # Tous les cercles de la rangée partagent la même hauteur :
                # un emplacement constant est réservé à l'étiquette, que le
                # texte fasse une ou deux lignes.
                tops = [box["circleTop"] for box in boxes]
                assert max(tops) - min(tops) <= 2
                for box in boxes:
                    # L'étiquette reste contenue dans sa colonne : elle ne
                    # déborde plus sur les boutons voisins.
                    assert box["labelLeft"] >= box["cellLeft"] - 1
                    assert box["labelRight"] <= box["cellRight"] + 1
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _measure_circle_rows(editor_app, labels, playwright_driver):
    """Ouvre l'aperçu « Accueil » avec des boutons ronds ``labels`` et
    renvoie, par rangée de la variante cercle, les centres horizontaux
    des cercles. Lance un serveur éphémère et Playwright."""
    from threading import Thread

    from werkzeug.serving import make_server

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    _preview_ready_app(app)
    with app.app_context():
        app.config["PAGE_PATIENT_BUTTON_SHAPE"] = "circle"
        activity = Activity(name="Accueil quinconce", letter="Q")
        db.session.add(activity)
        db.session.add_all([
            Button(
                label=label, activity=activity,
                is_present=True, is_active=True,
                shape="circle", sort_order=index,
            )
            for index, label in enumerate(labels, start=1)
        ])
        db.session.commit()
    client = authenticated_client(editor_app)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                page.goto(f"{url}/admin/page-editor/patient")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
                page.locator("#editor-mode-design").click()

                page.locator("#editor-scenario").select_option("home")
                rows = page.frame_locator("#editor-preview").locator(
                    ".page-editor-button-shape-preview:visible .inner-grid-container"
                )
                playwright.expect(rows).to_have_count(2)
                return rows.evaluate_all("""rows => rows.map(row =>
                    [...row.querySelectorAll('.div_circle_buttons')].map(
                        circle => circle.getBoundingClientRect().left
                                + circle.getBoundingClientRect().width / 2
                    )
                )""")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.e2e
def test_patient_circle_button_equal_rows_stay_aligned(editor_app, playwright_driver):
    """Régression : deux rangées de même longueur partagent les mêmes
    colonnes — chaque bouton du bas est exactement sous celui du haut
    (la grille ne doit pas se dimensionner en fit-content)."""
    centers = _measure_circle_rows(
        editor_app,
        ["Ordonnance", "Conseil", "Retrait", "Vaccination", "Test", "Certificat"],
        playwright_driver,
    )
    top, bottom = centers
    assert len(top) == len(bottom) == 3
    for above, below in zip(top, bottom):
        assert abs(above - below) <= 2


@pytest.mark.e2e
def test_patient_circle_button_shorter_second_row_staggers(editor_app, playwright_driver):
    """Régression : une seconde rangée plus courte se place en quinconce
    — chaque bouton du bas centré sous l'espace entre deux boutons du
    haut — au lieu d'être tassée au centre."""
    centers = _measure_circle_rows(
        editor_app,
        ["Ordonnance", "Conseil", "Retrait", "Vaccination", "Prioritaire"],
        playwright_driver,
    )
    top, bottom = centers
    assert len(top) == 3 and len(bottom) == 2
    for index, below in enumerate(bottom):
        midpoint = (top[index] + top[index + 1]) / 2
        assert abs(below - midpoint) <= 5


def test_builtin_theme_snapshots_are_independent():
    from page_editor import builtin_themes

    first = builtin_themes("announce")
    first[0]["snapshot"]["css"]["title_font_color"] = "#000000"
    first[0]["snapshot"]["layout"]["title"]["span"] = 1
    fresh = builtin_themes("announce")
    assert fresh[0]["snapshot"]["css"]["title_font_color"] != "#000000"
    assert fresh[0]["snapshot"]["layout"]["title"]["span"] == 12


@pytest.mark.parametrize("page", list(ADAPTERS))
def test_factory_payload_reads_shipped_defaults(page):
    """« Valeurs d'usine » : disposition par défaut, configuration et
    variables des JSON de ``static/json`` (et non de la configuration
    publiée, qui peut avoir été personnalisée)."""
    adapter = ADAPTERS[page]
    payload = factory_payload(page)
    assert payload["schema_version"] == 1
    assert payload["page"] == page
    assert payload["layout"] == default_layout(page)
    managed_config = {
        field["key"]
        for component in adapter["components"].values()
        for field in component["config"]
    }
    assert set(payload["config"]) == managed_config


def test_factory_payload_normalizes_factory_css_values():
    """Le fichier annonce écrit ses couleurs en majuscules : la
    normalisation (même règle qu'à la validation) évite les faux diffs
    entre valeur courante et valeur d'usine."""
    payload = factory_payload("announce")
    assert payload["css"]["title_background_color"] == "#176b52"
    assert payload["css"]["announce_secondary_color"] == "#f5f8f7"
    assert payload["config"]["announce_title"] == "Bienvenue à la {P}"
    assert payload["config"]["announce_ongoing_display"] is False


def test_factory_payload_marks_missing_config_keys_none():
    """Un réglage géré absent du JSON d'usine vaut ``None`` : le client
    n'affiche alors aucun bouton « reprendre la valeur d'usine »."""
    payload = factory_payload("patient")
    assert "page_patient_print_fail_abandon_timer" in payload["config"]
    assert payload["config"]["page_patient_print_fail_abandon_timer"] is None
    # Les choix d'échec d'impression, eux, ont une valeur d'usine.
    assert payload["config"]["page_patient_print_fail_behavior"] == "keep"
    assert payload["config"]["page_patient_print_fail_show_retry"] is False
    assert payload["config"]["page_patient_print_fail_show_staff"] is False


def test_state_exposes_factory_defaults(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    document = client.get("/admin/page-editor/announce/state").get_json()
    assert document["defaults"]["layout"] == default_layout("announce")
    assert document["defaults"]["css"]["title_background_color"] == "#176b52"
    assert document["defaults"]["config"]["announce_title"] == \
        "Bienvenue à la {P}"


def test_theme_import_of_partial_snapshot_is_completed(editor_app):
    """Un instantané importé amputé (réglages/composants apparus depuis)
    est accepté : ``save_theme`` le complète comme les thèmes anciens en
    base avant validation, sinon l'import d'un export d'ancienne version
    échouerait sur la « structure incomplète »."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = _legacy_patient_draft()

    saved = client.post("/admin/page-editor/patient/themes", json={
        "name": "Import ancien", "payload": payload,
    })
    assert saved.status_code == 200
    snapshot = saved.get_json()["theme"]["snapshot"]
    assert snapshot["layout"]["print_error"]["zone"] == "main"
    assert "print_error_font_size" in snapshot["css"]
    assert validate_payload("patient", snapshot)


def test_themes_crud_and_validation(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Thème hiver"

    saved = client.post("/admin/page-editor/announce/themes", json={
        "name": "Hiver", "description": "Ambiance froide", "payload": payload,
    })
    assert saved.status_code == 200
    theme = saved.get_json()["theme"]
    assert theme["name"] == "Hiver"
    assert theme["snapshot"]["config"]["announce_title"] == "Thème hiver"
    assert theme["author"] == "editor"

    listed = client.get("/admin/page-editor/announce/themes").get_json()["themes"]
    assert [item["name"] for item in listed] == ["Hiver"]

    # Doublon refusé, puis écrasement explicite accepté.
    duplicate = client.post("/admin/page-editor/announce/themes", json={
        "name": "Hiver", "payload": payload,
    })
    assert duplicate.status_code == 409
    assert duplicate.get_json()["exists"] is True
    overwrite = client.post("/admin/page-editor/announce/themes", json={
        "name": "Hiver", "payload": payload, "overwrite": True,
    })
    assert overwrite.status_code == 200
    assert overwrite.get_json()["theme"]["id"] == theme["id"]

    # Payload invalide et nom vide rejetés ; le snapshot est normalisé.
    bad = make_payload("announce")
    bad["css"]["title_font_size"] = "banane"
    assert client.post("/admin/page-editor/announce/themes", json={
        "name": "Bad", "payload": bad,
    }).status_code == 400
    assert client.post("/admin/page-editor/announce/themes", json={
        "name": "   ", "payload": payload,
    }).status_code == 400

    # Suppression.
    assert client.delete(
        f"/admin/page-editor/announce/themes/{theme['id']}"
    ).status_code == 200
    assert client.get(
        "/admin/page-editor/announce/themes"
    ).get_json()["themes"] == []
    assert client.delete(
        f"/admin/page-editor/announce/themes/{theme['id']}"
    ).status_code == 404

    anonymous = app.test_client()
    assert anonymous.get("/admin/page-editor/announce/themes").status_code == 401


def test_publish_is_atomic_and_detects_advanced_mode_conflict(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre publié"
    # Comme le vrai client : base_hash = empreinte de la version publiée
    # chargée dans l'éditeur (ici la configuration initiale du fixture).
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    ).get_json()

    with app.app_context():
        app.config["ANNOUNCE_SUBTITLE"] = "Modification avancée concurrente"
    conflict = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved["draft_version"]},
    )
    assert conflict.status_code == 409
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").first() is None
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json is not None


def _preview_ready_app(app):
    """Environnement Jinja minimal pour rendre le gabarit d'aperçu hors du
    serveur complet (mêmes globals que le parcours navigateur e2e)."""
    from jinja2 import FileSystemLoader

    from page_editor import preview_vars_style

    root = Path(__file__).resolve().parents[1]
    app.static_folder = str(root / "static")
    app.jinja_loader = FileSystemLoader(root / "templates")
    app.jinja_env.globals.update(
        csrf_token=lambda: "test",
        get_css_url=lambda mode=None: "/static/css/test.css",
        page_layout_style=layout_style,
        preview_vars_style=preview_vars_style,
        user_has_permission=lambda *args, **kwargs: True,
        page_editor_enabled=lambda page: True,
    )


def test_announce_display_flag_editable_despite_advanced_disabled(editor_app):
    """« Prochains patients » reste sélectionnable et affichable depuis
    l'éditeur même lorsque le réglage du mode avancé est désactivé :
    le flag est exposé dans l'inspecteur, l'aperçu suit le brouillon et la
    publication écrit la valeur choisie."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        app.config["ANNOUNCE_NEXT_PATIENTS_DISPLAY"] = False
        published_base = payload_hash(current_payload("announce"))

    document = client.get("/admin/page-editor/announce/state").get_json()
    next_fields = {
        field["key"] for field in document["adapter"]["components"]["next"]["config"]
    }
    assert "announce_next_patients_display" in next_fields
    assert document["adapter"]["components"]["next"]["managed_bool"] == \
        "announce_next_patients_display"

    # L'aperçu du composant publié masqué porte l'attribut qui permet au
    # brouillon de le réafficher en direct (pas de blocage figé).
    preview = client.get("/admin/page-editor/announce/preview").get_data(as_text=True)
    assert 'id="div_next_patients" data-page-editor-component="next" ' \
           'data-config-bool="announce_next_patients_display" data-config-hidden' in preview

    # L'utilisateur coche « Afficher la liste des prochains patients ».
    payload = make_payload("announce")
    payload["config"]["announce_next_patients_display"] = True
    payload["base_hash"] = published_base
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200

    preview = client.get("/admin/page-editor/announce/preview").get_data(as_text=True)
    assert 'id="div_next_patients" data-page-editor-component="next" ' \
           'data-config-bool="announce_next_patients_display" >' in preview

    published = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert published.status_code == 200
    with app.app_context():
        option = ConfigOption.query.filter_by(
            config_key="announce_next_patients_display").one()
        assert option.value_bool is True
        assert app.config["ANNOUNCE_NEXT_PATIENTS_DISPLAY"] is True


def test_announce_display_mode_select_hides_component_in_preview(editor_app):
    """Le mode d'affichage « Jamais affiché » est un réglage de l'éditeur et
    masque le composant dans l'aperçu piloté par le brouillon."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    payload = make_payload("announce")
    payload["config"]["announce_text_up_patients_display"] = "never"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200

    preview = client.get("/admin/page-editor/announce/preview").get_data(as_text=True)
    assert 'data-config-hide-values=\'["never"]\' data-config-hidden' in preview


def test_state_completes_legacy_draft_with_published_config(editor_app):
    """Un brouillon enregistré avant l'exposition d'un réglage reçoit la
    valeur publiée de la clé manquante : l'inspecteur ne présente pas une
    case décochée trompeuse."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    draft = make_payload("announce")
    del draft["config"]["announce_next_patients_display"]
    del draft["config"]["announce_next_patients_alignment"]
    draft["base_hash"] = payload_hash(draft)
    with app.app_context():
        state = PageEditorState(
            page_key="announce", draft_json=draft,
            draft_base_hash=draft["base_hash"], draft_version=7,
        )
        db.session.add(state)
        app.config["ANNOUNCE_NEXT_PATIENTS_DISPLAY"] = True
        db.session.commit()

    document = client.get("/admin/page-editor/announce/state").get_json()
    assert document["draft"]["config"]["announce_next_patients_display"] is True
    assert document["draft"]["config"]["announce_next_patients_alignment"] == "center"


def test_publish_accepts_legacy_draft_missing_managed_keys(editor_app):
    """La comparaison d'empreinte se limite aux clés connues du brouillon :
    les réglages ajoutés depuis ne bloquent pas la publication et sont
    publiés avec la valeur actuelle."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    # Empreinte de base = publié au moment de la création du brouillon,
    # calculée sur le même jeu de clés que le brouillon.
    base = make_payload("announce")
    del base["config"]["announce_next_patients_display"]
    draft = make_payload("announce")
    del draft["config"]["announce_next_patients_display"]
    draft["config"]["announce_title"] = "Titre issu d'un ancien brouillon"
    draft["base_hash"] = payload_hash(base)
    with app.app_context():
        state = PageEditorState(
            page_key="announce", draft_json=draft,
            draft_base_hash=draft["base_hash"], draft_version=4,
        )
        db.session.add(state)
        app.config["ANNOUNCE_NEXT_PATIENTS_DISPLAY"] = True
        db.session.commit()

    response = client.post(
        "/admin/page-editor/announce/publish", json={"draft_version": 4})
    assert response.status_code == 200
    with app.app_context():
        assert ConfigOption.query.filter_by(
            config_key="announce_next_patients_display").one().value_bool is True
        assert ConfigOption.query.filter_by(
            config_key="announce_title").one().value_str == \
            "Titre issu d'un ancien brouillon"


def test_publish_rolls_back_every_database_change_on_failure(editor_app, monkeypatch):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre à annuler"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    ).get_json()

    def fail_css_stage(source, key, value):
        raise RuntimeError("simulated CSS persistence failure")

    monkeypatch.setattr(app.css_variable_manager, "stage_variable", fail_css_stage)
    response = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved["draft_version"]},
    )
    assert response.status_code == 500
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").first() is None
        assert PageEditorRevision.query.filter_by(page_key="announce").count() == 0
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.published_revision == 0
        assert state.draft_json is not None


def test_publish_restore_and_revision_retention(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    draft_version = 0

    for revision_number in range(1, 12):
        payload = make_payload("announce")
        # Comme le vrai client : base_hash = empreinte de la version publiée
        # chargée dans l'éditeur, ici la révision précédente.
        previous = make_payload("announce")
        previous["config"]["announce_title"] = (
            "Texte de démonstration" if revision_number == 1
            else f"Titre {revision_number - 1}"
        )
        payload["base_hash"] = payload_hash(previous)
        payload["config"]["announce_title"] = f"Titre {revision_number}"
        saved_response = client.put(
            "/admin/page-editor/announce/draft",
            json={"draft_version": draft_version, "payload": payload},
        )
        assert saved_response.status_code == 200
        draft_version = saved_response.get_json()["draft_version"]
        published_response = client.post(
            "/admin/page-editor/announce/publish",
            json={"draft_version": draft_version},
        )
        assert published_response.status_code == 200
        draft_version = published_response.get_json()["draft_version"]

    with app.app_context():
        assert PageEditorRevision.query.filter_by(page_key="announce").count() == 10
        assert PageEditorRevision.query.filter_by(page_key="announce", revision=1).first() is None
        assert ConfigOption.query.filter_by(config_key="announce_title").one().value_str == "Titre 11"
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json is None
        assert state.published_revision == 11

    restored = client.post("/admin/page-editor/announce/revisions/2/restore", json={})
    assert restored.status_code == 200
    restored_version = restored.get_json()["draft_version"]
    with app.app_context():
        # La restauration charge la révision dans le brouillon sans publier :
        # la configuration courante reste inchangée tant que rien n'est publié.
        assert ConfigOption.query.filter_by(config_key="announce_title").one().value_str == "Titre 11"
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json["config"]["announce_title"] == "Titre 2"
    republished = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": restored_version},
    )
    assert republished.status_code == 200
    assert republished.get_json()["revision"] == 12
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").one().value_str == "Titre 2"
        assert PageEditorRevision.query.filter_by(page_key="announce").count() == 10


def test_revision_diff_compares_published_to_snapshot(editor_app):
    """``GET /revisions/<n>/diff`` rend « publié → révision », soit ce qu'une
    restauration changerait — le contenu du bouton « Comparer »."""
    app, _ = editor_app
    client = authenticated_client(editor_app)

    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre de la révision 1"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    published = client.post("/admin/page-editor/announce/publish",
                            json={"draft_version": saved["draft_version"]})
    assert published.status_code == 200

    # Comme le vrai client : le brouillon repart du document publié.
    second = client.get("/admin/page-editor/announce/state").get_json()["published"]
    second["config"]["announce_title"] = "Titre de la révision 2"
    saved = client.put("/admin/page-editor/announce/draft",
                       json={"draft_version": published.get_json()["draft_version"],
                             "payload": second})
    assert saved.status_code == 200
    published = client.post("/admin/page-editor/announce/publish",
                            json={"draft_version": saved.get_json()["draft_version"]})
    assert published.status_code == 200

    response = client.get("/admin/page-editor/announce/revisions/1/diff")
    assert response.status_code == 200
    diff = response.get_json()
    assert diff["identical"] is False
    title_change = next(
        change for change in diff["changes"] if change["key"] == "announce_title")
    assert title_change["component"] == "Titre"
    # Restaurer la révision 1 remettrait l'ancien titre à la place du publié.
    assert title_change["old"] == "Titre de la révision 2"
    assert title_change["new"] == "Titre de la révision 1"

    identical = client.get("/admin/page-editor/announce/revisions/2/diff")
    assert identical.status_code == 200
    assert identical.get_json()["identical"] is True


def test_revision_diff_unknown_and_permissions(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    assert app.test_client().get(
        "/admin/page-editor/announce/revisions/1/diff").status_code == 401

    unknown = client.get("/admin/page-editor/announce/revisions/42/diff")
    assert unknown.status_code == 404
    assert unknown.get_json()["error"] == "Révision inconnue."

    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        role.admin_announce = False
        db.session.commit()
    assert client.get(
        "/admin/page-editor/announce/revisions/1/diff").status_code == 403


def test_restore_preserves_shared_draft_and_detects_conflict(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)

    payload = make_payload("announce")
    saved = client.put("/admin/page-editor/announce/draft",
                       json={"draft_version": 0, "payload": payload}).get_json()
    published = client.post("/admin/page-editor/announce/publish",
                            json={"draft_version": saved["draft_version"]})
    assert published.status_code == 200
    draft_version = published.get_json()["draft_version"]

    other = make_payload("announce")
    other["config"]["announce_title"] = "Brouillon à protéger"
    saved = client.put("/admin/page-editor/announce/draft",
                       json={"draft_version": draft_version, "payload": other})
    draft_version = saved.get_json()["draft_version"]

    stale = client.post("/admin/page-editor/announce/revisions/1/restore",
                        json={"draft_version": draft_version - 1})
    assert stale.status_code == 409
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json["config"]["announce_title"] == "Brouillon à protéger"

    restored = client.post("/admin/page-editor/announce/revisions/1/restore",
                           json={"draft_version": draft_version})
    assert restored.status_code == 200
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json["config"]["announce_title"] == "Texte de démonstration"
        assert state.published_revision == 1


def test_advanced_change_between_load_and_first_save_blocks_publish(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    loaded = client.get("/admin/page-editor/announce/state").get_json()
    payload = loaded["published"]
    payload["config"]["announce_title"] = "Titre édité"

    # Changement en mode avancé entre l'ouverture de l'éditeur et la
    # première sauvegarde du brouillon : il ne doit pas être écrasé
    # silencieusement.
    with app.app_context():
        app.config["ANNOUNCE_SUBTITLE"] = "Modification avancée concurrente"

    saved = client.put("/admin/page-editor/announce/draft",
                       json={"draft_version": loaded["draft_version"],
                             "payload": payload})
    assert saved.status_code == 200
    conflict = client.post("/admin/page-editor/announce/publish",
                           json={"draft_version": saved.get_json()["draft_version"]})
    assert conflict.status_code == 409
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").first() is None


def _save_first_draft(client, payload):
    """Premier enregistrement d'un brouillon dont ``base_hash`` est
    l'empreinte du ``published`` chargé dans l'éditeur (client à jour)."""
    return client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )


def test_publish_conflict_lists_external_changes(editor_app):
    """Quand le client était à jour à la création du brouillon, le conflit
    détaille les réglages modifiés en mode avancé depuis."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre du brouillon"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_base_json is not None
        app.config["ANNOUNCE_SUBTITLE"] = "Modification avancée concurrente"
        app.config["ANNOUNCE_LAYOUT"]["title"]["span"] = 6

    conflict = client.post("/admin/page-editor/announce/publish",
                           json={"draft_version": saved["draft_version"]})
    assert conflict.status_code == 409
    data = conflict.get_json()
    assert data["code"] == "base_conflict"
    changes = {change["key"]: change for change in data["external_changes"]}
    assert changes["announce_subtitle"]["section"] == "config"
    assert changes["announce_subtitle"]["component"] == "Sous-titre"
    assert changes["announce_subtitle"]["new"] == \
        "Modification avancée concurrente"
    assert changes["span"]["section"] == "layout"
    assert changes["span"]["component"] == "Titre"


def test_publish_conflict_without_reference_reports_no_external_changes(editor_app):
    """Client désynchronisé au premier enregistrement : la référence du
    brouillon est inconnue, le conflit renvoie ``external_changes: null``."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    loaded = client.get("/admin/page-editor/announce/state").get_json()
    payload = loaded["published"]
    payload["config"]["announce_title"] = "Titre édité"
    with app.app_context():
        app.config["ANNOUNCE_SUBTITLE"] = "Modification avancée concurrente"
    saved = client.put("/admin/page-editor/announce/draft",
                       json={"draft_version": loaded["draft_version"],
                             "payload": payload})
    assert saved.status_code == 200
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_base_json is None
    conflict = client.post("/admin/page-editor/announce/publish",
                           json={"draft_version": saved.get_json()["draft_version"]})
    assert conflict.status_code == 409
    data = conflict.get_json()
    assert data["code"] == "base_conflict"
    assert data["external_changes"] is None


def test_rebase_keeps_draft_edits_and_takes_external_values(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre du brouillon"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    with app.app_context():
        app.config["ANNOUNCE_SUBTITLE"] = "Sous-titre externe"
    conflict = client.post("/admin/page-editor/announce/publish",
                           json={"draft_version": saved["draft_version"]})
    assert conflict.status_code == 409

    stale = client.post("/admin/page-editor/announce/draft/rebase",
                        json={"draft_version": saved["draft_version"] - 1})
    assert stale.status_code == 409

    rebased = client.post("/admin/page-editor/announce/draft/rebase",
                          json={"draft_version": saved["draft_version"]})
    assert rebased.status_code == 200
    data = rebased.get_json()
    assert data["draft"]["config"]["announce_title"] == "Titre du brouillon"
    assert data["draft"]["config"]["announce_subtitle"] == "Sous-titre externe"
    with app.app_context():
        current = current_payload("announce")
        assert data["draft"]["base_hash"] == payload_hash(current)
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_base_json == current

    published = client.post("/admin/page-editor/announce/publish",
                            json={"draft_version": data["draft_version"]})
    assert published.status_code == 200
    with app.app_context():
        assert ConfigOption.query.filter_by(
            config_key="announce_title").one().value_str == "Titre du brouillon"
        assert ConfigOption.query.filter_by(
            config_key="announce_subtitle").one().value_str == "Sous-titre externe"
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_base_json is None


def test_publish_force_applies_draft_despite_conflict(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre du brouillon"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    with app.app_context():
        app.config["ANNOUNCE_SUBTITLE"] = "Sous-titre externe"

    published = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved["draft_version"], "force": True},
    )
    assert published.status_code == 200
    with app.app_context():
        assert ConfigOption.query.filter_by(
            config_key="announce_title").one().value_str == "Titre du brouillon"
        # La publication complète écrase la modification externe.
        assert app.config["ANNOUNCE_SUBTITLE"] == "Texte de démonstration"
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json is None
        assert state.draft_base_json is None


def test_rebase_requires_an_existing_draft(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    missing = client.post("/admin/page-editor/announce/draft/rebase",
                          json={"draft_version": 0})
    assert missing.status_code == 404

    payload = make_payload("announce")
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    published = client.post("/admin/page-editor/announce/publish",
                            json={"draft_version": saved["draft_version"]})
    assert published.status_code == 200
    # L'état existe mais sans brouillon : 400.
    empty = client.post("/admin/page-editor/announce/draft/rebase",
                        json={"draft_version": published.get_json()["draft_version"]})
    assert empty.status_code == 400


def test_draft_base_json_cleared_on_delete(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = _save_first_draft(client, payload).get_json()
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_base_json is not None

    deleted = client.delete("/admin/page-editor/announce/draft",
                            json={"draft_version": saved["draft_version"]})
    assert deleted.status_code == 200
    with app.app_context():
        state = PageEditorState.query.filter_by(page_key="announce").one()
        assert state.draft_json is None
        assert state.draft_base_json is None


@pytest.mark.parametrize(
    ("key", "value", "ok"),
    [
        ("title_font_size", "banane", False),
        ("title_font_size", "32px", True),
        ("title_font_size", "calc(2rem + 4px)", True),
        ("title_font_color", "pas-une-couleur-123", False),
        ("title_font_color", "darkcyan", True),
        ("title_font_color", "rgb(10, 20, 30)", True),
        ("title_font_color", "#12ab34", True),
    ],
)
def test_css_values_are_checked_against_their_field_type(key, value, ok):
    payload = make_payload("announce")
    payload["css"][key] = value
    if ok:
        assert validate_payload("announce", payload)["css"][key] == value
    else:
        with pytest.raises(ValueError, match="CSS"):
            validate_payload("announce", payload)


# --- Écran « Erreur d'impression » (borne patient) ----------------------------

_PRINT_ERROR_CONFIG_KEYS = {
    field["key"]
    for field in ADAPTERS["patient"]["components"]["print_error"]["config"]
}


def _legacy_patient_draft():
    """Brouillon tel qu'enregistré avant l'ajout du composant « Erreur
    d'impression » : ni entrée de layout, ni réglages, ni variables CSS
    propres au composant."""
    draft = make_payload("patient")
    del draft["layout"]["print_error"]
    for key in [key for key in draft["css"] if key.startswith("print_error_")]:
        del draft["css"][key]
    for key in _PRINT_ERROR_CONFIG_KEYS & set(draft["config"]):
        del draft["config"][key]
    draft["base_hash"] = payload_hash(draft)
    return draft


def test_patient_adapter_exposes_print_error_scenario():
    """Le scénario et le composant « Erreur d'impression » existent sur la page
    patient : textes, comportement d'échec et apparence optionnelle (les champs
    optionnels acceptent les brouillons écrits avant leur ajout)."""
    adapter = ADAPTERS["patient"]
    assert {"id": "print_error", "label": "Erreur d'impression"} in adapter["scenarios"]

    component = adapter["components"]["print_error"]
    assert component["zone"] == "main"
    assert component["selector"] == "#print_status_overlay"
    assert component["scenarios"] == [
        "print_error", "printing", "print_uncertain", "staff_called"
    ]

    config_keys = {field["key"] for field in component["config"]}
    assert {
        "page_patient_print_fail_behavior",
        "page_patient_interface_print_failed",
        "page_patient_interface_no_ticket",
        "page_patient_interface_print_failed_staff",
        "page_patient_interface_retry",
        "page_patient_interface_call_staff",
    } <= config_keys

    css_fields = {field["key"]: field for field in component["css"]}
    for key in ("print_error_font_size", "print_error_font_color",
                "print_error_border_size", "print_error_border_color",
                "print_error_background_color", "print_error_number_size"):
        assert css_fields[key]["optional"] is True
        assert css_fields[key]["default"]


def test_print_error_text_fields_offer_call_number_marker():
    """{N} (numéro d'appel, résolu côté borne) est proposé dans la barre
    d'insertion des textes de l'écran d'erreur."""
    components = public_adapter_data("patient")["components"]
    fields = {field["key"]: field for field in components["print_error"]["config"]}
    for key in ("page_patient_interface_print_failed",
                "page_patient_interface_no_ticket",
                "page_patient_interface_print_failed_staff"):
        tokens = {marker["token"] for marker in fields[key]["markers"]}
        assert "{N}" in tokens


def test_validate_payload_accepts_draft_without_optional_print_error_css():
    """Un payload sans les variables optionnelles ``print_error_*`` reste
    valide : les anciens brouillons ne les connaissent pas."""
    payload = _legacy_patient_draft()
    # Le layout d'un ancien brouillon ne contient pas le composant : la
    # validation directe doit donc l'exiger (le complément est fait côté
    # routes, cf. _complete_payload).
    with pytest.raises(ValueError, match="composants"):
        validate_payload("patient", payload)

    payload["layout"] = complete_layout("patient", payload["layout"])
    assert validate_payload("patient", payload)["layout"]["print_error"]["zone"] == "main"


def test_complete_layout_and_css_fill_missing_entries():
    """``complete_layout``/``complete_css`` ajoutent les entrées apparues après
    l'enregistrement du brouillon, sans écraser celles déjà présentes."""
    draft = _legacy_patient_draft()
    draft["layout"]["title"] = {
        "zone": "header", "order": 5, "visible": False, "span": 6,
        "alignment": "left",
    }
    layout = complete_layout("patient", draft["layout"])
    assert layout["print_error"] == default_layout("patient")["print_error"]
    assert layout["title"]["visible"] is False

    # Sans gestionnaire de variables (contexte minimal), le défaut du champ
    # sert de repli.
    css = complete_css("patient", draft["css"])
    assert css["print_error_font_size"] == "50px"
    assert css["print_error_number_size"] == "80px"


def test_state_completes_legacy_draft_with_print_error_component(editor_app):
    """L'état servi à l'éditeur complète un brouillon antérieur : sans entrée
    de layout pour le composant, l'éditeur casserait au premier rendu."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    draft = _legacy_patient_draft()
    with app.app_context():
        db.session.add(PageEditorState(
            page_key="patient", draft_json=draft,
            draft_base_hash=draft["base_hash"], draft_version=3,
        ))
        db.session.commit()

    document = client.get("/admin/page-editor/patient/state").get_json()
    assert document["draft"]["layout"]["print_error"]["zone"] == "main"
    assert "print_error_font_size" in document["draft"]["css"]
    assert "page_patient_interface_print_failed" in document["draft"]["config"]


def test_publish_accepts_draft_predating_print_error_component(editor_app):
    """Publier directement un ancien brouillon ne doit pas échouer : le
    payload stocké est complété puis normalisé avant l'écriture."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    draft = _legacy_patient_draft()
    with app.app_context():
        base_hash = payload_hash(
            _draft_base_reference(current_payload("patient"), draft))
        db.session.add(PageEditorState(
            page_key="patient", draft_json=draft,
            draft_base_hash=base_hash, draft_version=5,
        ))
        db.session.commit()

    response = client.post("/admin/page-editor/patient/publish",
                           json={"draft_version": 5})
    assert response.status_code == 200

    with app.app_context():
        layout = app.config["PAGE_PATIENT_LAYOUT"]
        assert layout["print_error"]["zone"] == "main"
        assert app.css_variable_manager.values["patient"]["print_error_font_size"]


def test_theme_snapshot_is_completed_with_current_components_and_keys(editor_app):
    """Un thème enregistré avant l'apparition d'un composant ou d'un réglage
    est servi complété : l'appliquer dans l'éditeur ne doit pas produire un
    brouillon refusé à la sauvegarde (« structure incomplète »)."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        db.session.add(PageEditorTheme(
            page_key="patient", name="Ancien thème",
            snapshot_json=_legacy_patient_draft(),
        ))
        db.session.commit()

    themes = client.get("/admin/page-editor/patient/themes").get_json()["themes"]
    snapshot = themes[0]["snapshot"]
    assert snapshot["layout"]["print_error"]["zone"] == "main"
    assert "print_error_font_size" in snapshot["css"]
    assert "page_patient_interface_print_failed" in snapshot["config"]
    assert validate_payload("patient", snapshot)

    # La complétion porte sur la réponse : l'instantané stocké n'est pas
    # réécrit (il garde la trace de ce qui était réellement enregistré).
    with app.app_context():
        stored = PageEditorTheme.query.filter_by(
            page_key="patient", name="Ancien thème").one()
        assert "print_error" not in stored.snapshot_json["layout"]


def test_preview_home_renders_real_buttons(editor_app):
    """Régression : l'aperçu « Accueil » inclut les gabarits de boutons de la
    borne, qui attendent ``button_translations`` — son absence levait une
    UndefinedError dès qu'un bouton existait."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        activity = Activity(name="Ordonnances", letter="O")
        db.session.add_all([
            activity,
            Button(label="Déposer une ordonnance", activity=activity,
                   is_present=True, is_active=True, shape="square", sort_order=1),
        ])
        db.session.commit()

    response = client.get("/admin/page-editor/patient/preview?scenario=home")
    assert response.status_code == 200
    assert "Déposer une ordonnance" in response.get_data(as_text=True)


def test_patient_preview_reuses_real_validation_and_conclusion_markup(editor_app):
    """Les étapes sensibles ne doivent plus être des maquettes divergentes :
    elles réutilisent les mêmes gabarits que la borne et restent pilotables par
    le brouillon envoyé en postMessage."""
    client = authenticated_client(editor_app)
    _preview_ready_app(editor_app[0])

    validation = client.get(
        "/admin/page-editor/patient/preview?scenario=validation"
    ).get_data(as_text=True)
    assert 'id="div_confirmation"' in validation
    assert 'id="print_and_validate"' in validation
    assert 'data-config-key="page_patient_validation_message"' in validation
    assert 'data-config-bool="page_patient_display_button_scan"' in validation
    assert 'data-config-all-bool="page_patient_display_scan_explanation,page_patient_qrcode_display"' in validation

    conclusion = client.get(
        "/admin/page-editor/patient/preview?scenario=conclusion"
    ).get_data(as_text=True)
    assert 'id="div_conclusion"' in conclusion
    assert 'data-config-key="page_patient_confirmation_message"' in conclusion
    assert 'data-config-key="page_patient_interface_done_back"' in conclusion
    assert 'data-config-any-bool="page_patient_print_after_scan,page_patient_print_after_print"' in conclusion
    assert "patient_conclusion.js" not in conclusion


# --- Images des boutons de validation et réglages QR code --------------------

def test_patient_buttons_component_exposes_picture_and_qrcode_fields():
    """Le choix d'image des boutons « Imprimer »/« Annuler » et les réglages
    QR code sont exposés par l'éditeur visuel (auparavant réservés au mode
    avancé) avec le bon type de champ et le bon scénario."""
    fields = {
        field["key"]: field
        for field in public_adapter_data("patient")["components"]["buttons"]["config"]
    }
    for key in ("page_patient_button_print_ticket_picture",
                "page_patient_button_cancel_picture"):
        assert fields[key]["type"] == "image"
        assert fields[key]["scenarios"] == ["validation"]
    # Le booléen « image sur le bouton » ne renvoie plus vers le mode
    # avancé : le fichier se choisit dans l'éditeur.
    assert "help" not in fields["page_patient_button_print_ticket_display_picture"]
    for key in ("page_patient_qrcode_web_page",
                "page_patient_qrcode_data",
                "page_patient_qrcode_display_specific_message"):
        assert fields[key]["scenarios"] == ["validation", "conclusion"]
    # La barre de balises suit le validateur « before_call » du registre.
    tokens = {marker["token"]
              for marker in fields["page_patient_qrcode_data"]["markers"]}
    assert {"{N}", "{A}"} <= tokens


@pytest.mark.parametrize("value", ["imprimer.png", "annuler.webp", ""])
def test_image_fields_accept_simple_filenames_or_empty(value):
    """``""`` signifie « aucune image » ; sinon seul un nom de fichier
    simple avec une extension image connue est accepté."""
    payload = make_payload("patient")
    payload["config"]["page_patient_button_print_ticket_picture"] = value
    result = validate_payload("patient", payload)
    assert result["config"]["page_patient_button_print_ticket_picture"] == value


@pytest.mark.parametrize("value", [
    "../x.png", "a/b.png", "sous\\dossier.png", "C:\\x.png", "fichier.txt", 42,
])
def test_image_fields_reject_paths_and_non_image_values(value):
    payload = make_payload("patient")
    payload["config"]["page_patient_button_print_ticket_picture"] = value
    with pytest.raises(ValueError):
        validate_payload("patient", payload)


def test_button_images_endpoint_lists_real_files(editor_app):
    """La boîte « Choisir… » liste les fichiers réels du dépôt
    static/images/buttons (tri insensible à la casse, extension connue) ;
    l'endpoint exige la permission de la page."""
    app, _ = editor_app
    app.static_folder = str(Path(__file__).resolve().parents[1] / "static")
    client = authenticated_client(editor_app)
    response = client.get("/admin/page-editor/patient/images")
    assert response.status_code == 200
    images = response.get_json()["images"]
    assert "imprimer.png" in images
    assert images == sorted(images, key=str.casefold)
    assert all("/" not in name and "\\" not in name for name in images)

    assert app.test_client().get(
        "/admin/page-editor/patient/images").status_code == 401
    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        role.admin_patient = False
        db.session.commit()
    assert client.get("/admin/page-editor/patient/images").status_code == 403


def test_patient_validation_preview_uses_draft_pictures(editor_app):
    """En aperçu, les <img> des boutons Imprimer/Annuler sont toujours
    rendues (data-config-src) : leur source suit le brouillon et non la
    configuration publiée."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    payload = make_payload("patient")
    payload["config"]["page_patient_button_print_ticket_picture"] = "imprimer.png"
    payload["base_hash"] = payload_hash(make_payload("patient"))
    saved = client.put("/admin/page-editor/patient/draft",
                       json={"draft_version": 0, "payload": payload})
    assert saved.status_code == 200

    preview = client.get(
        "/admin/page-editor/patient/preview?scenario=validation"
    ).get_data(as_text=True)
    assert 'data-config-src="page_patient_button_print_ticket_picture"' in preview
    assert 'data-config-src="page_patient_button_cancel_picture"' in preview
    assert 'src="/static/images/buttons/imprimer.png"' in preview


def test_patient_children_preview_separates_back_button_from_activity_buttons(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        app.config["PAGE_PATIENT_INTERFACE_CHILDREN_BACK"] = "Retour"
        parent_activity = Activity(name="Accueil", letter="A")
        child_activity = Activity(name="Ordonnances", letter="O")
        parent = Button(
            label="Démarches", activity=parent_activity, is_parent=True,
            is_present=True, is_active=True, shape="square", sort_order=1,
        )
        child = Button(
            label="Ordonnances", activity=child_activity, parent_button=parent,
            is_present=True, is_active=True, shape="square", sort_order=1,
        )
        db.session.add_all([parent_activity, child_activity, parent, child])
        db.session.commit()

    preview = client.get(
        "/admin/page-editor/patient/preview?scenario=children"
    ).get_data(as_text=True)
    assert 'data-page-editor-component="back_button"' in preview
    marker = 'data-config-key="page_patient_interface_children_back">'
    assert marker in preview
    assert preview.split(marker, 1)[1].split("</p>", 1)[0].strip() == "Retour"


def test_patient_adapter_limits_layout_controls_and_filters_fields_by_scenario():
    adapter = public_adapter_data("patient")
    assert all(
        component["layout_controls"] == ["visible"]
        for component in adapter["components"].values()
    )
    fields = {
        field["key"]: field["scenarios"]
        for field in adapter["components"]["buttons"]["config"]
    }
    assert fields["page_patient_validation_message"] == ["validation"]
    assert fields["page_patient_confirmation_message"] == ["conclusion"]
    assert fields["page_patient_qrcode_display"] == ["validation", "conclusion"]
    assert fields["page_patient_button_shape"] == ["home"]
    assert fields["page_patient_children_button_shape"] == ["children"]
    shape_field = next(
        field for field in adapter["components"]["buttons"]["config"]
        if field["key"] == "page_patient_button_shape"
    )
    assert shape_field["choices"] == [
        ["mixed", "Formes actuelles différentes — choisir une forme"],
        ["circle", "Boutons ronds"],
        ["square", "Boutons rectangulaires"],
    ]
    css_fields = {
        field["key"]: field
        for field in adapter["components"]["buttons"]["css"]
    }
    # Les tailles/couleurs des formes sont communes aux deux niveaux ; la
    # forme qui les conditionne dépend du scénario.
    by_scenario = {"children": "page_patient_children_button_shape"}
    assert css_fields["circle_button_size"]["visible_when"] == {
        "key": "page_patient_button_shape", "values": ["circle"],
        "key_by_scenario": by_scenario,
    }
    assert css_fields["circle_button_size"]["scenarios"] == ["home", "children"]
    assert css_fields["square_button_width"]["visible_when"] == {
        "key": "page_patient_button_shape", "values": ["square"],
        "key_by_scenario": by_scenario,
    }
    assert css_fields["square_button_width"]["scenarios"] == ["home", "children"]
    assert not any(key.startswith("square_cancel_") for key in css_fields)
    back_button = adapter["components"]["back_button"]
    assert back_button["label"] == "Bouton Retour"
    assert back_button["scenarios"] == ["children"]
    assert [field["key"] for field in back_button["config"]] == [
        "page_patient_interface_children_back"
    ]
    assert {
        field["key"] for field in back_button["css"]
    } == {
        "square_cancel_button_width",
        "square_cancel_button_height",
        "square_cancel_button_color",
        "square_cancel_button_border_size",
        "square_cancel_button_border_color",
        "square_cancel_button_text_color",
        "square_cancel_button_text_size",
        "square_cancel_button_text_border_size",
        "square_cancel_button_text_border_color",
        "square_cancel_button_text_background_color",
        "square_cancel_button_image_size",
    }
    assert adapter["components"]["subtitle"]["label"] == "Sous-titre standard"
    assert adapter["components"]["activity_status"]["scenarios"] == [
        "activity_inactive"
    ]
    assert adapter["components"]["specific_message"]["scenarios"] == [
        "validation", "conclusion"
    ]
    status_keys = {
        field["key"]
        for field in adapter["components"]["activity_status"]["config"]
    }
    assert status_keys == {
        "page_patient_disable_button",
        "page_patient_disable_default_message",
        "page_patient_timer_activity_inactive",
    }


def test_patient_inactive_activity_preview_keeps_normal_buttons_and_marks_example(editor_app):
    """Sans activité planifiée, un seul bouton sert d'exemple et les autres
    restent normaux dans le cadre complet de l'accueil."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        activity = Activity(name="Vaccination", letter="V")
        other_activity = Activity(name="Ordonnances", letter="O")
        db.session.add_all([
            activity,
            other_activity,
            Button(label="Vaccination", activity=activity, is_present=True,
                   is_active=True, shape="square", sort_order=1),
            Button(label="Ordonnances", activity=other_activity, is_present=True,
                   is_active=True, shape="square", sort_order=2),
        ])
        db.session.commit()

    preview = client.get(
        "/admin/page-editor/patient/preview?scenario=activity_inactive"
    ).get_data(as_text=True)

    assert preview.count('class="page-editor-button-shape-preview"') == 3
    assert "div_square_bloc_activity" in preview
    assert "div_circle_buttons" in preview
    assert "div_square_button_inactive" in preview
    assert "div_square_button_active" in preview
    assert '"is_active": "False"' in preview
    assert '"is_active": "True"' in preview
    assert "Aucune activité n’est indisponible ou programmée" in preview
    assert preview.count("data-preview-inactive-button") == 3
    assert 'data-page-editor-component="activity_status"' in preview
    assert 'data-config-key="page_patient_disable_default_message"' in preview
    assert 'data-config-display-key="page_patient_disable_button"' in preview


def test_patient_inactive_activity_preview_uses_scheduled_activities(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        scheduled = Activity(name="Vaccination", letter="V")
        normal = Activity(name="Ordonnances", letter="O")
        scheduled.schedules.append(ActivitySchedule(name="Horaires vaccination"))
        db.session.add_all([
            scheduled,
            normal,
            Button(label="Vaccination", activity=scheduled, is_present=True,
                   is_active=True, shape="square", sort_order=1),
            Button(label="Ordonnances", activity=normal, is_present=True,
                   is_active=True, shape="square", sort_order=2),
        ])
        db.session.commit()

    preview = client.get(
        "/admin/page-editor/patient/preview?scenario=activity_inactive"
    ).get_data(as_text=True)

    assert "Aucune activité n’est indisponible ou programmée" not in preview
    assert preview.count("data-preview-inactive-button") == 3
    assert "div_square_button_inactive" in preview
    assert "div_square_button_active" in preview


def test_patient_shape_is_applied_to_all_buttons_only_on_publish(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        first_activity = Activity(name="Ordonnances", letter="O")
        second_activity = Activity(name="Vaccination", letter="V")
        db.session.add_all([
            first_activity,
            second_activity,
            Button(label="Ordonnances", activity=first_activity,
                   is_present=True, is_active=True, shape="circle", sort_order=1),
            Button(label="Vaccination", activity=second_activity,
                   is_present=True, is_active=True, shape="square", sort_order=2),
        ])
        app.config["PAGE_PATIENT_BUTTON_SHAPE"] = "mixed"
        db.session.commit()
        payload = current_payload("patient")
        payload["config"]["page_patient_button_shape"] = "square"
        payload["base_hash"] = payload_hash(current_payload("patient"))

    saved = client.put(
        "/admin/page-editor/patient/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200
    with app.app_context():
        assert {button.shape for button in Button.query.all()} == {
            "circle", "square"
        }

    published = client.post(
        "/admin/page-editor/patient/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert published.status_code == 200
    with app.app_context():
        assert {button.shape for button in Button.query.all()} == {"square"}
        assert ConfigOption.query.filter_by(
            config_key="page_patient_button_shape"
        ).one().value_str == "square"


def test_advanced_button_changes_switch_shape_selector_to_mixed(editor_app):
    """Une personnalisation bouton par bouton ne doit pas être écrasée à la
    prochaine ouverture de l'éditeur visuel."""
    app, _ = editor_app
    with app.app_context():
        first_activity = Activity(name="A", letter="A")
        second_activity = Activity(name="B", letter="B")
        first = Button(label="A", activity=first_activity, is_present=True,
                       is_active=True, shape="circle", sort_order=1)
        second = Button(label="B", activity=second_activity, is_present=True,
                        is_active=True, shape="square", sort_order=2)
        db.session.add_all([first_activity, second_activity, first, second])
        _sync_patient_button_shape_mode()
        db.session.commit()
        assert app.config["PAGE_PATIENT_BUTTON_SHAPE"] == "mixed"

        second.shape = "circle"
        _sync_patient_button_shape_mode()
        db.session.commit()
        assert app.config["PAGE_PATIENT_BUTTON_SHAPE"] == "circle"
        assert ConfigOption.query.filter_by(
            config_key="page_patient_button_shape"
        ).one().value_str == "circle"


def _add_parent_with_children(home_shape, children_shape):
    """Un bouton d'accueil simple, un bouton parent et deux sous-activités."""
    activities = [Activity(name=name, letter=name[0]) for name in
                  ("Ordonnances", "Tests", "Angine", "Cystite")]
    single = Button(label="Ordonnances", activity=activities[0], is_present=True,
                    is_active=True, shape=home_shape, sort_order=1)
    parent = Button(label="Tests", activity=activities[1], is_parent=True,
                    is_present=True, is_active=True, shape=home_shape, sort_order=2)
    children = [
        Button(label=label, activity=activity, parent_button=parent,
               is_present=True, is_active=True, shape=children_shape,
               sort_order=index)
        for index, (label, activity) in enumerate(
            (("Angine", activities[2]), ("Cystite", activities[3])), start=1)
    ]
    db.session.add_all([*activities, single, parent, *children])
    db.session.commit()
    return single, parent, children


def test_button_shape_modes_split_home_and_children_levels():
    from button_shapes import shape_mode, shape_modes

    assert shape_mode(["circle", "circle"]) == "circle"
    assert shape_mode(["circle", "square"]) == "mixed"
    # Niveau sans bouton : la valeur enregistrée est conservée.
    assert shape_mode([], fallback="square") == "square"
    assert shape_mode([None, "inconnue"], fallback=None) is None
    assert shape_modes(
        [(None, "square"), (None, "square"), (6, "circle"), (6, "circle")]
    ) == {"page_patient_button_shape": "square",
          "page_patient_children_button_shape": "circle"}
    assert shape_modes(
        [(None, "circle")], fallbacks={"page_patient_children_button_shape": "square"}
    )["page_patient_children_button_shape"] == "square"


def test_publish_applies_home_and_children_shapes_separately(editor_app):
    """Accueil en rectangles, sous-activités en ronds : chaque réglage ne
    modifie que les boutons de son niveau."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        _add_parent_with_children("circle", "circle")
        payload = current_payload("patient")
        payload["config"]["page_patient_button_shape"] = "square"
        payload["config"]["page_patient_children_button_shape"] = "circle"
        payload["base_hash"] = payload_hash(current_payload("patient"))

    saved = client.put(
        "/admin/page-editor/patient/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200
    published = client.post(
        "/admin/page-editor/patient/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert published.status_code == 200
    with app.app_context():
        shapes = {
            (button.parent_button_id is None, button.shape)
            for button in Button.query.all()
        }
        assert shapes == {(True, "square"), (False, "circle")}
        assert ConfigOption.query.filter_by(
            config_key="page_patient_children_button_shape"
        ).one().value_str == "circle"
        reloaded = current_payload("patient")["config"]
        assert reloaded["page_patient_button_shape"] == "square"
        assert reloaded["page_patient_children_button_shape"] == "circle"


def test_publish_keeps_mixed_children_shapes_untouched(editor_app):
    """Changer la forme de l'accueil ne réaligne plus les sous-activités que
    le mode avancé a différenciées."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        _, _, children = _add_parent_with_children("circle", "circle")
        children[1].shape = "square"
        db.session.commit()
        payload = current_payload("patient")
        assert payload["config"]["page_patient_children_button_shape"] == "mixed"
        payload["config"]["page_patient_button_shape"] = "square"
        payload["base_hash"] = payload_hash(current_payload("patient"))

    saved = client.put(
        "/admin/page-editor/patient/draft",
        json={"draft_version": 0, "payload": payload},
    )
    published = client.post(
        "/admin/page-editor/patient/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert published.status_code == 200
    with app.app_context():
        assert sorted(
            button.shape for button in Button.query.filter(
                Button.parent_button_id.isnot(None))
        ) == ["circle", "square"]
        assert {
            button.shape for button in Button.query.filter(
                Button.parent_button_id.is_(None))
        } == {"square"}


def test_published_shapes_follow_buttons_when_stored_reflection_is_stale(editor_app):
    """Installation mise à jour : la ligne « sous-activités » n'existe pas
    encore (défaut « circle ») alors que les sous-boutons sont rectangulaires.
    L'éditeur doit afficher la forme réelle, sinon publier les convertirait."""
    app, _ = editor_app
    with app.app_context():
        _add_parent_with_children("circle", "square")
        app.config["PAGE_PATIENT_CHILDREN_BUTTON_SHAPE"] = "circle"
        config = current_payload("patient")["config"]
        assert config["page_patient_button_shape"] == "circle"
        assert config["page_patient_children_button_shape"] == "square"
        completed = complete_config("patient", {})
        assert completed["page_patient_children_button_shape"] == "square"


def test_advanced_button_changes_sync_children_shape_separately(editor_app):
    app, _ = editor_app
    with app.app_context():
        _, _, children = _add_parent_with_children("circle", "square")
        _sync_patient_button_shape_mode()
        db.session.commit()
        assert app.config["PAGE_PATIENT_BUTTON_SHAPE"] == "circle"
        assert app.config["PAGE_PATIENT_CHILDREN_BUTTON_SHAPE"] == "square"

        children[0].shape = "circle"
        _sync_patient_button_shape_mode()
        db.session.commit()
        assert app.config["PAGE_PATIENT_BUTTON_SHAPE"] == "circle"
        assert app.config["PAGE_PATIENT_CHILDREN_BUTTON_SHAPE"] == "mixed"
        assert ConfigOption.query.filter_by(
            config_key="page_patient_children_button_shape"
        ).one().value_str == "mixed"


def test_children_preview_follows_children_shape_setting(editor_app):
    """L'aperçu « Sous-activités » bascule sur le réglage dédié, l'accueil
    garde le sien."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        _add_parent_with_children("square", "circle")

    children_preview = client.get(
        "/admin/page-editor/patient/preview?scenario=children"
    ).get_data(as_text=True)
    assert 'data-config-display-key="page_patient_children_button_shape"' in children_preview
    assert 'data-config-display-key="page_patient_button_shape"' not in children_preview

    home_preview = client.get(
        "/admin/page-editor/patient/preview?scenario=home"
    ).get_data(as_text=True)
    assert 'data-config-display-key="page_patient_button_shape"' in home_preview
    assert 'data-config-display-key="page_patient_children_button_shape"' not in home_preview


def test_patient_state_serves_journey_and_grouping_facts(editor_app):
    """La frise « parcours » reçoit sa définition et les boutons de
    regroupement réels (affichés, avec une sous-activité affichée)."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        _, parent, children = _add_parent_with_children("circle", "circle")
        empty_parent = Button(label="Vide", is_parent=True, is_present=True,
                              is_active=True, shape="circle", sort_order=3)
        db.session.add(empty_parent)
        db.session.commit()

    state = client.get("/admin/page-editor/patient/state").get_json()
    assert state["journey_facts"] == {"children_parents": ["Tests"]}
    steps = [step["id"] for step in state["adapter"]["journey"]["steps"]]
    assert steps == ["home", "children", "validation", "printing", "conclusion"]
    home_actions = state["adapter"]["journey"]["steps"][0]["actions"]
    assert {action["id"] for action in home_actions} == {
        "choose_activity", "choose_direct", "choose_group", "inactive",
    }
    printing = state["adapter"]["journey"]["steps"][3]
    assert [action["target"] for action in printing["actions"]] == [
        "conclusion", "print_error", "print_uncertain",
    ]

    announce = client.get("/admin/page-editor/announce/state").get_json()
    assert announce["adapter"]["journey"] is None
    assert announce["journey_facts"] == {}


@pytest.mark.e2e
def test_patient_journey_browser_flow(editor_app, playwright_driver):
    """Frise du parcours : clic sur une étape → aperçu du scénario et
    réglages ; « sauter » la validation met à jour badge, résumé et
    bandeau d'aperçu, et l'écriture va dans le brouillon."""
    from threading import Thread

    from jinja2 import FileSystemLoader
    from werkzeug.serving import make_server

    from page_editor import preview_vars_style

    playwright = pytest.importorskip("playwright.sync_api")
    app, _ = editor_app
    root = Path(__file__).resolve().parents[1]
    app.static_folder = str(root / "static")
    app.jinja_loader = FileSystemLoader(root / "templates")
    app.jinja_env.globals.update(
        csrf_token=lambda: "test", get_css_url=lambda mode=None: "/static/css/patient.css",
        page_layout_style=layout_style, preview_vars_style=preview_vars_style,
        user_has_permission=lambda *args, **kwargs: True, page_editor_enabled=lambda page: True,
    )
    with app.app_context():
        _add_parent_with_children("circle", "circle")
        app.config["PAGE_PATIENT_DIRECT_PRINT"] = False
        app.config["PAGE_PATIENT_DISPLAY_CONCLUSION"] = True
        app.config["PAGE_PATIENT_PRINT_UNCERTAIN_TIMER"] = 15
        app.config["PAGE_PATIENT_PRINT_UNCERTAIN_TIMEOUT_ACTION"] = "received"
    client = authenticated_client(editor_app)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with nullcontext(playwright_driver) as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": url}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{url}/admin/page-editor/patient")
                playwright.expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
                playwright.expect(page.locator("#editor-modes")).to_be_visible()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-editor-mode", "journey")
                playwright.expect(page.locator("#editor-components-panel")).to_be_hidden()
                playwright.expect(page.locator("#editor-scenario-field")).to_be_hidden()
                playwright.expect(page.locator("#editor-actions")).to_be_visible()
                playwright.expect(page.locator("#editor-workflow-publish")).to_have_attribute(
                    "data-state", "complete")
                playwright.expect(page.locator("#editor-workflow-publish-status")).to_have_text(
                    "Version publiée")

                # Les trois espaces ne cumulent plus leurs outils : Design
                # rend la palette et le scénario, Simulation pilote l'aperçu
                # depuis un chemin interactif, puis Parcours restaure la frise.
                page.get_by_role("tab", name="Design des écrans").click()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-editor-mode", "design")
                playwright.expect(page.locator("#editor-components-panel")).to_be_visible()
                playwright.expect(page.locator("#editor-scenario-field")).to_be_visible()
                playwright.expect(page.locator("#editor-journey")).to_be_hidden()
                page.get_by_role("tab", name="Simulation").click()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-editor-mode", "simulation")
                playwright.expect(page.locator("#editor-components-panel")).to_be_hidden()
                playwright.expect(page.locator("#editor-inspector-panel")).to_be_hidden()
                playwright.expect(page.locator("#editor-simulation-intro")).to_be_visible()
                playwright.expect(page.locator("#editor-scenario-field")).to_be_hidden()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Accueil")

                # Chemin avec sous-activité puis incident d'impression : les
                # actions suivent la configuration réelle et chaque écran
                # commande le scénario de l'aperçu.
                page.locator("[data-simulation-action='choose_group']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Sous-activités")
                playwright.expect(page.locator("#editor-scenario")).to_have_value("children")
                page.locator("[data-simulation-action='choose_child']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Validation")
                page.locator("[data-simulation-action='print']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Impression")
                page.locator("[data-simulation-action='print_uncertain']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Impression incertaine")
                page.locator("[data-simulation-action='ticket_missing']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Erreur d’impression")
                page.locator("[data-simulation-action='ask_back']").click()
                playwright.expect(page.locator("#editor-simulation-result")).to_contain_text(
                    "abandonnée après l’échec d’impression")
                playwright.expect(page.locator("#editor-simulation-trail li")).to_have_count(6)
                page.locator("#editor-simulation-reset").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Accueil")
                playwright.expect(page.locator("#editor-simulation-trail li")).to_have_count(1)
                page.get_by_role("tab", name="Parcours").click()
                journey = page.locator("#editor-journey")
                playwright.expect(journey).to_be_visible()
                playwright.expect(page.locator("#editor-journey-summary")).to_contain_text(
                    "Accueil → Sous-activités (selon le cas) → Validation")

                validation = page.locator("[data-journey-step='validation']")
                playwright.expect(validation).to_have_attribute("data-journey-state", "shown")
                validation.click()
                playwright.expect(page.locator("#editor-scenario")).to_have_value("validation")
                playwright.expect(validation).to_have_attribute("aria-pressed", "true")
                playwright.expect(page.locator("#editor-inspector")).to_contain_text("Étape 3 · Validation")
                toggle = page.locator("#editor-journey-page_patient_direct_print")
                playwright.expect(toggle).to_be_checked()
                scan = page.locator("#editor-journey-page_patient_display_button_scan")
                playwright.expect(scan).to_be_enabled()

                toggle.uncheck()
                playwright.expect(page.locator("#editor-workflow-draft")).to_have_attribute(
                    "data-state", "ready")
                playwright.expect(page.locator("#editor-workflow-draft-status")).to_have_text(
                    "Modifications à enregistrer")
                playwright.expect(page.locator("#editor-workflow-publish-status")).to_have_text(
                    "Enregistrer avant publication")
                playwright.expect(validation).to_have_attribute("data-journey-state", "hidden")
                playwright.expect(validation).to_contain_text("Sautée")
                playwright.expect(page.locator("#editor-journey-skipped")).to_be_visible()
                playwright.expect(scan).to_be_disabled()
                playwright.expect(page.locator("#editor-journey-summary")).not_to_contain_text("Validation")
                assert page.evaluate(
                    "JSON.parse(localStorage.getItem('page-editor-backup-patient')).payload.config.page_patient_direct_print"
                ) is True

                # Écran annexe : « Personnel appelé » disparaît si le bouton
                # n'est plus proposé.
                page.locator("[data-journey-step='staff_called']").click()
                playwright.expect(page.locator("#editor-scenario")).to_have_value("staff_called")
                page.locator("#editor-journey-page_patient_print_fail_show_staff").uncheck()
                playwright.expect(page.locator("[data-journey-step='staff_called']")).to_contain_text("Jamais")

                # Interrupteur « Afficher » de la frise : masque la conclusion.
                conclusion = page.locator("[data-journey-step='conclusion']")
                playwright.expect(conclusion).to_have_attribute("data-journey-state", "shown")
                page.locator("#editor-journey-toggle-conclusion").uncheck()
                playwright.expect(conclusion).to_have_attribute("data-journey-state", "hidden")
                playwright.expect(page.locator("#editor-journey-summary")).not_to_contain_text("Conclusion")
                assert page.evaluate(
                    "JSON.parse(localStorage.getItem('page-editor-backup-patient')).payload.config.page_patient_display_conclusion"
                ) is False
                # Étape non masquable : raison affichée, pas d'interrupteur.
                playwright.expect(page.locator("#editor-journey-toggle-home")).to_have_count(0)
                playwright.expect(page.locator("#editor-journey-path li").first).to_contain_text("Obligatoire")

                # Les écrans masqués sont réellement sautés dans le moteur :
                # impression directe puis retour à l'accueil sans conclusion.
                page.get_by_role("tab", name="Simulation").click()
                page.locator("#editor-simulation-reset").click()
                page.locator("[data-simulation-action='choose_group']").click()
                page.locator("[data-simulation-action='choose_child']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Impression")
                playwright.expect(page.locator("#editor-simulation-trail")).to_contain_text(
                    "sans passer par : Validation")
                page.locator("[data-simulation-action='print_ok']").click()
                playwright.expect(page.locator("#editor-simulation-step")).to_have_text("Accueil")
                playwright.expect(page.locator("#editor-simulation-result")).to_contain_text(
                    "l’écran « Conclusion » est masqué")
                page.get_by_role("tab", name="Parcours").click()

                # Bandeau « Sur la borne » : délai et suite donnée, valeurs du
                # brouillon en direct.
                page.locator("[data-journey-step='print_uncertain']").click()
                exit_note = page.locator("#editor-journey-skipped")
                playwright.expect(exit_note).to_contain_text("Sur la borne")
                playwright.expect(exit_note).to_contain_text("ticket considéré comme reçu")
                timer = page.locator("#editor-journey-page_patient_print_uncertain_timer")
                timer.fill("20")
                timer.press("Tab")
                playwright.expect(exit_note).to_contain_text("après 20 s")
                page.locator("#editor-journey-page_patient_print_uncertain_timeout_action").select_option("staff")
                playwright.expect(exit_note).to_contain_text("le comptoir vérifie")

                page.locator("[data-journey-step='staff_called']").click()

                # Raccourci vers un composant de l'écran.
                page.locator("#editor-inspector").get_by_role("button", name="Erreur d'impression").click()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-editor-mode", "design")
                playwright.expect(page.locator(".page-editor-component-context")).to_contain_text(
                    "Écran : Personnel appelé")
                playwright.expect(page.locator("[data-component-id='print_error']")).to_have_attribute("aria-pressed", "true")
                playwright.expect(page.locator("#editor-journey")).to_be_hidden()

                # Sous 1200 px l'inspecteur est un tiroir : la sélection d'une
                # étape l'ouvre, il se ferme sans laisser ses champs au clavier.
                page.set_viewport_size({"width": 1024, "height": 800})
                page.get_by_role("tab", name="Parcours").click()
                page.locator("[data-journey-step='validation']").click()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-inspector-open", "true")
                playwright.expect(page.locator("#editor-inspector-panel")).to_have_attribute("aria-hidden", "false")
                page.locator("#editor-inspector-close").click()
                playwright.expect(page.locator("#page-editor")).to_have_attribute("data-inspector-open", "false")
                playwright.expect(page.locator("#editor-inspector-panel")).to_have_attribute("aria-hidden", "true")

                # Le téléphone reste un accès de dépannage : les commandes
                # sont repliées par défaut et la barre ne monopolise pas la
                # hauteur de l'écran, mais toutes les actions restent accessibles.
                page.set_viewport_size({"width": 390, "height": 844})
                playwright.expect(page.locator("#editor-actions-toggle")).to_be_visible()
                playwright.expect(page.locator("#editor-actions")).to_be_hidden()
                playwright.expect(page.locator("#editor-actions")).to_have_attribute("aria-hidden", "true")
                assert page.locator(".page-editor-toolbar").evaluate(
                    "element => getComputedStyle(element).position") == "static"
                page.locator("#editor-actions-toggle").click()
                playwright.expect(page.locator("#editor-actions")).to_be_visible()
                playwright.expect(page.locator("#editor-actions")).to_have_attribute("aria-hidden", "false")
                playwright.expect(page.locator("#editor-workflow-draft")).to_be_visible()
                assert errors == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_print_error_preview_renders_overlay(editor_app):
    """L'aperçu du scénario reproduit l'overlay de la borne : message lié à la
    clé de config, numéro en grand masqué si le texte contient {N}, boutons du
    mode « Demander »."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)

    preview = client.get(
        "/admin/page-editor/patient/preview?scenario=print_error"
    ).get_data(as_text=True)

    assert 'id="print_status_overlay" data-page-editor-component="print_error"' in preview
    assert 'data-config-key="page_patient_interface_print_failed" data-number-styled' in preview
    assert 'data-print-error-number-for="page_patient_interface_print_failed"' in preview
    assert 'data-config-key="page_patient_interface_retry"' in preview
    # Sans « Réessayer » ni « Appeler le personnel », la borne propose
    # seulement le bouton de retour (libellé « Terminer ») : l'aperçu aussi.
    back = preview.split('data-config-hide-if-any="page_patient_print_fail_show_retry,page_patient_print_fail_show_staff"', 1)[1]
    assert 'data-config-key="page_patient_interface_done_back"' in back.split("</div>", 1)[0]
    # Les annotations de délai vivent dans l'éditeur, pas dans l'écran.
    assert "page-editor-flow-note" not in preview


def test_uncertain_and_staff_called_previews_match_kiosk(editor_app):
    """« Impression incertaine » : bouton « Appeler le personnel » piloté
    par son réglage et numéro en grand ; « Personnel appelé » : numéro en
    grand — comme bigNumberHtml sur la borne."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    with app.app_context():
        app.config["PAGE_PATIENT_PRINT_FAIL_SHOW_STAFF"] = False

    uncertain = client.get(
        "/admin/page-editor/patient/preview?scenario=print_uncertain"
    ).get_data(as_text=True)
    staff_button = uncertain.split('data-config-bool="page_patient_print_fail_show_staff"', 1)[1]
    assert staff_button.split(">", 1)[0].strip() == "data-config-hidden"
    assert 'data-config-key="page_patient_interface_call_staff"' in staff_button.split("</div>", 1)[0]
    assert 'data-print-error-number-for="page_patient_interface_print_uncertain"' in uncertain

    staff_called = client.get(
        "/admin/page-editor/patient/preview?scenario=staff_called"
    ).get_data(as_text=True)
    assert 'data-print-error-number-for="page_patient_interface_staff_called"' in staff_called


def test_preview_script_hides_back_button_when_a_choice_exists():
    source = (Path(__file__).resolve().parents[1] / "static/js/page_editor_preview.js").read_text(encoding="utf-8")
    assert "[data-config-hide-if-any]" in source


def test_patient_layout_uses_fixed_three_row_grid_without_forcing_overlay():
    """La borne conserve ses trois régions dans les 800 px disponibles ;
    l'overlay d'impression reste piloté par patients.js dans la zone centrale."""
    style = str(layout_style("patient"))
    assert "#main{display:grid" in style
    assert "grid-template-rows:auto minmax(0,1fr) auto" in style
    assert "#div_buttons_parents{grid-column:1;grid-row:2;height:auto" in style
    assert ".language-selector{order:" not in style
    assert "#print_status_overlay{order:" not in style
    assert "#print_status_overlay{display:none" not in style


def test_print_error_labels_registered_for_translation():
    """Les textes de l'écran d'impression rejoignent la liste des clés
    traduisibles."""
    from params_registry import TRANSLATABLE_CONFIG_KEYS
    keys = TRANSLATABLE_CONFIG_KEYS
    assert {
        "page_patient_interface_printing",
        "page_patient_interface_print_failed",
        "page_patient_interface_retry",
        "page_patient_interface_call_staff",
        "page_patient_interface_staff_called",
        "page_patient_interface_no_ticket",
        "page_patient_interface_print_failed_staff",
    } <= keys


# --- Cohérence des défauts CSS seedés avec le thème « Officine » -------------

@pytest.mark.parametrize("page,filename", [
    ("patient", "default_patient_css_variables.json"),
    ("announce", "default_announce_css_variables.json"),
    ("phone", "default_phone_css_variables.json"),
])
def test_default_css_variables_json_match_officine_theme(page, filename):
    """Les variables CSS seedées au premier démarrage portent les valeurs du
    thème par défaut « Officine » : sinon chaque bump de version du JSON
    réécrirait les anciennes couleurs par-dessus le thème (cf. régression
    borne repassée en « classique » au bump 0.22)."""
    from page_editor import builtin_themes

    path = Path(__file__).resolve().parents[1] / "static" / "json" / filename
    variables = json.loads(path.read_text(encoding="utf-8"))["variables"]
    theme = next(t for t in builtin_themes(page) if t["id"] == "builtin-officine")

    for key, expected in theme["snapshot"]["css"].items():
        assert variables.get(key) == str(expected), (
            f"{filename}: {key} = {variables.get(key)!r}, "
            f"attendu {expected!r} (thème Officine)")


# --- Fiabilité : libellés d'erreur, casse hex, audit, thèmes ----------------


def test_validation_errors_use_field_labels():
    """Les erreurs de validation citent le libellé affiché dans l'inspecteur
    (« Composant › Champ ») en conservant la clé technique entre
    parenthèses pour la journalisation et le support."""
    payload = make_payload("announce")
    payload["css"]["title_font_color"] = "banane"
    with pytest.raises(ValueError) as excinfo:
        validate_payload("announce", payload)
    message = str(excinfo.value)
    assert "Titre › Couleur du texte" in message
    assert "title_font_color" in message

    payload = make_payload("announce")
    payload["config"]["announce_infos_display_time"] = 0
    with pytest.raises(ValueError) as excinfo:
        validate_payload("announce", payload)
    message = str(excinfo.value)
    assert "Galerie › Durée d'affichage de chaque image" in message
    assert "announce_infos_display_time" in message

    # Clé inconnue de l'adaptateur : repli sur la clé brute.
    payload = make_payload("announce")
    payload["config"]["announce_infos_width"] = 49
    with pytest.raises(ValueError) as excinfo:
        validate_payload("announce", payload)
    assert "Galerie › Largeur de la galerie (announce_infos_width)" in \
        str(excinfo.value)


def test_payload_diff_ignores_hex_color_case(editor_app):
    """« #008B8B » publié (thème, palette) et « #008b8b » dans le brouillon
    sont la même couleur : la normalisation de la casse empêche le faux
    positif dans le diff."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        app.css_variable_manager.values["announce"]["title_font_color"] = "#008B8B"

    working = make_payload("announce")
    working["css"]["title_font_color"] = "#008b8b"
    diff = client.post(
        "/admin/page-editor/announce/diff", json={"payload": working}
    ).get_json()
    assert diff["identical"] is True
    assert diff["counts"] == {"config": 0, "css": 0, "layout": 0}
    assert diff["changes"] == []

    # Une couleur réellement différente ressort toujours, valeur normalisée.
    working["css"]["title_font_color"] = "#AA11BB"
    diff = client.post(
        "/admin/page-editor/announce/diff", json={"payload": working}
    ).get_json()
    assert diff["counts"]["css"] == 1
    change = diff["changes"][0]
    assert change["old"] == "#008b8b" and change["new"] == "#aa11bb"


def test_publish_syncs_parent_css_variables(editor_app):
    """À la publication, les variables « parentes » du mode avancé (entrées
    de cascade d'admin_colors.js, rôles de la copie de palettes — aucune
    règle CSS ne les consomme) sont réalignées sur la variable de l'éditeur
    qui incarne le rôle, dans la même transaction."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    for page, parents in _PARENT_CSS_REPRESENTATIVES.items():
        css_types = {
            field["key"]: field["type"]
            for component in ADAPTERS[page]["components"].values()
            for field in component["css"]
        }
        # Chaque représentant cité doit être un champ css de l'adaptateur.
        assert set(parents.values()) <= set(css_types)

        payload = make_payload(page)
        expected = {}
        for index, (parent, child) in enumerate(parents.items()):
            field_type = css_types[child]
            value = (
                f"#1a2b{index:x}c" if field_type == "color"
                else "37px" if field_type == "size"
                else "750"
            )
            payload["css"][child] = value
            expected[parent] = value
        payload["base_hash"] = payload_hash(make_payload(page))
        saved = client.put(
            f"/admin/page-editor/{page}/draft",
            json={"draft_version": 0, "payload": payload},
        )
        assert saved.status_code == 200
        published = client.post(
            f"/admin/page-editor/{page}/publish",
            json={"draft_version": saved.get_json()["draft_version"]},
        )
        assert published.status_code == 200
        source = ADAPTERS[page]["css_source"]
        for parent, value in expected.items():
            assert app.css_variable_manager.values[source][parent] == value


def test_save_draft_records_audit(editor_app):
    """PUT /draft journalise l'enregistrement comme les autres routes de
    l'éditeur (publish/delete/rebase)."""
    from models import AuditLog

    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    response = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert response.status_code == 200
    with app.app_context():
        entry = AuditLog.query.filter_by(
            resource="page_design_draft", action="update").one()
        assert entry.target == "announce"
        assert entry.outcome == "success"
        assert entry.details == "save"
        assert entry.username == "editor"


def test_theme_name_unique_in_database(editor_app):
    """L'unicité (page_key, name) est portée par le modèle et la base : un
    doublon inséré en contournant la route lève IntegrityError."""
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.exc import IntegrityError

    constraints = [
        arg for arg in PageEditorTheme.__table_args__
        if isinstance(arg, UniqueConstraint)
    ]
    assert any(
        {column.name for column in constraint.columns} == {"page_key", "name"}
        for constraint in constraints
    )

    app, _ = editor_app
    with app.app_context():
        db.session.add(PageEditorTheme(
            page_key="announce", name="Doublon", snapshot_json={}))
        db.session.commit()
        db.session.add(PageEditorTheme(
            page_key="announce", name="Doublon", snapshot_json={}))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_theme_overwrite_keeps_original_author(editor_app):
    """Un écrasement met à jour l'instantané et sa date mais conserve
    l'auteur d'origine du thème."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    with app.app_context():
        role = Role.query.filter_by(name="page-editor").one()
        other = User(username="relecteur", password="unused", active=True)
        other.roles.append(role)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    second = app.test_client()
    with second.session_transaction() as session:
        session["_user_id"] = str(other_id)
        session["_fresh"] = True

    payload = make_payload("announce")
    saved = client.post("/admin/page-editor/announce/themes", json={
        "name": "Saisons", "payload": payload})
    assert saved.status_code == 200
    theme_id = saved.get_json()["theme"]["id"]

    overwritten = second.post("/admin/page-editor/announce/themes", json={
        "name": "Saisons", "payload": payload, "overwrite": True})
    assert overwritten.status_code == 200
    theme = overwritten.get_json()["theme"]
    assert theme["id"] == theme_id
    assert theme["author"] == "editor"


# --- Sous-groupes de l'inspecteur et unités des champs numériques ----------


def test_patient_buttons_config_fields_are_grouped_and_contiguous():
    """§3.4 : les ~24 réglages de contenu du composant « Contenu principal »
    sont rangés en sous-groupes titrés, contigus pour n'afficher chaque
    titre qu'une fois."""
    fields = public_adapter_data("patient")["components"]["buttons"]["config"]
    seen = []
    for field in fields:
        group = field.get("group")
        assert group, f"{field['key']} sans sous-groupe"
        if not seen or seen[-1] != group:
            assert group not in seen, f"groupe « {group} » non contigu"
            seen.append(group)
    assert seen == [
        "Boutons d'activité",
        "QR code et scan",
        "Écran de validation",
        "Conclusion et délais",
    ]
    # « Imprimer directement » pilote l'apparition de l'écran de validation :
    # il ouvre son groupe.
    direct_print = next(
        field for field in fields if field["key"] == "page_patient_direct_print")
    assert direct_print["group"] == "Écran de validation"


def test_int_fields_expose_units_and_unit_free_labels():
    """§3.5 : les champs ``int`` portent leur unité (suffixe affiché par le
    client) ; les libellés n'en répètent plus la valeur."""
    announce_fields = {
        field["key"]: field
        for component in public_adapter_data("announce")["components"].values()
        for field in component["config"]
    }
    assert announce_fields["announce_infos_display_time"]["unit"] == "s"
    assert announce_fields["announce_infos_display_time"]["label"] == \
        "Durée d'affichage de chaque image"
    assert announce_fields["announce_infos_width"]["unit"] == "px"
    assert announce_fields["announce_infos_width"]["label"] == "Largeur de la galerie"
    assert announce_fields["announce_infos_height"]["unit"] == "px"
    assert announce_fields["announce_infos_height"]["label"] == "Hauteur de la galerie"

    patient_fields = {
        field["key"]: field
        for component in public_adapter_data("patient")["components"].values()
        for field in component["config"]
    }
    for key, label in (
        ("page_patient_end_timer", "Délai avant retour à l'accueil"),
        ("page_patient_timer_activity_inactive", "Durée d'affichage du message"),
        ("page_patient_print_fail_abandon_timer",
         "Retour auto à l'accueil si aucun choix (0 = jamais)"),
    ):
        assert patient_fields[key]["unit"] == "s"
        assert patient_fields[key]["label"] == label


# --- Panneau « Écrans » : identité des clients et rechargement ciblé -------


def test_register_client_records_ip_and_user_agent():
    """§3.8 : la connexion mémorise l'IP et l'agent du client — affichés
    dans le panneau « Écrans » de l'éditeur."""
    from flask import request as flask_request

    from sockets import connected_clients_info, register_client

    app = Flask("register-client")
    with app.test_request_context(
            "/", headers={"User-Agent": "Borne/2.0", "username": "borne hall"},
            environ_overrides={"REMOTE_ADDR": "192.0.2.20"}):
        flask_request.sid = "s-identite"
        register_client(flask_request)
    try:
        info = connected_clients_info["s-identite"]
        assert info["username"] == "borne hall"
        assert info["ip"] == "192.0.2.20"
        assert info["user_agent"] == "Borne/2.0"
    finally:
        connected_clients_info.pop("s-identite", None)


def test_screen_status_for_exposes_full_sid_ip_and_user_agent(monkeypatch):
    """Le sid complet sert au rechargement ciblé ; IP et agent identifient
    l'écran dans le panneau."""
    from types import SimpleNamespace

    from extensions import socketio
    from sockets import connected_clients_info, page_screen_status, screen_status_for

    sid = "sid-complet-0123456789"
    connected_clients_info[sid] = {
        "username": "écran hall", "ip": "192.0.2.10", "user_agent": "Borne/1.0"}
    page_screen_status["announce"][sid] = {
        "username": "écran hall", "revision": 2, "at": 1700000000.0}
    fake_manager = SimpleNamespace(
        rooms={"/socket_update_screen": {None: {sid}}})
    monkeypatch.setattr(socketio, "server", SimpleNamespace(manager=fake_manager))
    try:
        (screen,) = screen_status_for("announce")
        assert screen["sid"] == sid
        assert screen["ip"] == "192.0.2.10"
        assert screen["user_agent"] == "Borne/1.0"
        assert screen["username"] == "écran hall"
        assert screen["revision"] == 2
    finally:
        connected_clients_info.pop(sid, None)
        page_screen_status["announce"].clear()


def test_apply_targets_a_single_screen_or_404(editor_app, monkeypatch):
    """POST /apply avec ``sid`` connu émet le refresh dans la room de cet
    écran seul ; sid inconnu → 404 sans émission ; sans sid → diffusion."""
    from types import SimpleNamespace

    import routes.admin_page_editor as editor_module
    from extensions import socketio
    from models import AuditLog
    from sockets import connected_clients_info, page_screen_status

    app, _ = editor_app
    client = authenticated_client(editor_app)

    sid = "sid-complet-0123456789"
    connected_clients_info[sid] = {
        "username": "écran hall", "ip": "192.0.2.10", "user_agent": "Borne/1.0"}
    page_screen_status["announce"][sid] = {
        "username": "écran hall", "revision": 1, "at": 1700000000.0}
    fake_manager = SimpleNamespace(
        rooms={"/socket_update_screen": {None: {sid}}})
    monkeypatch.setattr(socketio, "server", SimpleNamespace(manager=fake_manager))
    calls = []
    monkeypatch.setattr(
        editor_module, "communikation",
        lambda *args, **kwargs: calls.append((args, kwargs)))
    try:
        # /screens expose le sid complet (tronqué seulement à l'affichage)
        # et l'identité de l'écran.
        data = client.get("/admin/page-editor/announce/screens").get_json()
        (item,) = [s for s in data["screens"] if s["sid"] == sid]
        assert item["ip"] == "192.0.2.10"
        assert item["user_agent"] == "Borne/1.0"

        unknown = client.post("/admin/page-editor/announce/apply",
                              json={"sid": "sid-inconnu-999"})
        assert unknown.status_code == 404
        assert unknown.get_json()["error"] == "Écran inconnu."
        assert calls == []

        targeted = client.post("/admin/page-editor/announce/apply",
                               json={"sid": sid})
        assert targeted.status_code == 200
        assert calls[-1] == (
            ("update_screen",), {"event": "refresh", "room": sid})
        with app.app_context():
            entry = AuditLog.query.filter_by(
                resource="page_design_screens").order_by(
                    AuditLog.id.desc()).first()
            assert entry.details == f"sid={sid}"

        broadcast = client.post("/admin/page-editor/announce/apply", json={})
        assert broadcast.status_code == 200
        assert calls[-1] == (("update_screen",), {"event": "refresh"})
    finally:
        connected_clients_info.pop(sid, None)
        page_screen_status["announce"].clear()


# --- Langue d'aperçu et alerte « traductions à revoir » (§4) ----------------


def _preview_language_setup(app, **translations):
    """Langue active « en » + traductions ConfigOption demandées
    (``{clé: texte}``) ; « nl » reste inactive pour couvrir le repli."""
    from models import Language, Translation

    with app.app_context():
        db.session.add(Language(
            code="en", name="Anglais", translation="English",
            is_active=True, sort_order=2))
        db.session.add(Language(
            code="nl", name="Néerlandais", translation="Nederlands",
            is_active=False, sort_order=3))
        for index, (key, text) in enumerate(translations.items()):
            db.session.add(Translation(
                table_name="ConfigOption", column_name="value_str",
                row_id=index + 1, key_name=key, language_code="en",
                translated_text=text))
        db.session.commit()


def test_state_lists_active_languages_for_preview_selector(editor_app):
    """Le document d'état expose les langues actives ; le client masque le
    sélecteur d'aperçu quand une seule langue est active."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_language_setup(app)
    languages = client.get(
        "/admin/page-editor/patient/state").get_json()["languages"]
    assert languages == [{"code": "en", "name": "Anglais"}]


def test_patient_preview_renders_draft_texts_in_preview_language(editor_app):
    """``?lang=xx`` remplace les textes traduisibles par leur traduction
    AVANT la substitution des balises ; traduction vide, code inconnu ou
    langue inactive retombent sur la source française."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    _preview_language_setup(
        app,
        page_patient_title="Welcome to {P}",
        page_patient_subtitle="",   # vide : repli sur la source
    )
    with app.app_context():
        app.config["PHARMACY_NAME"] = "Pharmacie du Centre"
        app.config["PAGE_PATIENT_TITLE"] = "Bienvenue à la {P}"
        app.config["PAGE_PATIENT_SUBTITLE"] = "Choisissez votre activité"

    translated = client.get(
        "/admin/page-editor/patient/preview?scenario=home&lang=en"
    ).get_data(as_text=True)
    assert "Welcome to Pharmacie du Centre" in translated
    # Traduction vide : le texte source reste affiché.
    assert "Choisissez votre activité" in translated
    # La table des traductions est embarquée pour les remplacements faits
    # en direct par page_editor_preview.js.
    assert '"page_patient_title": "Welcome to {P}"' in translated

    for url in (
        "/admin/page-editor/patient/preview?scenario=home",
        "/admin/page-editor/patient/preview?scenario=home&lang=zz",
        "/admin/page-editor/patient/preview?scenario=home&lang=nl",
    ):
        french = client.get(url).get_data(as_text=True)
        assert "Bienvenue à la Pharmacie du Centre" in french
        assert "Welcome to" not in french


def test_announce_preview_translates_call_text_format(editor_app):
    """Les « call_texts » de l'écran d'annonce suivent aussi ``?lang=``."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    _preview_language_setup(app, announce_call_text="Ticket {N} at {C}")
    with app.app_context():
        app.config["ANNOUNCE_CALL_TEXT"] = "Numéro {N} au {C}"

    preview = client.get(
        "/admin/page-editor/announce/preview?scenario=active&lang=en"
    ).get_data(as_text=True)
    assert "Ticket 042 at Comptoir 1" in preview
    assert "Numéro 042" not in preview


def test_phone_preview_renders_markdown_in_preview_language(editor_app):
    """Le markdown téléphone est traduit au rendu initial comme au
    re-rendu POST (``lang`` du corps, même validation que ``?lang=``)."""
    app, _ = editor_app
    client = authenticated_client(editor_app)
    _preview_ready_app(app)
    _preview_language_setup(app, phone_line1="**Number {N}**")
    with app.app_context():
        app.config["PHONE_LINE1"] = "**Numéro {N}**"

    preview = client.get(
        "/admin/page-editor/phone/preview?scenario=confirmation&lang=en"
    ).get_data(as_text=True)
    assert "<strong>Number 042</strong>" in preview

    payload = make_payload("phone")
    payload["config"]["phone_line1"] = "**Numéro {N}**"
    response = client.post(
        "/admin/page-editor/phone/preview/render",
        json={"payload": payload, "lang": "en"})
    assert response.status_code == 200
    assert "<strong>Number 042</strong>" in \
        response.get_json()["html"]["phone_line1"]

    response = client.post(
        "/admin/page-editor/phone/preview/render",
        json={"payload": payload, "lang": "zz"})
    assert "<strong>Numéro 042</strong>" in \
        response.get_json()["html"]["phone_line1"]


def test_publish_reports_translatable_texts_to_review(editor_app):
    """La réponse de publication liste les clés traduisibles dont la source
    change (``translation_review``) — le client propose alors d'ouvrir
    l'administration des traductions."""
    app, _ = editor_app
    client = authenticated_client(editor_app)

    payload = make_payload("announce")
    payload["config"]["announce_call_text"] = "Numéro {N}, comptoir {C}"
    payload["config"]["announce_title"] = "Bienvenue"   # non traduisible
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200
    response = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert response.status_code == 200
    assert response.get_json()["translation_review"] == [
        {"key": "announce_call_text", "label": "Format"}]


def test_publish_without_translatable_change_reports_empty_review(editor_app):
    """Une modification hors catalogue traduisible (titre de l'écran
    d'annonce, purement français) ne déclenche pas l'alerte."""
    app, _ = editor_app
    client = authenticated_client(editor_app)

    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Nouveau titre"
    payload["base_hash"] = payload_hash(make_payload("announce"))
    saved = client.put(
        "/admin/page-editor/announce/draft",
        json={"draft_version": 0, "payload": payload},
    )
    assert saved.status_code == 200
    response = client.post(
        "/admin/page-editor/announce/publish",
        json={"draft_version": saved.get_json()["draft_version"]},
    )
    assert response.status_code == 200
    assert response.get_json()["translation_review"] == []
