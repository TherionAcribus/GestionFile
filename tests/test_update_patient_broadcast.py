"""Régression « borne → App Comptoir » : la diffusion de la file ne dépend pas
de l'authentification de la requête déclenchante.

Incident constaté en production (sonde Socket.IO sur les deux namespaces) :
un patient inscrit depuis la borne déclenchait bien ``update`` sur
``/socket_update_patient`` (Admin rafraîchi) mais JAMAIS ``update_patient_list``
sur ``/socket_app_counter`` (App Comptoir muette) — alors que les deux
émissions sont dans la même fonction ``communikation("update_patient")``.

Cause : ``create_patients_list_for_pyside`` était à la fois la fonction métier
appelée par ``communikation`` ET une route Flask décorée
``@require_app_token_or_login``. Depuis une requête borne (anonyme) ou sans
requête (scheduler), le décorateur renvoyait ``(Response 401, 401)`` à la place
de la liste ; ``socketio.emit`` échouait sur ce payload non sérialisable,
l'exception était avalée, et seule l'émission publique (sans données) partait.

Ces tests appellent ``communikation`` depuis les contextes réellement
rencontrés et vérifient ce qui part VRAIMENT : l'évènement App avec une liste
JSON-sérialisable portant le patient.
"""

import json
import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

import communication
from models import db, Activity, Language, Patient


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-broadcast",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add_all([langue, activite])
        db.session.commit()
        db.session.add(Patient(call_number="O-16", status="standing",
                               activity_id=activite.id, language_id=langue.id))
        db.session.commit()
        yield app


@pytest.fixture
def emissions(monkeypatch):
    """Capture les appels à socketio.emit en imposant la contrainte réelle du
    transport : le payload doit être sérialisable en JSON (sinon l'émission
    réelle lève et est avalée par communication_websocket)."""
    captures = []

    def faux_emit(event, payload, namespace=None, room=None, **kwargs):
        json.dumps(payload)  # TypeError sur une Response Flask, comme en prod
        captures.append((namespace, event, payload))

    monkeypatch.setattr(communication.socketio, "emit", faux_emit)
    return captures


def _evenement_app(emissions):
    return [p for ns, ev, p in emissions
            if (ns, ev) == ("/socket_app_counter", "update_patient_list")]


def test_inscription_borne_anonyme_diffuse_la_file_a_l_app(application, emissions):
    """Requête SANS jeton ni session (borne) : l'App doit recevoir la liste."""
    with application.test_request_context("/patient/confirm_print", method="POST"):
        communication.communikation("update_patient")

    app_events = _evenement_app(emissions)
    assert len(app_events) == 1, (
        "update_patient_list n'a pas été émis vers /socket_app_counter depuis "
        "une requête anonyme (régression borne → App Comptoir)")
    payload = app_events[0]
    assert isinstance(payload["data"], list)
    assert [p["call_number"] for p in payload["data"]] == ["O-16"]
    assert isinstance(payload["revision"], int)


def test_sans_requete_scheduler_diffuse_la_file_a_l_app(application, emissions):
    """Contexte applicatif seul (jobs du scheduler) : même exigence."""
    with application.app_context():
        communication.communikation("update_patient")

    app_events = _evenement_app(emissions)
    assert len(app_events) == 1
    assert [p["call_number"] for p in app_events[0]["data"]] == ["O-16"]


def test_les_deux_namespaces_partagent_la_meme_revision(application, emissions):
    with application.test_request_context("/"):
        communication.communikation("update_patient")

    revisions = {ns: p["revision"] for ns, ev, p in emissions}
    assert set(revisions) == {"/socket_app_counter", "/socket_update_patient"}
    assert len(set(revisions.values())) == 1


def test_snapshot_invalide_n_est_jamais_diffuse_a_l_app(application, emissions, monkeypatch):
    """Garde-fou : si la fonction de snapshot renvoyait autre chose qu'une
    liste, on ne doit ni planter ni envoyer data=None (qui viderait la file
    affichée) ; l'émission publique part et l'App rattrape par trou de
    révision."""
    monkeypatch.setattr(communication, "create_patients_list_for_pyside",
                        lambda: ({"error": "Unauthorized"}, 401))

    with application.app_context():
        communication.communikation("update_patient")

    assert _evenement_app(emissions) == []
    assert any(ns == "/socket_update_patient" for ns, _, _ in emissions)


def test_la_route_http_reste_protegee(application):
    """La séparation métier/route ne doit pas ouvrir l'endpoint HTTP."""
    from routes.pyside import pyside_bp
    application.register_blueprint(pyside_bp)

    reponse = application.test_client().get("/api/patients_list_for_pyside")

    assert reponse.status_code == 401
