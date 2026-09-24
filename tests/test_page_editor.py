from copy import deepcopy
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager

from css_manager import CSSManager
from models import Activity, Button, ConfigOption, PageEditorRevision, PageEditorState, Role, User, db
from page_editor import (
    ADAPTERS,
    current_payload,
    default_layout,
    layout_style,
    palette_source_data,
    payload_hash,
    public_adapter_data,
    validate_payload,
)
from params_registry import get_spec
from routes.admin_page_editor import (
    _patient_preview_data,
    _preview_tokens,
    _render_phone_markdown,
    admin_page_editor_bp,
)
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
            config[field["key"]] = False if spec.value_type == "value_bool" else "Texte de démonstration"
        for field in component["css"]:
            css[field["key"]] = (
                "#008B8B" if field["type"] == "color"
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
    assert {role["id"] for role in palette} == {"primary", "secondary", "border"}
    assert all(role["keys"] for role in palette)
    assert all(set(role["keys"]) <= registered_colors for role in palette)


def test_palette_source_contains_only_published_role_values(editor_app):
    app, _ = editor_app
    with app.app_context():
        primary = next(
            role for role in public_adapter_data("patient")["palette"]
            if role["id"] == "primary"
        )
        for key in primary["keys"]:
            app.css_variable_manager.values["patient"][key] = "#123456"
        source = palette_source_data("patient")

    primary_source = next(role for role in source["roles"] if role["id"] == "primary")
    assert source["page"] == "patient"
    assert primary_source["values"] == ["#123456"] * len(primary["keys"])
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
    assert normalized["css"]["calling_background_color"] == "#008B8B"

def test_phone_markdown_preview_is_sanitized():
    html = _render_phone_markdown({
        "phone_line1": "**Numéro {N}** [lien](javascript:alert(1))"
    })["phone_line1"]
    assert "<strong>Numéro 042</strong>" in html
    assert "javascript:" not in html


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
