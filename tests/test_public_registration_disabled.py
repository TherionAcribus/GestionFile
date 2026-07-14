"""Régression : l'inscription publique est désactivée (point 1.1).

Deux niveaux, exécutables **sans MySQL ni serveur** :

1. Comportement réel : on monte le **vrai** ``flask_security.Security`` avec
   ``register_blueprint=True`` sur un datastore SQLite en mémoire, exactement
   comme app.py. Avec ``SECURITY_REGISTERABLE=False`` la route ``/register``
   n'est pas enregistrée -> un GET renvoie **404**. Un cas témoin (``True``)
   confirme que c'est bien ce drapeau qui gouverne l'enregistrement de la route.

2. Régression statique : config.py fixe bien ``SECURITY_REGISTERABLE = False`` et
   les routes de création d'utilisateur d'admin_security.py sont protégées par la
   permission ``security``. (config.py/app.py exigent MySQL, d'où la vérification
   sur source pour la partie config.)
"""

import os
import re

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_security import (
    Security,
    SQLAlchemyUserDatastore,
    UserMixin,
    RoleMixin,
)


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- Reproduction fidèle du montage Flask-Security de production (cf. app.py) ---


def _make_app(registerable):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret-register"
    app.config["SECURITY_PASSWORD_SALT"] = "test-salt"
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Le drapeau sous test : gouverne l'enregistrement de la route /register.
    app.config["SECURITY_REGISTERABLE"] = registerable

    db = SQLAlchemy(app)

    roles_users = db.Table(
        "roles_users",
        db.Column("user_id", db.Integer, db.ForeignKey("user.id")),
        db.Column("role_id", db.Integer, db.ForeignKey("role.id")),
    )

    class Role(db.Model, RoleMixin):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(80), unique=True)

    class User(db.Model, UserMixin):
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True)
        username = db.Column(db.String(255), unique=True, nullable=True)
        password = db.Column(db.String(255))
        active = db.Column(db.Boolean())
        fs_uniquifier = db.Column(db.String(64), unique=True, nullable=False)
        roles = db.relationship("Role", secondary=roles_users)

    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    # Montage identique à app.py : register_blueprint=True.
    Security(app, user_datastore, register_blueprint=True)

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client_no_register():
    return _make_app(registerable=False).test_client()


# --- 1. Comportement réel ---


def test_register_route_returns_404_when_disabled(client_no_register):
    """Inscription publique désactivée -> la route /register n'existe pas (404)."""
    resp = client_no_register.get("/register")
    assert resp.status_code == 404


def test_register_post_also_404_when_disabled(client_no_register):
    """Aucune méthode ne doit atteindre la vue d'inscription."""
    resp = client_no_register.post("/register", data={"email": "x@y.z"})
    assert resp.status_code == 404


def test_control_register_route_exists_when_enabled():
    """Témoin : c'est bien SECURITY_REGISTERABLE qui gouverne la route.

    Avec True, Flask-Security enregistre /register (GET != 404)."""
    client = _make_app(registerable=True).test_client()
    resp = client.get("/register")
    assert resp.status_code != 404


# --- 2. Régression statique ---


def test_config_disables_public_registration():
    source = _read("config.py")
    assert re.search(r"^\s*SECURITY_REGISTERABLE\s*=\s*False", source, re.MULTILINE), (
        "SECURITY_REGISTERABLE doit être False dans config.py"
    )
    assert not re.search(r"^\s*SECURITY_REGISTERABLE\s*=\s*True", source, re.MULTILINE), (
        "config.py ne doit plus activer SECURITY_REGISTERABLE"
    )


def test_user_creation_routes_require_security_permission():
    """La création d'utilisateurs n'est possible que depuis l'admin protégée.

    add_user_form (formulaire) et add_new_user (création) doivent porter le
    décorateur @require_permission('security')."""
    source = _read("routes/admin_security.py")
    for view in ("def add_user_form(", "def add_new_user("):
        # Fenêtre de source précédant la définition de la vue.
        idx = source.index(view)
        preceding = source[:idx]
        # Le dernier bloc de décorateurs avant la vue doit contenir la garde.
        decorators = preceding[preceding.rindex("@admin_security_bp.route"):]
        assert "@require_permission('security')" in decorators, (
            f"{view} doit être protégée par require_permission('security')"
        )
