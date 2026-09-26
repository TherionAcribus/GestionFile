"""Page /admin/counter refaite : comptoirs en cartes avec l'état en direct.

Couvre : liste (personne connectée, compétences, patient en cours, appel
automatique), renommage validé (204 sans remplacement) sans effacer les
anciennes « activités », déconnexion par l'administrateur, suppression
refusée si un patient est en cours, réordonnancement (plus de 500).

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

import routes.admin_counter as admin_counter
from models import Activity, Counter, Language, Patient, Pharmacist, Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        COUNTER_ORDER="order",
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_counter.admin_counter_bp)
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
        admin.roles.append(Role(name="admin", admin_counter=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance", letter="A")
        fr = Language(code="fr", name="Français", translation="Français")
        marie = Pharmacist(name="Marie", initials="MA", activities=[ordo])
        jean = Pharmacist(name="Jean", initials="JD")
        db.session.add_all([ordo, fr, marie, jean])
        db.session.flush()
        c1 = Counter(name="Comptoir 1", staff=marie, sort_order=0, auto_calling=True,
                     activities=[ordo])
        c2 = Counter(name="Comptoir 2", staff=jean, sort_order=1)
        c3 = Counter(name="Comptoir 3", sort_order=2)
        db.session.add_all([c1, c2, c3])
        db.session.flush()
        db.session.add(Patient(call_number="A-12", status="ongoing", counter_id=c1.id,
                               activity_id=ordo.id, language_id=fr.id,
                               timestamp=datetime(2024, 1, 1, 10)))
        db.session.commit()
    with patch.object(admin_counter, "communikation", lambda *a, **k: None), \
            patch("ui_feedback.communikation", lambda *a, **k: None):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _counter(app, name):
    with app.app_context():
        c = Counter.query.filter_by(name=name).one()
        return c.id, c.staff_id, c.auto_calling, [a.name for a in c.activities], c.sort_order


def test_liste_etat_en_direct(client):
    html = client.get("/admin/counter/table").get_data(as_text=True)
    assert "3 comptoirs" in html and "2 occupés" in html and "1 en appel automatique" in html
    assert "Marie (MA)" in html and "Avec A-12" in html
    assert "Peut appeler : Ordonnance" in html
    assert "Jean n&#39;a aucune compétence" in html or "Jean n'a aucune compétence" in html
    assert "Libre : en attente de connexion" in html
    # Anciens « actes réalisés » : plus proposés.
    assert 'name="activities"' not in html
    # Ordre de sort_order.
    assert html.index("Comptoir 1") < html.index("Comptoir 2") < html.index("Comptoir 3")


def test_renommer_sans_effacer_les_anciennes_activites(app, client):
    cid = _counter(app, "Comptoir 1")[0]
    response = client.post(f"/admin/counter/counter_update/{cid}", data={"name": "Caisse 1"})
    assert response.status_code == 200 and "Caisse 1" in response.get_data(as_text=True)
    assert _counter(app, "Caisse 1")[3] == ["Ordonnance"]


@pytest.mark.parametrize("name", ["", "x" * 21])
def test_renommer_invalide(app, client, name):
    cid = _counter(app, "Comptoir 1")[0]
    response = client.post(f"/admin/counter/counter_update/{cid}", data={"name": name})
    assert response.status_code == 204
    assert _counter(app, "Comptoir 1")[0] == cid


def test_deconnexion_par_l_admin(app, client):
    cid = _counter(app, "Comptoir 1")[0]
    response = client.post(f"/admin/counter/disconnect/{cid}")
    assert response.status_code == 200
    _, staff_id, auto, _, _ = _counter(app, "Comptoir 1")
    assert staff_id is None and auto is False
    assert "Personne" in response.get_data(as_text=True)


def test_suppression_refusee_si_patient_en_cours(app, client):
    cid = _counter(app, "Comptoir 1")[0]
    html = client.get(f"/admin/counter/confirm_delete/{cid}").get_data(as_text=True)
    assert "A-12" in html
    client.delete(f"/admin/counter/delete/{cid}")
    assert _counter(app, "Comptoir 1")[0] == cid


def test_suppression_comptoir_occupe_sans_patient(app, client):
    cid = _counter(app, "Comptoir 2")[0]
    assert "La session de Jean" in client.get(f"/admin/counter/confirm_delete/{cid}").get_data(as_text=True)
    assert client.delete(f"/admin/counter/delete/{cid}").status_code == 200
    with app.app_context():
        assert Counter.query.filter_by(name="Comptoir 2").first() is None


def test_creation(app, client):
    response = client.post("/admin/counter/add_new_counter", data={"name": "Comptoir 4"})
    assert response.status_code == 200
    assert "div_add_counter_form" in response.get_data(as_text=True)
    assert _counter(app, "Comptoir 4")[4] == 3
    assert client.post("/admin/counter/add_new_counter", data={"name": ""}).status_code == 204


def test_reordonnancement(app, client):
    ids = [_counter(app, n)[0] for n in ("Comptoir 3", "Comptoir 1", "Comptoir 2")]
    response = client.post("/admin/counter/update_counter_order",
                           data={"order[]": [str(i) for i in ids]})
    assert response.status_code == 200
    assert "refresh_counter_table" in response.headers["HX-Trigger"]
    assert _counter(app, "Comptoir 3")[4] == 0 and _counter(app, "Comptoir 2")[4] == 2
