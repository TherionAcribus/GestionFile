"""Journal d'audit (point 7 — Phase 8) : câblage Flask/SQLAlchemy.

Trois volets, exécutables sans MySQL :

1. ``persist_audit_record`` écrit bien une ligne (et normalise les « - » en NULL)
   sur une base SQLite jouet reproduisant les colonnes du modèle ``AuditLog``.
2. Résolution de l'utilisateur courant et de l'IP (mini app Flask + flask_login).
3. ``record_audit`` est best-effort : il journalise et ne lève jamais, même
   quand la persistance en base échoue (aucune base initialisée).
"""

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402
from flask import Flask  # noqa: E402
from flask_sqlalchemy import SQLAlchemy  # noqa: E402
from flask_login import LoginManager, UserMixin, login_user  # noqa: E402

import audit_service  # noqa: E402
from audit_service import persist_audit_record, record_audit  # noqa: E402
from audit_log import build_audit_record, ACTION_DELETE, OUTCOME_SUCCESS  # noqa: E402


# --- 1. Persistance sur une base SQLite jouet -------------------------------

@pytest.fixture
def toy_db():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True
    db = SQLAlchemy(app)

    class AuditLog(db.Model):
        __tablename__ = "audit_log"
        id = db.Column(db.Integer, primary_key=True)
        timestamp = db.Column(db.DateTime)
        username = db.Column(db.String(64))
        action = db.Column(db.String(40), nullable=False)
        resource = db.Column(db.String(40), nullable=False)
        target = db.Column(db.String(64))
        outcome = db.Column(db.String(16), nullable=False)
        ip = db.Column(db.String(64))
        details = db.Column(db.String(255))

    with app.app_context():
        db.create_all()
        yield app, db, AuditLog


def test_persist_writes_row(toy_db):
    app, db, AuditLog = toy_db
    rec = build_audit_record(ACTION_DELETE, "user", user="alice", target_id=42,
                             outcome=OUTCOME_SUCCESS, ip="1.2.3.4", details="username=alice")
    entry = persist_audit_record(db.session, AuditLog, rec)
    assert entry.id is not None
    row = db.session.get(AuditLog, entry.id)
    assert row.username == "alice"
    assert row.action == "delete"
    assert row.resource == "user"
    assert row.target == "42"
    assert row.outcome == "success"
    assert row.ip == "1.2.3.4"
    assert row.details == "username=alice"


def test_persist_normalizes_dash_to_null(toy_db):
    app, db, AuditLog = toy_db
    # user/target/ip absents -> "-" côté record -> NULL en base (pas la chaîne "-").
    rec = build_audit_record(ACTION_DELETE, "queue")
    entry = persist_audit_record(db.session, AuditLog, rec)
    row = db.session.get(AuditLog, entry.id)
    assert row.username is None
    assert row.target is None
    assert row.ip is None
    assert row.action == "delete"
    assert row.resource == "queue"


# --- 2. Résolution de l'utilisateur courant et de l'IP ----------------------

def _login_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    lm = LoginManager(app)

    class U(UserMixin):
        def __init__(self, name):
            self.id = name
            self.username = name

    @lm.user_loader
    def _load(uid):
        return U(uid)

    return app, U


def test_current_username_when_logged_in():
    app, U = _login_app()

    @app.route("/who")
    def who():
        login_user(U("bob"))
        return audit_service._current_username() or "-"

    client = app.test_client()
    assert client.get("/who").get_data(as_text=True) == "bob"


def test_current_username_none_without_request_context():
    # Hors de tout contexte de requête, aucune identité n'est résolue.
    assert audit_service._current_username() is None


def test_client_ip_none_without_request_context():
    assert audit_service._client_ip() is None


def test_client_ip_from_request():
    app = Flask(__name__)

    @app.route("/ip")
    def ip():
        return audit_service._client_ip() or "-"

    client = app.test_client()
    # Werkzeug attribue 127.0.0.1 comme remote_addr au client de test.
    assert client.get("/ip").get_data(as_text=True) == "127.0.0.1"


# --- 3. record_audit best-effort : journalise et ne lève jamais -------------

def test_record_audit_never_raises_and_logs(caplog):
    app = Flask(__name__)

    @app.route("/act")
    def act():
        # Aucune base n'est initialisée ici : la persistance échoue en silence,
        # mais record_audit doit malgré tout renvoyer l'enregistrement sans lever.
        with caplog.at_level(logging.INFO):
            rec = record_audit(ACTION_DELETE, "user", target_id=99, outcome=OUTCOME_SUCCESS)
        assert rec["action"] == "delete"
        assert rec["outcome"] == "success"
        return "ok"

    client = app.test_client()
    assert client.get("/act").status_code == 200
    # La trace d'audit a bien été émise (ligne "audit ...").
    assert any(r.getMessage().startswith("audit ") for r in caplog.records)
