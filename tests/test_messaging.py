"""Contrat métier et HTTP de la messagerie App Comptoir."""

import uuid
from datetime import timedelta

import pytest
from flask import Flask

from auth_utils import generate_app_token
from models import (
    AppMessage, AppMessageRecipient, AppMessagingPresence,
    Counter, Pharmacist, db,
)
from routes.messaging import messaging_bp
from services import messaging_service as messaging


@pytest.fixture()
def messaging_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="messaging-test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        APP_MESSAGING_ENABLED=True,
    )
    db.init_app(app)
    app.register_blueprint(messaging_bp)
    with app.app_context():
        db.create_all()
        alice = Pharmacist(name="Alice", initials="AL", is_active=True)
        bob = Pharmacist(name="Bob", initials="BO", is_active=True)
        charlie = Pharmacist(name="Charlie", initials="CH", is_active=True)
        db.session.add_all([alice, bob, charlie])
        db.session.flush()
        counters = [
            Counter(name="1", staff=alice),
            Counter(name="2", staff=bob),
            Counter(name="3", staff=charlie),
        ]
        db.session.add_all(counters)
        db.session.commit()
        token = generate_app_token()
        ids = {"alice": alice.id, "bob": bob.id, "charlie": charlie.id}
    yield app, token, ids


@pytest.fixture()
def api(messaging_app):
    app, token, ids = messaging_app
    client = app.test_client()
    client.environ_base["HTTP_X_APP_TOKEN"] = token
    return app, client, ids


def _presence(client, counter_id, instance=None):
    instance = instance or str(uuid.uuid4())
    response = client.post("/api/messaging/presence", json={
        "counter_id": counter_id,
        "client_instance_id": instance,
    })
    assert response.status_code == 200
    return instance, response.get_json()


def _send(client, counter_id, **overrides):
    payload = {
        "counter_id": counter_id,
        "client_message_id": str(uuid.uuid4()),
        "kind": "direct",
        "recipient_staff_id": 2,
        "body": "Bonjour",
    }
    payload.update(overrides)
    return client.post("/api/messaging/messages", json=payload), payload


def test_routes_require_the_application_token(messaging_app):
    app, _token, _ids = messaging_app
    client = app.test_client()
    response = client.get("/api/messaging/state?counter_id=1")
    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_disabled_removes_state_and_rejects_actions(api):
    app, client, _ids = api
    app.config["APP_MESSAGING_ENABLED"] = False
    assert client.get("/api/messaging/state?counter_id=1").get_json() == {"enabled": False}
    response, _ = _send(client, 1)
    assert response.status_code == 403
    assert response.get_json()["error"] == "messaging_disabled"


def test_direct_message_is_idempotent_private_and_readable(api):
    app, client, ids = api
    _presence(client, 1)
    _presence(client, 2)
    response, payload = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    sent = response.get_json()
    message_id = sent["id"]
    assert sent["created_at"].endswith("Z")

    duplicate = client.post("/api/messaging/messages", json=payload)
    assert duplicate.status_code == 200
    with app.app_context():
        assert AppMessage.query.count() == 1
        assert AppMessageRecipient.query.count() == 1

    bob_state = client.get("/api/messaging/state?counter_id=2").get_json()
    assert bob_state["unread_total"] == 1
    bob_thread = client.get(
        f"/api/messaging/messages?counter_id=2&kind=direct&peer_staff_id={ids['alice']}"
    ).get_json()["messages"]
    assert [item["body"] for item in bob_thread] == ["Bonjour"]

    charlie_thread = client.get(
        f"/api/messaging/messages?counter_id=3&kind=direct&peer_staff_id={ids['alice']}"
    ).get_json()["messages"]
    assert charlie_thread == []

    read = client.post("/api/messaging/read", json={
        "counter_id": 2, "message_ids": [message_id],
    })
    assert read.get_json()["read"] == 1
    alice_thread = client.get(
        f"/api/messaging/messages?counter_id=1&kind=direct&peer_staff_id={ids['bob']}"
    ).get_json()["messages"]
    assert alice_thread[0]["read_at"] is not None


def test_broadcast_snapshots_only_active_recipients(api):
    app, client, ids = api
    _presence(client, 1)
    _presence(client, 2)
    response, _ = _send(client, 1, kind="broadcast", recipient_staff_id=None)
    assert response.status_code == 201
    assert response.get_json()["recipient_count"] == 1
    with app.app_context():
        recipient = AppMessageRecipient.query.one()
        assert recipient.recipient_staff_id == ids["bob"]


def test_expired_presence_refuses_direct_message_and_purge_removes_old_data(api):
    app, client, ids = api
    _presence(client, 1)
    bob_instance, _ = _presence(client, 2)
    with app.app_context():
        presence = db.session.get(AppMessagingPresence, bob_instance)
        presence.last_seen_at = messaging.now() - timedelta(seconds=91)
        db.session.commit()
    response, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 409
    assert response.get_json()["error"] == "recipient_offline"

    with app.app_context():
        old = AppMessage(
            client_message_id=str(uuid.uuid4()), kind="broadcast",
            sender_staff_id=ids["alice"], sender_counter_id=1,
            sender_name="Alice", sender_counter_name="1", body="ancien",
            created_at=messaging.now() - timedelta(days=8),
        )
        db.session.add(old)
        db.session.commit()
        assert messaging.purge_expired() == 1
        assert AppMessage.query.count() == 0
        assert AppMessageRecipient.query.count() == 0


def test_validation_and_server_side_identity(api):
    _app, client, ids = api
    _presence(client, 1)
    _presence(client, 2)
    response, _ = _send(
        client, 1, recipient_staff_id=ids["bob"], body=" ",
        sender_name="Personne usurpée",
    )
    assert response.status_code == 400
    response, _ = _send(
        client, 1, recipient_staff_id=ids["bob"], body="Valide",
        sender_name="Personne usurpée",
    )
    assert response.get_json()["sender"]["name"] == "Alice"

    too_long, _ = _send(
        client, 1, recipient_staff_id=ids["bob"], body="x" * 1001,
    )
    assert too_long.status_code == 400
    assert too_long.get_json()["error"] == "invalid_message"


def test_disabling_clears_all_presence(api, monkeypatch):
    app, client, _ids = api
    _presence(client, 1)
    _presence(client, 2)
    monkeypatch.setattr(messaging, "_emit", lambda *_args, **_kwargs: None)

    with app.app_context():
        assert AppMessagingPresence.query.count() == 2
        messaging.disable_and_clear_presence()
        assert AppMessagingPresence.query.count() == 0
