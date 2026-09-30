"""Bootstrap du premier administrateur (déploiement Coolify).

La fonction ``create_default_user`` ne doit plus créer un compte ``admin``
avec un mot de passe fixe connu : elle consomme ``ADMIN_INITIAL_PASSWORD``,
génère un mot de passe aléatoire sinon, et refuse un mot de passe fourni
mais contraire à la politique (échec franc du conteneur ``init``).
"""

import os

import pytest
from flask import Flask

from models import db, Role, User
from routes.admin_security import create_default_user, create_default_roles

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_STRONG_PWD = "MotDePasse-2026-robuste"


def _make_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    # NB : ne PAS définir SQLALCHEMY_BINDS ici — le bind resterait enregistré
    # dans les métadatas de l'extension `db` partagée et ferait échouer les
    # `db.create_all()` des autres fichiers de test exécutés ensuite.
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-admin-bootstrap",
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        db.session.add(Role(name="admin", description="Administrateur",
                            admin_security=True))
        db.session.commit()
    return app


@pytest.fixture
def app_context():
    app = _make_app()
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _clean_admin_env(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)


def test_env_password_is_used(app_context, monkeypatch):
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", _STRONG_PWD)
    monkeypatch.setenv("ADMIN_USERNAME", "root_admin")

    assert create_default_user() is True

    admin = User.query.filter_by(username="root_admin").first()
    assert admin is not None
    assert admin.verify_password(_STRONG_PWD)
    assert any(r.name == "admin" for r in admin.roles)


def test_generated_password_when_env_missing(app_context):
    assert create_default_user() is True

    admin = User.query.filter_by(username="admin").first()
    assert admin is not None
    # Jamais le mot de passe historique fixe, jamais en clair.
    assert not admin.verify_password("admin")
    assert admin.password != "admin"
    assert len(admin.password) > 20  # hash bcrypt


def test_weak_env_password_fails_hard(app_context, monkeypatch):
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "admin")

    with pytest.raises(RuntimeError):
        create_default_user()
    assert User.query.count() == 0


def test_noop_when_users_exist(app_context, monkeypatch):
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", _STRONG_PWD)
    assert create_default_user() is True
    # Un second appel ne réinitialise jamais le compte existant.
    admin = User.query.filter_by(username="admin").first()
    first_hash = admin.password
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "Autre-pass-2026!")
    assert create_default_user() is True
    assert User.query.count() == 1
    assert admin.password == first_hash


# ---------------------------------------------------------------------------
# Rôles de base (seeding idempotent, jamais destructeur)
# ---------------------------------------------------------------------------

_SENSITIVE = {"admin_security", "admin_security_view",
              "admin_security_manage", "admin_security_grant"}


def _role_fields(role):
    return {c.name for c in Role.__table__.columns
            if c.name.startswith("admin_") and getattr(role, c.name)}


def test_base_roles_sont_crees(app_context):
    assert create_default_roles() is True
    roles = {r.name: r for r in Role.query.all()}
    assert {"admin", "admin-fonctionnel",
            "affichage-medias", "exploitation"} <= set(roles)


def test_admin_fonctionnel_a_tout_sauf_securite(app_context):
    create_default_roles()
    role = Role.query.filter_by(name="admin-fonctionnel").one()
    fields = _role_fields(role)
    assert not fields & _SENSITIVE
    # Et il couvre bien tout le reste du registre.
    from permissions_registry import PERMISSION_FIELDS
    assert set(PERMISSION_FIELDS) - _SENSITIVE == fields


def test_affichage_medias_et_exploitation_ont_leur_perimetre(app_context):
    create_default_roles()
    assert _role_fields(Role.query.filter_by(
        name="affichage-medias").one()) == {
        "admin_patient", "admin_announce", "admin_phone",
        "admin_gallery", "admin_music_play", "admin_translation"}
    assert _role_fields(Role.query.filter_by(
        name="exploitation").one()) == {
        "admin_queue", "admin_counter", "admin_staff", "admin_stats"}


def test_base_roles_jamais_reecrits(app_context):
    create_default_roles()
    role = Role.query.filter_by(name="exploitation").one()
    role.description = "Personnalisé"
    role.admin_stats = False
    db.session.commit()

    assert create_default_roles() is True
    role = Role.query.filter_by(name="exploitation").one()
    assert role.description == "Personnalisé"
    assert role.admin_stats is False
    assert Role.query.filter_by(name="exploitation").count() == 1
