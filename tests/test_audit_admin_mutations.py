"""Journal d'audit — couverture des mutations Admin (point « journal incomplet »).

Complément fonctionnel au test statique ``test_audit_wiring`` (qui vérifie par
AST que chaque action sensible appelle ``record_audit``) : ici on exécute
réellement quelques routes représentatives et on vérifie la ligne ``AuditLog``
produite — action, ressource, cible, résultat et auteur.

Cas couverts (module comptoirs, représentatif du pattern appliqué aux 12
modules Admin) :

* succès métier -> ligne ``success`` après le commit ;
* échec métier (exception avant/pendant le commit) -> ``rollback`` puis ligne
  ``failure`` : l'audit ne doit pas persister de mutations partielles ;
* anonyme -> refus avant toute mutation, aucune ligne d'audit.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_counter as admin_counter
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from models import Activity, AuditLog, Counter, Role, User, db


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, template_folder="templates")
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        # Pas de SQLALCHEMY_BINDS : db.metadatas est partagé entre fichiers de
        # test ; déclarer 'users' ici (ce fichier s'exécute tôt dans la suite,
        # avant test_call_numbering) casserait le create_all() des fichiers
        # suivants sans ce bind.
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_counter.admin_counter_bp)

    # Stub Flask-Security (non initialisée dans cette app de test minimale) :
    # require_permission redirige les anonymes vers security.login.
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user

        user = User.query.filter_by(username="admin").first()
        login_user(user)
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(
            username="admin",
            email="a@a.a",
            password=generate_password_hash("x"),
            active=True,
        )
        admin.roles.append(Role(name="admin", admin_counter=True))
        activity = Activity(name="Act", letter="A")
        counter = Counter(name="Comptoir 1", sort_order=0)
        db.session.add_all([admin, activity, counter])
        db.session.commit()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    client.post("/_test/login")
    return client


def _audit_rows(app, resource=None):
    with app.app_context():
        q = AuditLog.query
        if resource:
            q = q.filter_by(resource=resource)
        rows = q.order_by(AuditLog.id).all()
        # Détacher avant la fin du contexte pour lire les attributs après coup.
        db.session.expunge_all()
    return rows


def _first_counter_id(app):
    with app.app_context():
        return Counter.query.first().id


def test_update_counter_writes_success_audit(app, auth_client):
    cid = _first_counter_id(app)

    resp = auth_client.post(
        f"/admin/counter/counter_update/{cid}",
        data={"name": "Accueil"},
    )

    assert resp.status_code == 200
    rows = _audit_rows(app, "counter")
    assert len(rows) == 1
    row = rows[0]
    assert row.action == ACTION_UPDATE
    assert row.outcome == OUTCOME_SUCCESS
    assert row.target == str(cid)
    assert row.username == "admin"
    assert "Accueil" in (row.details or "")


def test_update_counter_failure_writes_failure_audit_and_rolls_back(
        app, auth_client, monkeypatch):
    cid = _first_counter_id(app)

    # Le commit métier échoue une fois ; l'audit (même session, commit dédié)
    # doit néanmoins aboutir — et le rollback doit avoir annulé la mutation.
    calls = []
    real_commit = db.session.commit

    def flaky_commit():
        if not calls:
            calls.append(1)
            raise RuntimeError("commit explosé")
        return real_commit()

    monkeypatch.setattr(db.session, "commit", flaky_commit)

    resp = auth_client.post(
        f"/admin/counter/counter_update/{cid}",
        data={"name": "Jamais persisté"},
    )

    assert resp.status_code == 200  # la route rend un toast d'erreur, pas 500
    # Le détail technique reste dans les journaux : jamais dans la réponse
    # (toast inclus — transporté via l'en-tête HX-Trigger).
    leak = "commit explosé"
    assert leak not in resp.get_data(as_text=True)
    assert leak not in (resp.headers.get("HX-Trigger") or "")
    with app.app_context():
        assert Counter.query.get(cid).name == "Comptoir 1"

    rows = _audit_rows(app, "counter")
    assert len(rows) == 1
    assert rows[0].action == ACTION_UPDATE
    assert rows[0].outcome == OUTCOME_FAILURE
    assert rows[0].target == str(cid)


def test_delete_counter_writes_success_audit(app, auth_client, monkeypatch):
    # display_counter_table rend un gabarit hors périmètre de ce test.
    monkeypatch.setattr(admin_counter, "display_counter_table", lambda: "")
    cid = _first_counter_id(app)

    resp = auth_client.delete(f"/admin/counter/delete/{cid}")

    assert resp.status_code == 200
    with app.app_context():
        assert Counter.query.get(cid) is None
    rows = _audit_rows(app, "counter")
    assert len(rows) == 1
    assert rows[0].action == ACTION_DELETE
    assert rows[0].outcome == OUTCOME_SUCCESS


def test_add_counter_writes_success_audit(app, auth_client, monkeypatch):
    monkeypatch.setattr(admin_counter, "display_counter_table", lambda: "")
    with app.app_context():
        activity_id = Activity.query.first().id

    resp = auth_client.post(
        "/admin/counter/add_new_counter",
        data={"name": "Caisse", "activities": str(activity_id)},
    )

    assert resp.status_code == 200
    rows = _audit_rows(app, "counter")
    assert len(rows) == 1
    assert rows[0].action == ACTION_CREATE
    assert rows[0].outcome == OUTCOME_SUCCESS
    assert "Caisse" in (rows[0].details or "")


def test_anonymous_gets_no_audit_row(app, client):
    resp = client.post("/admin/counter/counter_update/1", data={"name": "x"})

    assert resp.status_code == 302
    assert _audit_rows(app) == []
