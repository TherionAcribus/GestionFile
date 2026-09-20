"""Session « borne » (kiosque patient) — ticket signé sans compte utilisateur.

La borne cumulait deux connexions : jeton applicatif (APP_SECRET -> JWT) pour
l'imprimante ET couple utilisateur/mot de passe injecté en JavaScript dans le
formulaire /login. Désormais :

1. ``POST /api/kiosk/session_ticket`` (jeton applicatif requis) délivre une URL
   de connexion signée à courte durée de vie ;
2. ``GET /patient/kiosk_login/<ticket>`` vérifie signature + fraîcheur, pose le
   drapeau ``patient_kiosk`` en session (cookie HttpOnly) et redirige vers
   /patient ;
3. la garde /patient (reproduite ici à l'identique de app.py — app.py exige
   MySQL, cf. test_csrf_protection pour le même procédé) accepte cette session
   en plus de la session utilisateur, quand ``SECURITY_LOGIN_PATIENT`` est
   actif ;
4. ``/socket_patient`` accepte la session borne via
   ``_socket_patient_authorized``.

Le drapeau borne n'ouvre QUE la zone patient : ni /admin ni /counter.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Blueprint, Flask, redirect, request, session, url_for
from flask_login import LoginManager, login_user

from models import db, User
from auth_utils import (
    KIOSK_SESSION_KEY,
    generate_app_token,
    is_kiosk_patient_session,
    make_kiosk_login_ticket,
)


def _make_app(*, patient_security=True):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # L'extension db est partagée entre fichiers de test : db.metadatas
        # accumule le bind 'users' dès qu'un module le déclare
        # (test_phone_patient_token, test_calling_service...). Sans cette
        # entrée, create_all() échoue selon l'ordre d'exécution des tests.
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        SECRET_KEY="test-secret-kiosk",
        SECURITY_LOGIN_PATIENT=patient_security,
    )
    db.init_app(app)

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        return User.query.filter_by(fs_uniquifier=user_id).first()

    # Stub : la garde redirige les navigations vers admin_security.login.
    security_stub = Blueprint("admin_security", __name__)

    @security_stub.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(security_stub)

    @app.route("/_login_test/<int:uid>")
    def _login_test(uid):
        login_user(User.query.get(uid))
        return "ok", 200

    from routes.patient import patient_bp
    from routes.api_system import api_system_bp
    app.register_blueprint(patient_bp)
    app.register_blueprint(api_system_bp)

    # La vraie vue /patient rend un gabarit dépendant de toute l'app (css
    # manager, traductions...) : on la remplace par un stub — ce qui est testé
    # ici est la GARDE, pas le rendu.
    app.view_functions["patient.patients_front_page"] = lambda: ("patient", 200)

    # Garde reproduite à l'identique de app.py (require_login_for_admin) pour
    # les préfixes concernés par ce mécanisme.
    @app.before_request
    def _guard_like_app_py():
        from flask_login import current_user
        if request.path.startswith("/admin"):
            if not current_user.is_authenticated:
                return redirect(url_for("admin_security.login", next=request.url))
        elif request.path.startswith("/patient") and not request.path.startswith(
                ("/patient/phone", "/patient/kiosk_login")):
            if app.config["SECURITY_LOGIN_PATIENT"] and not (
                    current_user.is_authenticated or is_kiosk_patient_session()):
                return redirect(
                    url_for("admin_security.login", next=request.url))

    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def app_open():
    return _make_app(patient_security=False)


# --- Émission du ticket : jeton applicatif exigé ------------------------------

def test_ticket_endpoint_requires_app_token(app):
    resp = app.test_client().post("/api/kiosk/session_ticket")
    assert resp.status_code == 401


def test_ticket_endpoint_rejects_bad_token(app):
    resp = app.test_client().post(
        "/api/kiosk/session_ticket", headers={"X-App-Token": "bidon"})
    assert resp.status_code == 401


def test_ticket_endpoint_issues_signed_login_url(app):
    with app.app_context():
        token = generate_app_token()
    resp = app.test_client().post(
        "/api/kiosk/session_ticket", headers={"X-App-Token": token})
    assert resp.status_code == 200
    login_url = resp.get_json()["login_url"]
    assert login_url.startswith("/patient/kiosk_login/")
    assert len(login_url) > len("/patient/kiosk_login/")


def test_ticket_endpoint_get_rejected(app):
    assert app.test_client().get("/api/kiosk/session_ticket").status_code == 405


# --- Connexion par ticket -----------------------------------------------------

def _ticket_login(client, app):
    with app.app_context():
        ticket = make_kiosk_login_ticket()
    return client.get(f"/patient/kiosk_login/{ticket}")


def test_valid_ticket_sets_session_and_redirects(app):
    client = app.test_client()
    resp = _ticket_login(client, app)
    assert resp.status_code == 302
    assert resp.headers["Location"].rstrip("/").endswith("/patient")
    # La session borne est posée : /patient devient accessible.
    assert client.get("/patient").status_code == 200


def test_invalid_ticket_does_not_open_session(app):
    client = app.test_client()
    resp = client.get("/patient/kiosk_login/ticket-bidon")
    assert resp.status_code == 302
    # Pas de session borne : /patient redirige vers le login.
    follow = client.get("/patient")
    assert follow.status_code == 302
    assert "/login" in follow.headers["Location"]


def test_expired_ticket_does_not_open_session(app, monkeypatch):
    import auth_utils
    # TTL forcé à -1 : le ticket est immédiatement expiré.
    monkeypatch.setattr(auth_utils, "KIOSK_TICKET_MAX_AGE", -1)
    client = app.test_client()
    resp = _ticket_login(client, app)
    assert resp.status_code == 302
    assert client.get("/patient").status_code == 302


# --- Garde /patient ------------------------------------------------------------

def test_patient_guard_redirects_anonymous(app):
    resp = app.test_client().get("/patient")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_kiosk_login_route_not_guarded(app):
    """/patient/kiosk_login CRÉE la session : elle ne doit pas être soumise à
    la garde /patient (sinon la borne ne pourrait jamais se connecter)."""
    resp = app.test_client().get("/patient/kiosk_login/bidon")
    assert resp.status_code == 302
    # Redirection vers /patient (rejet doux), pas vers /login.
    assert "/login" not in resp.headers["Location"]


def test_kiosk_session_does_not_open_admin(app):
    """Le drapeau borne n'ouvre QUE la zone patient : /admin exige une vraie
    session utilisateur."""
    client = app.test_client()
    _ticket_login(client, app)
    assert client.get("/patient").status_code == 200
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_patient_accessible_without_flag_when_security_off(app_open):
    """Sécurité patient inactive : /patient reste public (comportement
    historique) et le ticket reste accepté."""
    client = app_open.test_client()
    assert client.get("/patient").status_code == 200
    resp = _ticket_login(client, app_open)
    assert resp.status_code == 302


# --- Socket.IO /socket_patient --------------------------------------------------

def test_socket_patient_accepts_kiosk_session(app):
    from sockets import _socket_patient_authorized
    client = app.test_client()
    _ticket_login(client, app)
    # La session est lue depuis le contexte de requête : on en rejoue une avec
    # le cookie de session obtenu.
    with app.test_request_context("/socket_patient"):
        session[KIOSK_SESSION_KEY] = True
        assert _socket_patient_authorized(True) is True


def test_socket_patient_rejects_anonymous_when_secured(app):
    from sockets import _socket_patient_authorized
    with app.test_request_context("/socket_patient"):
        assert _socket_patient_authorized(True) is False


def test_socket_patient_open_when_flag_off(app):
    from sockets import _socket_patient_authorized
    with app.test_request_context("/socket_patient"):
        assert _socket_patient_authorized(False) is True
