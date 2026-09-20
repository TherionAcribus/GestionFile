"""Point B10 — salle ``/socket_phone`` et statut téléphone derrière un jeton signé.

Avant : poser le cookie ``patient_call_number=X`` (non signé, lisible et
modifiable côté client) suffisait à rejoindre la salle ``call_X`` du namespace
``/socket_phone`` — et ``patient_id`` seul donnait le statut d'un patient
arbitraire via ``/patient/phone/status`` ou sa page de confirmation via
``/patient/phone/ping``.

Le serveur pose désormais ``patient_token`` (``URLSafeTimedSerializer`` sur
``SECRET_KEY``) en même temps que les deux cookies lisibles. Le jeton prouve
que CE couple de valeurs a été émis par le serveur ; les endpoints et la
jointure de salle l'exigent.

Verrouillé ici :

1. le jeton : aller-retour, rejet d'un numéro ou d'un id falsifié ;
2. ``/patient/phone/status`` : refus sans jeton valide, réponse avec ;
3. la salle ``call_<n>`` : ``your_turn`` n'arrive qu'au téléphone dont le
   triplet de cookies est signé.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Language, Patient
from auth_utils import make_patient_phone_token, check_patient_phone_token


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-phone",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # Obligatoire ici : ce fichier s'exécute après test_calling_service /
        # test_transactions, qui enregistrent le bind 'users' — db.metadatas
        # est partagé et conserve ce bind, create_all() l'exige donc dans la
        # config de TOUTE app ultérieure.
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
    )
    db.init_app(app)
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


def _patient(application, call_number="A12", status="calling"):
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add_all([langue, activite])
        db.session.commit()
        p = Patient(call_number=call_number, status=status,
                    activity_id=activite.id, language_id=langue.id)
        db.session.add(p)
        db.session.commit()
        return p.id


# --- 1. Le jeton lui-même ----------------------------------------------------

def test_token_aller_retour(application):
    with application.app_context():
        token = make_patient_phone_token(5, "A12")
        assert check_patient_phone_token(token, 5, "A12")


def test_token_rejette_les_falsifications(application):
    with application.app_context():
        token = make_patient_phone_token(5, "A12")
        assert not check_patient_phone_token(token, 5, "B99")   # numéro changé
        assert not check_patient_phone_token(token, 6, "A12")   # id changé
        assert not check_patient_phone_token("bidon", 5, "A12") # jeton forgé
        assert not check_patient_phone_token(None, 5, "A12")
        assert not check_patient_phone_token(token, None, "A12")
        assert not check_patient_phone_token(token, 5, None)


# --- 2. /patient/phone/status -------------------------------------------------

def test_status_refuse_les_cookies_non_signes(client, application):
    pid = _patient(application)
    # Attaquant : pose patient_id / patient_call_number à la main, sans jeton.
    client.set_cookie('patient_id', str(pid))
    client.set_cookie('patient_call_number', 'A12')
    assert client.get('/patient/phone/status').get_json() == {"status": None}


def test_status_repond_avec_token_signe(client, application):
    pid = _patient(application, call_number="A12", status="calling")
    with application.app_context():
        token = make_patient_phone_token(pid, "A12")
    client.set_cookie('patient_id', str(pid))
    client.set_cookie('patient_call_number', 'A12')
    client.set_cookie('patient_token', token)
    assert client.get('/patient/phone/status').get_json() == {
        "status": "calling", "call_number": "A12"}


def test_status_refuse_un_token_pour_un_autre_numero(client, application):
    pid = _patient(application, call_number="A12", status="calling")
    with application.app_context():
        # Jeton valide MAIS signé pour un autre numéro d'appel.
        token = make_patient_phone_token(pid, "B99")
    client.set_cookie('patient_id', str(pid))
    client.set_cookie('patient_call_number', 'A12')
    client.set_cookie('patient_token', token)
    assert client.get('/patient/phone/status').get_json() == {"status": None}


# --- 3. La salle Socket.IO call_<n> --------------------------------------------

def _connecte_telephone(application, client, avec_token, pid=5, call_number="A12"):
    """Ouvre une connexion /socket_phone avec les cookies posés à la main."""
    from extensions import socketio
    import sockets  # noqa: F401 — enregistre les handlers sur l'objet partagé

    client.set_cookie('patient_id', str(pid))
    client.set_cookie('patient_call_number', call_number)
    if avec_token:
        with application.app_context():
            token = make_patient_phone_token(pid, call_number)
        client.set_cookie('patient_token', token)
    return socketio.test_client(
        application, flask_test_client=client, namespace='/socket_phone')


def test_your_turn_n_arrive_pas_sans_token(client, application):
    from extensions import socketio
    import sockets  # noqa: F401

    socketio.init_app(application)
    sio = _connecte_telephone(application, client, avec_token=False)
    assert sio.is_connected('/socket_phone')
    with application.app_context():
        socketio.emit('your_turn', {'call_number': 'A12'},
                      namespace='/socket_phone', room='call_A12')
    assert all(m['name'] != 'your_turn' for m in sio.get_received('/socket_phone'))


def test_your_turn_arrive_au_telephone_signe(client, application):
    from extensions import socketio
    import sockets  # noqa: F401

    socketio.init_app(application)
    sio = _connecte_telephone(application, client, avec_token=True)
    assert sio.is_connected('/socket_phone')
    with application.app_context():
        socketio.emit('your_turn', {'call_number': 'A12'},
                      namespace='/socket_phone', room='call_A12')
    assert any(m['name'] == 'your_turn' for m in sio.get_received('/socket_phone'))
