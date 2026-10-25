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


def test_delivered_then_read_timestamps_follow_recipient_app(api):
    # « Remis » : l'app de Bob apprend que le message existe en interrogeant
    # le serveur (heartbeat/état/fil) — distinct de « Lu », la consultation
    # réelle. Lu implique Remis, jamais l'inverse.
    _app, client, ids = api
    _presence(client, 1)
    response, _payload = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    message_id = response.get_json()["id"]

    def alice_thread():
        return client.get(
            "/api/messaging/messages?counter_id=1&kind=direct"
            f"&peer_staff_id={ids['bob']}"
        ).get_json()["messages"]

    # Bob n'a pas encore contacté le serveur : ni remis ni lu.
    assert alice_thread()[0]["delivered_at"] is None
    assert alice_thread()[0]["read_at"] is None
    # Heartbeat de Bob : son app sait désormais que le message existe.
    _presence(client, 2)
    assert alice_thread()[0]["delivered_at"] is not None
    assert alice_thread()[0]["read_at"] is None
    # Accusé de lecture : les deux horodatages sont renseignés.
    client.post("/api/messaging/read", json={
        "counter_id": 2, "message_ids": [message_id],
    })
    assert alice_thread()[0]["delivered_at"] is not None
    assert alice_thread()[0]["read_at"] is not None


def test_read_implies_delivered_even_without_prior_delivery(api):
    # Lecture du fil sans contact préalable de l'app (jamais « remise ») :
    # l'accusé « Lu » pose aussi « Remis » — Lu implique Remis.
    _app, client, ids = api
    _presence(client, 1)
    response, _payload = _send(client, 1, recipient_staff_id=ids["bob"])
    message_id = response.get_json()["id"]
    read = client.post("/api/messaging/read", json={
        "counter_id": 2, "message_ids": [message_id],
    })
    assert read.get_json()["read"] == 1
    alice_thread = client.get(
        f"/api/messaging/messages?counter_id=1&kind=direct&peer_staff_id={ids['bob']}"
    ).get_json()["messages"]
    assert alice_thread[0]["delivered_at"] is not None
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


def test_expired_presence_accepts_direct_message_and_purge_removes_old_data(api):
    app, client, ids = api
    _presence(client, 1)
    bob_instance, _ = _presence(client, 2)
    with app.app_context():
        presence = db.session.get(AppMessagingPresence, bob_instance)
        presence.last_seen_at = messaging.now() - timedelta(seconds=91)
        db.session.commit()
    # Envoyer à un équipier actif mais hors ligne est désormais accepté :
    # le message l'attendra à sa prochaine connexion.
    response, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    with app.app_context():
        recipient = AppMessageRecipient.query.one()
        assert recipient.recipient_staff_id == ids["bob"]
        assert recipient.recipient_counter_id is None
        assert recipient.recipient_counter_name is None

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
        # Seul le vieux message est purgé ; le direct envoyé plus haut reste.
        assert AppMessage.query.count() == 1
        assert AppMessageRecipient.query.count() == 1


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


def test_state_lists_every_registered_staff_member_online_or_offline(api):
    app, client, ids = api
    _presence(client, 1)
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    directs = {
        item["staff_id"]: item
        for item in state["conversations"] if item["kind"] == "direct"
    }
    assert set(directs) == {ids["bob"], ids["charlie"]}
    assert ids["alice"] not in directs
    for entry in directs.values():
        assert entry["online"] is False
        assert entry["counter_id"] is None
        assert entry["counter_name"] is None
    assert state["online_count"] == 0

    _presence(client, 2)
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    directs = {
        item["staff_id"]: item
        for item in state["conversations"] if item["kind"] == "direct"
    }
    assert directs[ids["bob"]]["online"] is True
    assert directs[ids["bob"]]["counter_id"] == 2
    assert directs[ids["bob"]]["counter_name"] == "2"
    assert directs[ids["charlie"]]["online"] is False
    assert state["online_count"] == 1

    # Le flag « is_active » de l'admin n'est pas un critère d'annuaire : il
    # n'est pas alimenté en pratique (tous les équipiers réels sont à 0) —
    # Charlie reste joignable même désactivé.
    with app.app_context():
        charlie = db.session.get(Pharmacist, ids["charlie"])
        charlie.is_active = False
        db.session.commit()
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    directs = {
        item["staff_id"]: item
        for item in state["conversations"] if item["kind"] == "direct"
    }
    assert set(directs) == {ids["bob"], ids["charlie"]}


def test_state_conversations_carry_staff_initials(api):
    # Les initiales métier (colonne Pharmacist.initials, utilisée à la
    # connexion) accompagnent le nom dans l'annuaire messagerie : l'App
    # Comptoir les affiche et les matche dans sa recherche « Écrire à… ».
    _app, client, ids = api
    _presence(client, 1)
    _presence(client, 2)
    state = client.get("/api/messaging/state?counter_id=1").get_json()
    directs = {
        item["staff_id"]: item
        for item in state["conversations"] if item["kind"] == "direct"
    }
    # En ligne (dict de présence) comme hors ligne (annuaire complet).
    assert directs[ids["bob"]]["initials"] == "BO"
    assert directs[ids["charlie"]]["initials"] == "CH"


def test_heartbeat_after_expiration_emits_presence_changed(api, monkeypatch):
    app, client, _ids = api
    events = []
    monkeypatch.setattr(
        messaging, "_emit",
        lambda event, data=None: events.append(event),
    )
    instance, _ = _presence(client, 2)
    assert events == ["messaging_presence_changed"]

    # Heartbeat normal d'une présence encore fraîche : rien à signaler.
    events.clear()
    _presence(client, 2, instance=instance)
    assert events == []

    # Le poste revient après expiration : les autres doivent rafraîchir.
    with app.app_context():
        presence = db.session.get(AppMessagingPresence, instance)
        presence.last_seen_at = messaging.now() - timedelta(seconds=91)
        db.session.commit()
    _presence(client, 2, instance=instance)
    assert events == ["messaging_presence_changed"]


def test_direct_message_to_offline_staff_is_stored_and_read_on_connect(api):
    app, client, ids = api
    _presence(client, 1)
    response, _ = _send(client, 1, recipient_staff_id=ids["charlie"])
    assert response.status_code == 201
    with app.app_context():
        recipient = AppMessageRecipient.query.one()
        assert recipient.recipient_staff_id == ids["charlie"]
        assert recipient.recipient_counter_id is None
        assert recipient.recipient_counter_name is None

    # Charlie se connecte : le message l'attend comme non lu.
    _presence(client, 3)
    state = client.get("/api/messaging/state?counter_id=3").get_json()
    assert state["unread_total"] == 1


def test_broadcast_scope_all_online_and_invalid(api):
    app, client, ids = api
    _presence(client, 1)
    _presence(client, 2)
    response, _ = _send(
        client, 1, kind="broadcast", recipient_staff_id=None, scope="all",
    )
    assert response.status_code == 201
    assert response.get_json()["recipient_count"] == 2
    with app.app_context():
        recipients = AppMessageRecipient.query.all()
        assert {row.recipient_staff_id for row in recipients} == {
            ids["bob"], ids["charlie"],
        }
        offline = next(
            row for row in recipients
            if row.recipient_staff_id == ids["charlie"]
        )
        assert offline.recipient_counter_id is None
        assert offline.recipient_counter_name is None

    response, _ = _send(
        client, 1, kind="broadcast", recipient_staff_id=None, scope="bogus",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_message"

    # scope absent → "online" : seules les présences actives (comportement
    # des anciens clients).
    response, _ = _send(client, 1, kind="broadcast", recipient_staff_id=None)
    assert response.status_code == 201
    assert response.get_json()["recipient_count"] == 1


def test_yesterday_unread_message_stays_visible_until_read(api):
    app, client, ids = api
    _presence(client, 1)
    response, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    message_id = response.get_json()["id"]
    with app.app_context():
        message = db.session.get(AppMessage, message_id)
        message.created_at = messaging.now() - timedelta(days=1)
        db.session.commit()

    bob_url = (
        "/api/messaging/messages?counter_id=2&kind=direct"
        f"&peer_staff_id={ids['alice']}"
    )
    assert [m["id"] for m in client.get(bob_url).get_json()["messages"]] == [message_id]
    assert client.get("/api/messaging/state?counter_id=2").get_json()["unread_total"] == 1
    # L'expéditeur le voit aussi tant qu'un destinataire ne l'a pas lu.
    alice_url = (
        "/api/messaging/messages?counter_id=1&kind=direct"
        f"&peer_staff_id={ids['bob']}"
    )
    assert [m["id"] for m in client.get(alice_url).get_json()["messages"]] == [message_id]

    client.post("/api/messaging/read", json={
        "counter_id": 2, "message_ids": [message_id],
    })
    assert client.get(bob_url).get_json()["messages"] == []
    assert client.get(alice_url).get_json()["messages"] == []


def test_direct_to_self_or_unknown_staff_is_rejected(api):
    _app, client, ids = api
    _presence(client, 1)
    response, _ = _send(client, 1, recipient_staff_id=ids["alice"])
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_message"

    response, _ = _send(client, 1, recipient_staff_id=999999)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_message"


def test_direct_to_staff_flagged_inactive_is_still_accepted(api):
    # « is_active » n'est pas utilisé par la connexion par initiales ni par
    # l'annuaire : un équipier désactivé reste joignable tant qu'il est
    # enregistré — le message l'attendra à sa connexion.
    app, client, ids = api
    _presence(client, 1)
    with app.app_context():
        charlie = db.session.get(Pharmacist, ids["charlie"])
        charlie.is_active = False
        db.session.commit()
    response, _ = _send(client, 1, recipient_staff_id=ids["charlie"])
    assert response.status_code == 201


def test_disabling_clears_all_presence(api, monkeypatch):
    app, client, _ids = api
    _presence(client, 1)
    _presence(client, 2)
    monkeypatch.setattr(messaging, "_emit", lambda *_args, **_kwargs: None)

    with app.app_context():
        assert AppMessagingPresence.query.count() == 2
        messaging.disable_and_clear_presence()
        assert AppMessagingPresence.query.count() == 0


# --- Diffusion ciblée des évènements de messagerie -----------------------------
# Les évènements « messaging_* » d'un échange privé ne doivent plus atteindre
# tous les comptoirs : chaque App déclare sa salle ``messaging_counter_<id>``
# (handler ``join_messaging_room`` de sockets.py) et le service n'émet que
# vers les salles concernées.

def _spy_communikation(monkeypatch):
    calls = []

    def _fake(stream, data=None, flag=None, event="update",
              client_id=None, room=None):
        calls.append({
            "stream": stream, "data": data, "event": event, "room": room,
        })

    monkeypatch.setattr(messaging, "communikation", _fake)
    return calls


def _changed_events(calls):
    return [call for call in calls if call["event"] == "messaging_changed"]


def test_direct_message_targets_only_sender_and_recipient_counters(
        api, monkeypatch):
    _app, client, ids = api
    calls = _spy_communikation(monkeypatch)
    _presence(client, 1)
    _presence(client, 2)
    calls.clear()

    response, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    changed = _changed_events(calls)
    assert {call["room"] for call in changed} == {
        "messaging_counter_1", "messaging_counter_2"}
    assert all(call["stream"] == "app_counter" for call in changed)
    # Le payload du signal est inchangé : jamais le contenu du message.
    assert {call["data"]["kind"] for call in changed} == {"direct"}
    assert all("body" not in call["data"] for call in changed)


def test_direct_message_to_offline_recipient_targets_sender_only(
        api, monkeypatch):
    _app, client, ids = api
    calls = _spy_communikation(monkeypatch)
    _presence(client, 1)
    calls.clear()

    response, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    assert response.status_code == 201
    changed = _changed_events(calls)
    assert [call["room"] for call in changed] == ["messaging_counter_1"]


def test_broadcast_message_keeps_emitting_to_everyone(api, monkeypatch):
    _app, client, _ids = api
    calls = _spy_communikation(monkeypatch)
    _presence(client, 1)
    _presence(client, 2)
    calls.clear()

    response, _ = _send(
        client, 1, kind="broadcast", recipient_staff_id=None, scope="all",
    )
    assert response.status_code == 201
    changed = _changed_events(calls)
    assert len(changed) == 1
    assert changed[0]["room"] is None


def test_mark_read_targets_reader_and_sender_counters(api, monkeypatch):
    _app, client, ids = api
    calls = _spy_communikation(monkeypatch)
    _presence(client, 1)
    _presence(client, 2)
    _presence(client, 3)
    first, _ = _send(client, 1, recipient_staff_id=ids["bob"])
    second, _ = _send(client, 3, recipient_staff_id=ids["bob"])
    ids_read = [first.get_json()["id"], second.get_json()["id"]]
    calls.clear()

    response = client.post("/api/messaging/read", json={
        "counter_id": 2, "message_ids": ids_read,
    })
    assert response.get_json()["read"] == 2
    changed = _changed_events(calls)
    assert {call["room"] for call in changed} == {
        "messaging_counter_1", "messaging_counter_2", "messaging_counter_3"}
    assert all(call["data"] == {"read": True} for call in changed)


def test_presence_and_config_signals_stay_broadcast(api, monkeypatch):
    _app, client, _ids = api
    calls = _spy_communikation(monkeypatch)
    _presence(client, 1)
    assert any(
        call["event"] == "messaging_presence_changed" and call["room"] is None
        for call in calls
    )

    calls.clear()
    with _app.app_context():
        messaging.announce_enabled()
        messaging.disable_and_clear_presence()
    config = [c for c in calls if c["event"] == "messaging_config_changed"]
    assert len(config) == 2
    assert all(call["room"] is None for call in config)


# --- Handler ``join_messaging_room`` sur /socket_app_counter ------------------

def _init_counter_sockets(app):
    from extensions import socketio
    import sockets  # noqa: F401 — enregistre les handlers sur l'objet partagé

    # init_app instancie un nouveau Server Socket.IO : une seule fois par
    # test, AVANT de créer les clients — un second appel les déconnecterait.
    socketio.init_app(app)
    return socketio


def _counter_socket(socketio, app, client):
    return socketio.test_client(
        app, flask_test_client=client, namespace="/socket_app_counter")


def test_join_messaging_room_delivers_only_to_that_counter(messaging_app):
    """Un comptoir ne reçoit que les évènements adressés à sa salle ; un
    client qui n'a pas déclaré son comptoir ne reçoit plus les signaux
    ciblés, mais toujours les diffusions globales."""
    app, token, _ids = messaging_app
    from communication import communikation

    socketio = _init_counter_sockets(app)
    client = app.test_client()
    sio_a = _counter_socket(socketio, app, client)
    sio_b = _counter_socket(socketio, app, client)
    sio_muet = _counter_socket(socketio, app, client)
    assert sio_a.is_connected("/socket_app_counter")
    assert sio_b.is_connected("/socket_app_counter")

    sio_a.emit("join_messaging_room", {"counter_id": 1},
               namespace="/socket_app_counter")
    sio_b.emit("join_messaging_room", {"counter_id": 2},
               namespace="/socket_app_counter")

    with app.app_context():
        communikation("app_counter", event="messaging_changed",
                      data={"message_id": 7},
                      room="messaging_counter_1")
        communikation("app_counter", event="messaging_changed",
                      data={"message_id": 8},
                      room="messaging_counter_2")
        communikation("app_counter", event="messaging_presence_changed")

    recus_a = [m for m in sio_a.get_received("/socket_app_counter")
               if m["name"] == "messaging_changed"]
    assert [m["args"][0]["data"] for m in recus_a] == [{"message_id": 7}]
    recus_b = [m for m in sio_b.get_received("/socket_app_counter")
               if m["name"] == "messaging_changed"]
    assert [m["args"][0]["data"] for m in recus_b] == [{"message_id": 8}]
    # Sans salle rejointe : aucun signal ciblé, mais la diffusion passe.
    # NB : get_received vide la file — on ne le lit qu'une fois par client.
    recus_muet = sio_muet.get_received("/socket_app_counter")
    assert all(m["name"] != "messaging_changed" for m in recus_muet)
    assert any(m["name"] == "messaging_presence_changed" for m in recus_muet)


def test_join_messaging_room_rejette_les_payloads_invalides(messaging_app):
    """Un counter_id absent ou non convertible ne fait rejoindre aucune
    salle : le client ne reçoit aucun évènement ciblé."""
    app, _token, _ids = messaging_app
    from communication import communikation

    socketio = _init_counter_sockets(app)
    client = app.test_client()
    sio = _counter_socket(socketio, app, client)

    for payload in (
        {"counter_id": "abc"},
        {"counter_id": None},
        {},
        None,
        "pas-un-dict",
    ):
        sio.emit("join_messaging_room", payload,
                 namespace="/socket_app_counter")

    with app.app_context():
        communikation("app_counter", event="messaging_changed",
                      data={"message_id": 7},
                      room="messaging_counter_1")

    assert all(m["name"] != "messaging_changed"
               for m in sio.get_received("/socket_app_counter"))
