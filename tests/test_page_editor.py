from copy import deepcopy

import pytest
from flask import Flask
from flask_login import LoginManager

from css_manager import CSSManager
from models import ConfigOption, PageEditorRevision, PageEditorState, Role, User, db
from page_editor import ADAPTERS, current_payload, default_layout, layout_style, payload_hash, validate_payload
from params_registry import get_spec
from routes.admin_page_editor import _render_phone_markdown, admin_page_editor_bp
from routes.admin_config import authorize_config_change


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
    app.css_variable_manager = DummyCssVariableManager(css_values)
    app.css_manager = DummyCssManager()
    app.register_blueprint(admin_page_editor_bp)

    with app.app_context():
        # L'extension db est partagée par toute la suite et peut conserver des
        # métadonnées de binds ajoutées par d'autres modules de tests.
        db.create_all(bind_key=None)
        role = Role(name="announce-editor", admin_announce=True)
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


@pytest.mark.parametrize("page", ["announce", "patient", "phone"])
def test_each_adapter_accepts_its_complete_payload(page):
    payload = make_payload(page)
    assert validate_payload(page, payload) == payload


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


def test_publish_is_atomic_and_detects_advanced_mode_conflict(editor_app):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    payload = make_payload("announce")
    payload["config"]["announce_title"] = "Titre publié"
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
    assert restored.get_json()["revision"] == 12
    with app.app_context():
        assert ConfigOption.query.filter_by(config_key="announce_title").one().value_str == "Titre 2"
        assert PageEditorRevision.query.filter_by(page_key="announce").count() == 10
