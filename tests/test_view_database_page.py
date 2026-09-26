"""Page /admin/database (« Planification ») refaite.

Couvre :
- le classement des tâches (file / maintenance / activités) et le lien vers
  leur réglage (``scheduler_dashboard``) ;
- le fragment d'état de la remise à zéro quotidienne (désactivée, active
  avec ou sans copie historique, active mais non planifiée) ;
- la liste des tâches regroupée, et l'interrupteur qui retire désormais la
  tâche dès l'extinction.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_config as admin_config
import scheduler_dashboard
from models import Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]


# --- Classement pur -----------------------------------------------------------

def test_categories_et_liens():
    assert scheduler_dashboard.job_category("Clear Patient Table") == "patients"
    assert scheduler_dashboard.job_category("enable_3_mon_0900") == "activities"
    assert scheduler_dashboard.job_category("Scheduler Heartbeat") == "maintenance"
    assert scheduler_dashboard.job_settings("Auto Archive Data")[0] == "/admin/data"
    assert scheduler_dashboard.job_settings("Scheduler Heartbeat") is None


# --- Routes ---------------------------------------------------------------------

class _FakeScheduler:
    def __init__(self, jobs=()):
        self.jobs = {j.id: j for j in jobs}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def get_jobs(self):
        return list(self.jobs.values())


def _job(job_id, next_run=None):
    return SimpleNamespace(id=job_id, next_run_time=next_run, trigger=None)


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        CRON_DELETE_PATIENT_TABLE_ACTIVATED=True,
        CRON_TRANSFER_PATIENT_TO_HISTORY=True,
        CRON_DELETE_PATIENT_TABLE_HOUR="23:30",
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_config.admin_config_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_schedule=True))
        db.session.add(admin)
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _status(client, jobs):
    with patch.object(admin_config, "scheduler", _FakeScheduler(jobs)):
        return client.get("/admin/database/patient_purge_status").get_data(as_text=True)


def test_etat_active_avec_copie(client):
    html = _status(client, [_job("Clear Patient Table", datetime(2024, 1, 1, 23, 30))])
    assert "Active" in html and "01/01 à 23:30" in html
    assert "avec copie dans l'historique" in html or "avec copie dans l&#39;historique" in html


def test_etat_active_sans_copie(app, client):
    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = False
    html = _status(client, [_job("Clear Patient Table", datetime(2024, 1, 1, 23, 30))])
    assert "sans copie dans l" in html and "alert-warning" in html


def test_etat_active_mais_non_planifiee(client):
    assert "non planifiée" in _status(client, [])


def test_etat_desactivee(app, client):
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = False
    assert "Désactivée" in _status(client, [])


def test_liste_regroupee(client):
    jobs = [_job("Clear Patient Table", datetime(2024, 1, 1, 23, 30)),
            _job("Scheduler Heartbeat"), _job("enable_3_mon_0900")]
    with patch.object(admin_config, "scheduler", _FakeScheduler(jobs)):
        html = client.get("/admin/database/schedule_tasks_list").get_data(as_text=True)
    assert "File et maintenance" in html
    assert "Ouvertures et fermetures d" in html and "(1)" in html
    assert "Vidage quotidien de la file de patients" in html
    assert "Régler : Réglages ci-dessus" in html
    assert "Jamais exécutée" in html


def test_extinction_retire_la_tache_immediatement():
    calls, events = [], []
    with patch.object(admin_config, "reconcile_clear_patient_table_job",
                      lambda: calls.append(1) or "removed"), \
            patch.object(admin_config, "communikation",
                         lambda *a, **k: events.append(k.get("event"))):
        warning = admin_config.call_function_with_switch(
            "cron_delete_patient_table_activated", "false")
    assert calls == [1] and warning is None
    assert "refresh_schedule_tasks_list" in events
