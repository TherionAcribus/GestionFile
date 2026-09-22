"""Révocation de sessions — rotation de ``fs_uniquifier``.

Point audit (élevé) : « Déconnecter tout le monde » supprimait des fichiers
``flask_session`` alors que les sessions sont des cookies signés portés par
les navigateurs — rien n'était révoqué. De même, un changement de mot de
passe ne renouvelait pas ``fs_uniquifier`` : un cookie de session ou un
cookie « se souvenir de moi » volé restait valide.

Flask-Login mémorise ``fs_uniquifier`` comme identifiant (``UserMixin.get_id``
de Flask-Security renvoie ``str(fs_uniquifier)``), et le cookie remember
l'encode aussi. Renouveler ``fs_uniquifier`` invalide donc les deux.

Tests FONCTIONNELS (application Flask minimale + SQLite en mémoire, comme
test_kiosk_session.py) : on connecte un utilisateur, on capture son cookie,
on déclenche la révocation, puis on REJOUE l'ancien cookie — il doit être
refusé.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Blueprint, Flask, request
from flask_login import LoginManager, current_user, login_user

from models import db, Role, User

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Mots de passe conformes à password_policy (>= 10 car., non triviaux).
ADMIN_PWD = "Admin-pass-2026!"
BOB_PWD = "Bob-pass-2026!"
NEW_PWD = "Nouveau-pass-2026!"


def _make_app():
    app = Flask(
        __name__,
        # Les vues du blueprint rendent les gabarits réels (table de
        # sécurité HTMX après update_password).
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        SECRET_KEY="test-secret-revocation",
    )
    db.init_app(app)

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        # Réplique de flask_security.core._user_loader : résolution par
        # fs_uniquifier, utilisateur actif exigé.
        user = User.query.filter_by(fs_uniquifier=str(user_id)).first()
        if user and user.active:
            return user
        return None

    from routes.admin_security import admin_security_bp
    app.register_blueprint(admin_security_bp, url_prefix="")

    # Stub de l'endpoint Flask-Security (security.login) : la garde
    # require_permission y redirige les anonymes — il doit donc exister dans
    # la carte d'URL de l'app de test, comme en production.
    security_stub = Blueprint("security", __name__)

    @security_stub.route("/login", endpoint="login")
    def _fs_login():
        return "login", 200

    app.register_blueprint(security_stub)

    @app.route("/_test_login/<int:uid>")
    def _test_login(uid):
        login_user(User.query.get(uid),
                   remember=request.args.get("remember") == "1")
        return "ok", 200

    @app.route("/admin/secret")
    def _secret():
        if not current_user.is_authenticated:
            return "anon", 401
        return "ok", 200

    with app.app_context():
        db.create_all()
        role = Role(name="admin", description="Administrateur",
                    admin_security=True)
        admin = User(username="root", email="root@example.test", active=True)
        admin.set_password(ADMIN_PWD)
        admin.roles.append(role)
        bob = User(username="bob", email="bob@example.test", active=True)
        bob.set_password(BOB_PWD)
        db.session.add_all([role, admin, bob])
        db.session.commit()
    return app


@pytest.fixture
def app():
    return _make_app()


def _client_connecte(app, user_id, *, remember=False):
    """Client de test connecté (cookie de session, + remember si demandé)."""
    client = app.test_client()
    suffix = "?remember=1" if remember else ""
    resp = client.get(f"/_test_login/{user_id}{suffix}")
    assert resp.status_code == 200
    return client


def _rejouer_cookie(app, nom, valeur):
    """Nouveau client ne portant QUE le cookie rejoué."""
    client = app.test_client()
    client.set_cookie(nom, valeur)
    return client


def _uniquifier(app, user_id):
    with app.app_context():
        return User.query.get(user_id).fs_uniquifier


# ---------------------------------------------------------------------------
# logout_all : révocation globale réelle
# ---------------------------------------------------------------------------

def test_logout_all_revoque_la_session_rejouee(app):
    """Un cookie de session capturé AVANT logout_all est refusé APRÈS."""
    admin = _client_connecte(app, 1)
    vieux_cookie = admin.get_cookie("session").value

    assert admin.get("/admin/secret").status_code == 200
    resp = admin.post("/admin/logout_all")
    assert resp.status_code == 302

    # Rejouer l'ancien cookie -> anonyme.
    espion = _rejouer_cookie(app, "session", vieux_cookie)
    assert espion.get("/admin/secret").status_code == 401


def test_logout_all_revoque_le_cookie_remember(app):
    """Le cookie « se souvenir de moi » encode aussi fs_uniquifier : il est
    révoqué comme la session."""
    bob = _client_connecte(app, 2, remember=True)
    remember = bob.get_cookie("remember_token")
    assert remember is not None

    # Un client sans session mais avec remember_token est authentifié.
    clone = _rejouer_cookie(app, "remember_token", remember.value)
    assert clone.get("/admin/secret").status_code == 200

    admin = _client_connecte(app, 1)
    admin.post("/admin/logout_all")

    clone2 = _rejouer_cookie(app, "remember_token", remember.value)
    assert clone2.get("/admin/secret").status_code == 401


def test_logout_all_renouvelle_tous_les_uniquifiers(app):
    avant_1 = _uniquifier(app, 1)
    avant_2 = _uniquifier(app, 2)

    admin = _client_connecte(app, 1)
    admin.post("/admin/logout_all")

    assert _uniquifier(app, 1) != avant_1
    assert _uniquifier(app, 2) != avant_2


def test_logout_all_deconnecte_aussi_l_admin(app):
    """L'admin qui déclenche la déconnexion globale est lui-même déconnecté :
    sa session est révoquée et terminée."""
    admin = _client_connecte(app, 1)
    admin.post("/admin/logout_all")
    assert admin.get("/admin/secret").status_code == 401


def test_logout_all_get_refuse(app):
    """La route reste POST-only."""
    admin = _client_connecte(app, 1)
    assert admin.get("/admin/logout_all").status_code == 405


def test_logout_all_exige_authentification(app):
    """Un anonyme ne peut pas déclencher la déconnexion globale."""
    client = app.test_client()
    resp = client.post("/admin/logout_all")
    # require_permission redirige l'anonyme vers la page de connexion.
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# update_password : révocation des sessions du compte
# ---------------------------------------------------------------------------

def test_update_password_revoque_les_sessions_du_compte(app):
    """Changer le mot de passe de bob invalide sa session existante."""
    bob = _client_connecte(app, 2)
    vieux_cookie = bob.get_cookie("session").value
    avant = _uniquifier(app, 2)

    admin = _client_connecte(app, 1)
    resp = admin.post("/admin/security/update_password/2",
                      data={"password1": NEW_PWD, "password2": NEW_PWD})
    assert resp.status_code == 200

    assert _uniquifier(app, 2) != avant
    espion = _rejouer_cookie(app, "session", vieux_cookie)
    assert espion.get("/admin/secret").status_code == 401


def test_update_password_revoque_le_remember_du_compte(app):
    """Un cookie « se souvenir de moi » du compte devient invalide aussi."""
    bob = _client_connecte(app, 2, remember=True)
    remember = bob.get_cookie("remember_token").value

    admin = _client_connecte(app, 1)
    admin.post("/admin/security/update_password/2",
               data={"password1": NEW_PWD, "password2": NEW_PWD})

    espion = _rejouer_cookie(app, "remember_token", remember)
    assert espion.get("/admin/secret").status_code == 401


def test_update_password_preserve_la_session_admin(app):
    """L'admin qui change le mot de passe d'un AUTRE compte garde sa session."""
    admin = _client_connecte(app, 1)
    admin.post("/admin/security/update_password/2",
               data={"password1": NEW_PWD, "password2": NEW_PWD})
    assert admin.get("/admin/secret").status_code == 200


def test_update_password_self_reemission_session(app):
    """Auto-changement : la session courante est ré-émise (reste valide), mais
    toute AUTRE session du même compte est révoquée."""
    admin = _client_connecte(app, 1)
    vieux_cookie = admin.get_cookie("session").value

    resp = admin.post("/admin/security/update_password/1",
                      data={"password1": NEW_PWD, "password2": NEW_PWD})
    assert resp.status_code == 200

    # La session courante survit (ré-émission avec le nouvel uniquifier)...
    assert admin.get("/admin/secret").status_code == 200
    # ...mais l'ancien cookie (une autre session, ou un cookie volé) est mort.
    espion = _rejouer_cookie(app, "session", vieux_cookie)
    assert espion.get("/admin/secret").status_code == 401


# ---------------------------------------------------------------------------
# Vérifications statiques de garde-fou
# ---------------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def test_logout_all_ne_supprime_plus_de_fichiers():
    """La suppression de fichiers flask_session (inopérante — pas de stockage
    serveur) ne doit pas revenir : la révocation passe par fs_uniquifier."""
    import re
    source = _read("routes/admin_security.py")
    m = re.search(r"def logout_all\(\):(.*?)(?=\n@\w|\ndef |\Z)", source,
                  re.DOTALL)
    body = m.group(1)
    assert "fs_uniquifier" in body
    assert "logout_user" in body
    assert "os.unlink" not in body
    assert "os.listdir" not in body


def test_update_password_renouvelle_uniquifier():
    import re
    source = _read("routes/admin_security.py")
    m = re.search(r"def update_password\(user_id\):(.*?)(?=\n@\w|\ndef |\Z)",
                  source, re.DOTALL)
    assert m, "update_password introuvable"
    assert "fs_uniquifier" in m.group(1)


def test_config_cookies_securisees():
    """SameSite=Lax et HttpOnly explicites ; Secure activable via
    COOKIE_SECURE en HTTPS (jamais par défaut, sinon HTTP local cassé)."""
    source = _read("config.py")
    assert 'SESSION_COOKIE_SAMESITE = "Lax"' in source
    assert "SESSION_COOKIE_HTTPONLY = True" in source
    assert 'REMEMBER_COOKIE_SAMESITE = "Lax"' in source
    assert "REMEMBER_COOKIE_HTTPONLY = True" in source
    assert "SESSION_COOKIE_SECURE" in source
    assert "REMEMBER_COOKIE_SECURE" in source
    assert "COOKIE_SECURE" in source
