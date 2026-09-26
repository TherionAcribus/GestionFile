"""Page /admin/queue refaite : lecture d'abord, correction encadrée.

Couvre :
- le noyau pur ``queue_explain`` (durées, règle « appelé / au comptoir ⇒
  comptoir obligatoire ») ;
- la liste (résumé par statut, attente la plus longue, badges, filtres) ;
- la correction manuelle validée (204 sans remplacement), qui libère le
  comptoir d'un patient remis en attente et rafraîchit la liste ;
- l'ajout manuel (motif invalide refusé), la suppression d'un patient
  introuvable (plus de 500) et la confirmation de purge.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_queue as admin_queue
from models import Activity, Counter, Language, Patient, Role, User, db
from queue_explain import describe_minutes, minutes_between, validate_edit

SERVEUR_DIR = Path(__file__).resolve().parents[1]
_NOW = datetime(2024, 1, 1, 11, 0)


# --- Noyau pur ----------------------------------------------------------------

def test_durees():
    assert minutes_between(datetime(2024, 1, 1, 10, 48), _NOW) == 12
    assert minutes_between(None, _NOW) is None
    assert describe_minutes(0) == "à l'instant"
    assert describe_minutes(12) == "12 min"
    assert describe_minutes(65) == "1 h 05"


def test_validation_de_la_correction():
    assert validate_edit("standing", None) is None
    assert validate_edit("done", None) is None
    assert validate_edit("calling", None)
    assert validate_edit("ongoing", None)
    assert validate_edit("ongoing", 3) is None
    assert validate_edit("pending", 3)  # géré par le flux d'impression
    assert validate_edit("n'importe", None)


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_queue.admin_queue_bp)
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
        admin.roles.append(Role(name="admin", admin_queue=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance", letter="A")
        fr = Language(code="fr", name="Français", translation="Français")
        c1 = Counter(name="Comptoir 1", sort_order=0)
        db.session.add_all([ordo, fr, c1])
        db.session.flush()

        def p(num, status, minute, counter=None):
            return Patient(call_number=num, status=status, activity_id=ordo.id,
                           language_id=fr.id, counter_id=counter,
                           timestamp=datetime(2024, 1, 1, 10, minute))
        db.session.add_all([p("A-1", "done", 0), p("A-2", "ongoing", 5, c1.id),
                            p("A-3", "standing", 20), p("A-4", "standing", 48)])
        db.session.commit()
    noop = lambda *a, **k: None  # noqa: E731
    with patch.object(admin_queue, "_now", return_value=_NOW), \
            patch.object(admin_queue, "communikation", noop), \
            patch.object(admin_queue, "refresh_announce_screens", noop), \
            patch.object(admin_queue, "clear_counter_table", noop), \
            patch("ui_feedback.communikation", noop):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _patient(app, num):
    with app.app_context():
        p = Patient.query.filter_by(call_number=num).one()
        return p.id, p.status, p.counter_id


def _ids(app):
    with app.app_context():
        return Activity.query.first().id, Counter.query.first().id


def test_liste_resume_et_badges(client):
    html = client.post("/admin/queue/table",
                       data={"standing": "true", "ongoing": "true"}).get_data(as_text=True)
    assert "attente la plus longue : <strong>40 min</strong>" in html
    assert "attend depuis 40 min" in html and "attend depuis 12 min" in html
    assert "Au comptoir" in html and "A-1" not in html  # « servi » filtré
    assert 'name="status"' in html and 'value="pending"' not in html


def test_correction_remise_en_attente_libere_le_comptoir(app, client):
    pid, _, _ = _patient(app, "A-2")
    act, counter = _ids(app)
    response = client.post(f"/admin/queue/patient_update/{pid}",
                           data={"call_number": "A-2", "activity_id": str(act),
                                 "status": "standing", "counter_id": str(counter)})
    assert response.status_code == 200
    assert "refresh_queue_patient" in response.headers["HX-Trigger"]
    assert _patient(app, "A-2")[1:] == ("standing", None)


@pytest.mark.parametrize("over", [
    {"status": "calling", "counter_id": ""},     # appelé sans comptoir
    {"status": "pending"},                       # statut non modifiable
    {"call_number": ""},
    {"call_number": "X" * 11},
    {"activity_id": "999"},
])
def test_correction_invalide(app, client, over):
    pid, status, counter = _patient(app, "A-3")
    act, _ = _ids(app)
    data = {"call_number": "A-3", "activity_id": str(act), "status": "standing", "counter_id": ""}
    data.update(over)
    assert client.post(f"/admin/queue/patient_update/{pid}", data=data).status_code == 204
    assert _patient(app, "A-3")[1:] == (status, counter)


def test_ajout_manuel(app, client):
    with patch.object(admin_queue, "get_next_call_number", lambda a: "A-5"), \
            patch.object(admin_queue, "add_patient",
                         lambda num, act: Patient(id=99, call_number=num)):
        act, _ = _ids(app)
        response = client.post("/admin/queue/create_new_patient_auto", data={"activity_id": str(act)})
    assert response.status_code == 204
    assert "refresh_queue_patient" in response.headers["HX-Trigger"]
    assert client.post("/admin/queue/create_new_patient_auto",
                       data={"activity_id": "999"}).status_code == 204


def test_suppression_patient_introuvable_sans_500(client):
    assert client.delete("/admin/queue/delete_patient/9999").status_code == 204


def test_suppression_patient(app, client):
    pid, _, _ = _patient(app, "A-4")
    response = client.delete(f"/admin/queue/delete_patient/{pid}")
    assert response.status_code == 200
    with app.app_context():
        assert Patient.query.filter_by(call_number="A-4").first() is None


def test_confirmation_purge_compte_les_patients(client):
    html = client.get("/admin/database/confirm_delete_patient_table_without_saving").get_data(as_text=True)
    assert "4 patients" in html and 'hx-swap="none"' in html
