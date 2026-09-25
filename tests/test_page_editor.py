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
    expected = ["Officine", "Lisibilité renforcée", "Sauge & Lin", "Bleu Horizon", "Ardoise"]
    if page in ("announce", "patient"):
        expected.append("Classique")
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
    payload["css"].update(theme["snapshot"]["css"])
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
        values = sorted((luminance(foreground), luminance(background)))
        assert (values[1] + 0.05) / (values[0] + 0.05) >= 4.5, (theme["name"], page, key)
    if page == "patient":
        assert int(payload["css"]["square_button_height"].removesuffix("px")) >= 56
    if page == "announce" and theme_index == 1:
        assert payload["layout"]["gallery"]["visible"] is False
        assert "#div_center_divided{grid-template-columns:1fr}" in layout_style(page, payload["layout"])


@pytest.mark.parametrize("page", list(ADAPTERS))
def test_builtin_theme_can_be_saved_and_published_without_changing_content(editor_app, page):
    app, _ = editor_app
    client = authenticated_client(editor_app)
    initial = client.get(f"/admin/page-editor/{page}/state").get_json()["published"]
    theme = client.get(f"/admin/page-editor/{page}/themes").get_json()["builtins"][4]
    working = deepcopy(initial)
    working["css"].update(theme["snapshot"]["css"])
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


@pytest.mark.e2e
@pytest.mark.parametrize("page_key", list(ADAPTERS))
def test_builtin_themes_browser_flow(editor_app, page_key):
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
    client = authenticated_client(editor_app)
    before = client.get(f"/admin/page-editor/{page_key}/state").get_json()
    baseline = before["published"]
    theme_data = client.get(f"/admin/page-editor/{page_key}/themes").get_json()["builtins"]
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with playwright.sync_playwright() as driver:
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

                def working():
                    return page.evaluate("key => JSON.parse(localStorage.getItem('page-editor-backup-' + key)).payload", page_key)

                for theme in theme_data:
                    page.locator("#editor-themes").click()
                    row = page.locator(f"[data-theme-id='{theme['id']}']")
                    playwright.expect(row.locator(".btn-outline-danger")).to_have_count(0)
                    playwright.expect(row.locator(".page-editor-theme-swatches span")).to_have_count(5)
                    page.locator("#editor-theme-section-layout").uncheck()
                    page.locator("#editor-theme-section-content").check()
                    row.locator(".btn-primary").click()
                    playwright.expect(page.locator(".page-editor-dialog")).to_have_count(0)
                    applied = working()
                    assert applied["config"] == baseline["config"]
                    assert applied["layout"] == baseline["layout"]
                    assert applied["base_hash"] == baseline["base_hash"]
                    assert applied["css"] == theme["snapshot"]["css"]
                    preview = page.frame_locator("#editor-preview").locator("html")
                    playwright.expect(preview).to_have_css("--" + page_key + "_secondary_color", theme["snapshot"]["css"][page_key + "_secondary_color"])
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


def test_builtin_theme_snapshots_are_independent():
    from page_editor import builtin_themes

    first = builtin_themes("announce")
    first[0]["snapshot"]["css"]["title_font_color"] = "#000000"
    first[0]["snapshot"]["layout"]["title"]["span"] = 1
    fresh = builtin_themes("announce")
    assert fresh[0]["snapshot"]["css"]["title_font_color"] != "#000000"
    assert fresh[0]["snapshot"]["layout"]["title"]["span"] == 12


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
