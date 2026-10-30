"""Phrases rapides du menu « ⚡ » de la messagerie (quick_replies.py).

1. règles pures : nettoyage, doublons, limites, lecture tolérante ;
2. réglage d'équipe : registre, validateur, valeur par défaut ;
3. état de messagerie : bloc ``quick_replies`` (équipe + personnelles) ;
4. ``PUT /api/messaging/quick_replies`` : identité par le comptoir, refus
   lisibles, aucune écriture chez un autre équipier.
"""

import json
from pathlib import Path

import pytest
from flask import Flask

import quick_replies as qr
from auth_utils import generate_app_token
from models import Counter, Pharmacist, db
from params_registry import get_spec
from routes.messaging import messaging_bp
from utils import validate_config_text

SERVER_DIR = Path(__file__).resolve().parents[1]


# --- règles pures -----------------------------------------------------------

def test_team_absent_key_gives_default_phrases():
    assert qr.team_phrases({}) == list(qr.DEFAULT_TEAM)
    assert qr.team_phrases(None) == list(qr.DEFAULT_TEAM)


def test_team_empty_value_means_no_team_phrase():
    assert qr.team_phrases({qr.TEAM_CONFIG_NAME: ""}) == []
    assert qr.team_phrases({qr.TEAM_CONFIG_NAME: "  \n \r\n"}) == []


def test_team_lines_are_cleaned_and_deduplicated():
    value = "  J'arrive \r\n\nj'arrive\nEn   réserve\n"
    assert qr.team_phrases({qr.TEAM_CONFIG_NAME: value}) == ["J'arrive", "En réserve"]


def test_team_reading_tolerates_out_of_limit_storage():
    too_long = "x" * (qr.MAX_LENGTH + 1)
    lines = [too_long] + [f"Phrase {i}" for i in range(qr.MAX_TEAM + 5)]
    phrases = qr.team_phrases({qr.TEAM_CONFIG_NAME: "\n".join(lines)})
    assert too_long not in phrases
    assert len(phrases) == qr.MAX_TEAM


def test_validate_team_text_normalizes():
    result = qr.validate_team_text(" A \r\n\n b  c\na\n")
    assert result == {"success": True, "value": "A\nb c"}
    assert qr.validate_team_text("") == {"success": True, "value": ""}


def test_validate_team_text_rejects_limits():
    too_many = "\n".join(f"P{i}" for i in range(qr.MAX_TEAM + 1))
    assert qr.validate_team_text(too_many)["success"] is False
    result = qr.validate_team_text("x" * (qr.MAX_LENGTH + 1))
    assert result["success"] is False
    assert str(qr.MAX_LENGTH) in result["value"]


def test_validate_personal():
    assert qr.validate_personal([" Je   reviens ", "", "je reviens", "Pause"]) == [
        "Je reviens", "Pause"]
    assert qr.validate_personal([]) == []
    for bad in (None, "texte", [1, 2], {"a": 1}):
        with pytest.raises(qr.QuickRepliesError):
            qr.validate_personal(bad)
    with pytest.raises(qr.QuickRepliesError):
        qr.validate_personal([f"P{i}" for i in range(qr.MAX_PERSONAL + 1)])
    with pytest.raises(qr.QuickRepliesError):
        qr.validate_personal(["x" * (qr.MAX_LENGTH + 1)])


def test_personal_phrases_reading_is_tolerant():
    assert qr.personal_phrases(None) == []
    assert qr.personal_phrases("pas une liste") == []
    assert qr.personal_phrases(["A", 3, None, "B"]) == ["A", "B"]


# --- réglage d'équipe ---------------------------------------------------------

def test_team_setting_is_registered_with_its_validator():
    spec = get_spec(qr.TEAM_KEY)
    assert spec is not None
    assert spec.config_name == qr.TEAM_CONFIG_NAME
    assert spec.value_type == "value_text"
    assert spec.permission == "app"
    assert spec.validator == "quick_replies"
    assert not spec.restart_required


def test_team_setting_goes_through_the_validator():
    assert validate_config_text(qr.TEAM_KEY, "a\nA\nb") == {
        "success": True, "value": "a\nb"}
    assert validate_config_text(
        qr.TEAM_KEY, "x" * (qr.MAX_LENGTH + 1))["success"] is False


def test_default_config_carries_the_default_team_phrases():
    data = json.loads((SERVER_DIR / "static/json/default_config.json")
                      .read_text(encoding="utf-8"))
    value = data["configurations"][qr.TEAM_KEY]
    assert qr.team_phrases({qr.TEAM_CONFIG_NAME: value}) == list(qr.DEFAULT_TEAM)


def test_admin_change_refreshes_the_apps():
    source = (SERVER_DIR / "routes/admin_config.py").read_text(encoding="utf-8")
    block = source.split('if key == "app_quick_replies":', 1)[1].split("return warning", 1)[0]
    assert "messaging_presence_changed" in block


def test_admin_general_tab_exposes_the_team_setting():
    source = (SERVER_DIR / "templates/admin/app_general.html").read_text(encoding="utf-8")
    assert 'data-config-key="app_quick_replies"' in source
    assert 'macros.button("app_quick_replies"' in source


# --- API messagerie -----------------------------------------------------------

@pytest.fixture()
def api():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="quick-replies-test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        APP_MESSAGING_ENABLED=True,
    )
    db.init_app(app)
    app.register_blueprint(messaging_bp)
    with app.app_context():
        db.create_all()
        alice = Pharmacist(name="Alice", initials="AL", is_active=True)
        bob = Pharmacist(name="Bob", initials="BO", is_active=True,
                         quick_replies=["Je suis en pause"])
        db.session.add_all([alice, bob])
        db.session.flush()
        db.session.add_all([Counter(name="1", staff=alice),
                            Counter(name="2", staff=bob),
                            Counter(name="3")])
        db.session.commit()
        ids = {"alice": alice.id, "bob": bob.id}
        token = generate_app_token()
    client = app.test_client()
    client.environ_base["HTTP_X_APP_TOKEN"] = token
    yield app, client, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_state_carries_team_and_personal_phrases(api):
    app, client, _ids = api
    state = client.get("/api/messaging/state?counter_id=2").get_json()
    assert state["quick_replies"] == {
        "team": list(qr.DEFAULT_TEAM),
        "personal": ["Je suis en pause"],
        "max_personal": qr.MAX_PERSONAL,
        "max_length": qr.MAX_LENGTH,
    }
    app.config[qr.TEAM_CONFIG_NAME] = "Au labo"
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    assert state["quick_replies"]["team"] == ["Au labo"]
    assert state["quick_replies"]["personal"] == []


def test_save_personal_phrases_for_the_connected_staff_only(api):
    app, client, ids = api
    response = client.put("/api/messaging/quick_replies", json={
        "counter_id": 1, "personal": [" Au labo ", "au labo", "Pause café"]})
    assert response.status_code == 200
    assert response.get_json()["personal"] == ["Au labo", "Pause café"]
    with app.app_context():
        assert db.session.get(Pharmacist, ids["alice"]).quick_replies == [
            "Au labo", "Pause café"]
        assert db.session.get(Pharmacist, ids["bob"]).quick_replies == [
            "Je suis en pause"]
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    assert state["quick_replies"]["personal"] == ["Au labo", "Pause café"]


def test_emptying_personal_phrases_clears_the_column(api):
    app, client, ids = api
    response = client.put("/api/messaging/quick_replies",
                          json={"counter_id": 2, "personal": []})
    assert response.status_code == 200
    assert response.get_json()["personal"] == []
    with app.app_context():
        assert db.session.get(Pharmacist, ids["bob"]).quick_replies is None


def test_save_rejects_invalid_lists_with_a_readable_message(api):
    _app, client, _ids = api
    response = client.put("/api/messaging/quick_replies", json={
        "counter_id": 1, "personal": ["x" * (qr.MAX_LENGTH + 1)]})
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_quick_replies"
    assert str(qr.MAX_LENGTH) in body["message"]
    response = client.put("/api/messaging/quick_replies",
                          json={"counter_id": 1, "personal": "pas une liste"})
    assert response.status_code == 400


def test_save_requires_a_staff_member_at_the_counter(api):
    _app, client, _ids = api
    response = client.put("/api/messaging/quick_replies",
                          json={"counter_id": 3, "personal": ["A"]})
    assert response.status_code == 409
    assert response.get_json()["error"] == "staff_not_connected"


def test_save_requires_token_and_enabled_messaging(api):
    app, client, _ids = api
    anonymous = app.test_client()
    assert anonymous.put("/api/messaging/quick_replies",
                         json={"counter_id": 1, "personal": []}).status_code == 401
    app.config["APP_MESSAGING_ENABLED"] = False
    response = client.put("/api/messaging/quick_replies",
                          json={"counter_id": 1, "personal": []})
    assert response.status_code == 403


def test_staff_backup_exports_personal_phrases():
    source = (SERVER_DIR / "backup_service.py").read_text(encoding="utf-8")
    export = source.split("class StaffSection", 1)[1].split("def restore_data", 1)[0]
    assert '"quick_replies": p.quick_replies' in export
