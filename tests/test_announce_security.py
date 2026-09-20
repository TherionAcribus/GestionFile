"""Garde du perimetre « ecran » (blueprint announce).

Avant, ``SECURITY_LOGIN_SCREEN`` ne protegeait que ``/display`` dans le
``before_request`` global : les routes ``/announce/*`` que la page consomme
(``state``, ``patients_ongoing``, ``patients_next``, ``init_gallery``) restaient
publiques, et ``/announce/refresh`` — une action forcant le rechargement de
tous les ecrans — etait un GET accessible a n'importe qui.

Verrouille ici :

1. drapeau inactif -> les routes restent publiques (comportement historique) ;
2. drapeau actif -> refus pour l'anonyme : redirection en navigation
   navigateur, **401 JSON** pour un appel programmatique (HTMX/fetch) ;
3. drapeau actif -> jeton applicatif valide (X-App-Token) accepte, comme sur le
   namespace ``/socket_update_screen`` ;
4. ``/announce/refresh`` : POST-only, permission ``announce`` exigee quelle que
   soit la valeur du drapeau (401 anonyme / 403 sans permission / 204 autorise).
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager, login_user

from models import db, ConfigOption, Role, User
from auth_utils import generate_app_token


def _make_app(*, screen_security):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-secret-announce",
        SECURITY_LOGIN_SCREEN=screen_security,
    )
    db.init_app(app)

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        # Flask-Security : get_id() renvoie fs_uniquifier (UUID), pas l'id.
        return User.query.filter_by(fs_uniquifier=user_id).first()

    # Stub minimal : la garde redirige les navigations vers
    # url_for('admin_security.login') ; sans cet endpoint, url_for echouerait.
    security_stub = Blueprint("admin_security", __name__)

    @security_stub.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(security_stub)

    # Connexion de test : pose la session via le vrai login_user plutot que de
    # forger les cles de session a la main.
    @app.route("/_login_test/<int:uid>")
    def _login_test(uid):
        login_user(User.query.get(uid))
        return "ok", 200

    from routes.announce import announce_bp
    app.register_blueprint(announce_bp)

    with app.app_context():
        db.create_all()
        db.session.add(ConfigOption(config_key="announce_call_text",
                                    value_str="Patient {N} au comptoir {C}"))
        db.session.commit()
    return app


@pytest.fixture
def app_public():
    return _make_app(screen_security=False)


@pytest.fixture
def app_protected():
    return _make_app(screen_security=True)


def _make_user(app, *, admin_announce):
    with app.app_context():
        role = Role(name=f"role_{admin_announce}", admin_announce=admin_announce)
        user = User(username=f"user_{admin_announce}",
                    password="x", active=True)
        user.roles.append(role)
        db.session.add_all([role, user])
        db.session.commit()
        return user.id


def _login(client, user_id):
    client.get(f"/_login_test/{user_id}")


# --- 1-2. Garde SECURITY_LOGIN_SCREEN sur les routes /announce/* --------------

def test_flag_off_routes_publiques(app_public):
    assert app_public.test_client().get("/announce/state").status_code == 200


def test_flag_on_anonyme_navigation_redirige(app_protected):
    resp = app_protected.test_client().get(
        "/display", headers={"Accept": "text/html"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


@pytest.mark.parametrize("path", [
    "/announce/state",
    "/announce/patients_ongoing",
    "/announce/patients_next",
    "/announce/init_gallery",
])
def test_flag_on_anonyme_htmx_recoit_401(app_protected, path):
    resp = app_protected.test_client().get(
        path, headers={"HX-Request": "true"})
    assert resp.status_code == 401


def test_flag_on_jeton_applicatif_accepte(app_protected):
    with app_protected.app_context():
        token = generate_app_token()
    resp = app_protected.test_client().get(
        "/announce/state", headers={"X-App-Token": token})
    assert resp.status_code == 200


def test_flag_on_jeton_invalide_refuse(app_protected):
    resp = app_protected.test_client().get(
        "/announce/state",
        headers={"X-App-Token": "bidon", "Accept": "application/json"})
    assert resp.status_code == 401


def test_flag_on_session_acceptee(app_protected):
    user_id = _make_user(app_protected, admin_announce=False)
    client = app_protected.test_client()
    _login(client, user_id)
    assert client.get("/announce/state").status_code == 200


# --- 3-4. /announce/refresh : POST + permission 'announce' --------------------

def test_refresh_get_refuse(app_public):
    assert app_public.test_client().get("/announce/refresh").status_code == 405


def test_refresh_anonyme_401(app_public):
    resp = app_public.test_client().post(
        "/announce/refresh", headers={"HX-Request": "true"})
    assert resp.status_code == 401


def test_refresh_sans_permission_403(app_public):
    user_id = _make_user(app_public, admin_announce=False)
    client = app_public.test_client()
    _login(client, user_id)
    resp = client.post("/announce/refresh", headers={"HX-Request": "true"})
    assert resp.status_code == 403


def test_refresh_avec_permission_204(app_public):
    user_id = _make_user(app_public, admin_announce=True)
    client = app_public.test_client()
    _login(client, user_id)
    resp = client.post("/announce/refresh", headers={"HX-Request": "true"})
    assert resp.status_code == 204
