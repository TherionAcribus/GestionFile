"""Tableau de bord /admin refait : catalogue, permissions, cartes.

Couvre :
- le catalogue pur ``dashboard_catalog`` (libellés, filtrage par permission,
  cartes manquantes) ;
- la création automatique des cartes absentes (« Aujourd'hui » en tête) et
  l'enregistrement de la configuration qui renvoie les enveloppes différées
  des seules cartes autorisées ;
- le rendu des cartes Aujourd'hui, File d'attente, Comptoirs, Équipe ;
- les deux routes d'action qui n'exigeaient aucune authentification.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

import inspect
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_counter as admin_counter
import routes.admin_dashboard as admin_dashboard
import routes.admin_queue as admin_queue
import routes.admin_staff as admin_staff
from dashboard_catalog import CATALOG, card_info, missing_cards, visible_cards
from models import (Activity, Counter, DashboardCard, Language, Patient, Pharmacist,
                    Role, User, db)

SERVEUR_DIR = Path(__file__).resolve().parents[1]


# --- Catalogue -------------------------------------------------------------------

def test_catalogue():
    assert card_info("appschedule").label == "Tâches planifiées"
    assert card_info("inconnue").label == "inconnue"
    cards = [SimpleNamespace(name="queue", visible=True),
             SimpleNamespace(name="security", visible=True),
             SimpleNamespace(name="staff", visible=False)]
    allowed = visible_cards(cards, lambda perm: perm == "queue")
    assert [c.name for c in allowed] == ["queue"]
    assert missing_cards(["queue", "staff"])[0] == "today"
    assert "queue" not in missing_cards(list(CATALOG))


def test_routes_d_action_desormais_protegees():
    import routes.counter as counter
    import routes.patient as patient
    for func in (counter.dashboard_remove_counter_staff, patient.patient_refresh):
        source = inspect.getsource(func)
        assert "@require_permission(" in source, func.__name__


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    for bp in (admin_dashboard.admin_dashboard_bp, admin_queue.admin_queue_bp,
               admin_counter.admin_counter_bp, admin_staff.admin_staff_bp):
        app.register_blueprint(bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    now = datetime.now(admin_queue.time_tz).replace(tzinfo=None)
    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_queue=True, admin_counter=True,
                                admin_staff=True, admin_options=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance", letter="A")
        fr = Language(code="fr", name="Français", translation="Français")
        marie = Pharmacist(name="Marie", initials="MA")
        jean = Pharmacist(name="Jean", initials="JD")
        db.session.add_all([ordo, fr, marie, jean])
        db.session.flush()
        c1 = Counter(name="Comptoir 1", sort_order=0, staff=marie, auto_calling=True)
        c2 = Counter(name="Comptoir 2", sort_order=1)
        db.session.add_all([c1, c2])
        db.session.flush()
        db.session.add_all([
            Patient(call_number="A-1", status="done", activity_id=ordo.id, language_id=fr.id,
                    counter_id=c1.id, timestamp=now - timedelta(minutes=50),
                    timestamp_counter=now - timedelta(minutes=40), timestamp_end=now - timedelta(minutes=35)),
            Patient(call_number="A-2", status="ongoing", activity_id=ordo.id, language_id=fr.id,
                    counter_id=c1.id, timestamp=now - timedelta(minutes=30),
                    timestamp_counter=now - timedelta(minutes=10)),
            Patient(call_number="A-3", status="standing", activity_id=ordo.id, language_id=fr.id,
                    timestamp=now - timedelta(minutes=20)),
            Patient(call_number="A-4", status="standing", activity_id=ordo.id, language_id=fr.id,
                    timestamp=now - timedelta(minutes=2)),
        ])
        for position, name in enumerate(("security", "queue", "counter", "staff")):
            db.session.add(DashboardCard(name=name, visible=True, position=position, size="36"))
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def test_cartes_manquantes_creees_aujourd_hui_en_tete(app):
    with app.test_request_context():
        admin_dashboard._ensure_catalog_cards()
        cards = DashboardCard.query.order_by(DashboardCard.position).all()
        assert cards[0].name == "today" and cards[0].visible
        created = {c.name: c.visible for c in cards}
        assert set(CATALOG) <= set(created)
        assert created["player"] is False          # ajoutée masquée
        admin_dashboard._ensure_catalog_cards()     # idempotent
        assert DashboardCard.query.count() == len(set(created))


def test_enregistrement_renvoie_les_enveloppes_autorisees(client):
    with patch.object(admin_dashboard, "_can", lambda perm: perm in ("queue", "options")):
        response = client.post("/admin/dashboard/save_configuration",
                               json={"visible_cards": ["queue", "security"], "card_order": []})
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-card-url="/admin/queue/dashboard"' in body
    assert "/admin/security/dashboard" not in body     # pas la permission
    assert "revealed once" in body


def test_carte_aujourd_hui(client):
    html = client.get("/admin/today/dashboard").get_data(as_text=True)
    assert "en attente" in html and ">2<" in html          # A-3, A-4
    assert "20 min" in html and "(A-3)" in html            # attente la plus longue
    assert "servis aujourd" in html and "1/2" in html      # 1 comptoir occupé sur 2


def test_carte_file(client):
    html = client.get("/admin/queue/dashboard").get_data(as_text=True)
    assert "Au comptoir" in html and "A-2" in html and "Comptoir 1" in html
    assert "En attente (2)" in html
    assert html.index("A-3") < html.index("A-4")           # ordre d'arrivée
    assert "A-1" not in html                              # servi


def test_carte_comptoirs(client):
    html = client.get("/admin/counter/dashboard").get_data(as_text=True)
    assert "Marie" in html and "appel auto" in html and "avec A-2" in html
    assert "/admin/counter/disconnect/" in html and "hx-confirm" in html
    assert "/dash/counter/remove_staff" not in html


def test_carte_equipe(client):
    html = client.get("/admin/staff/dashboard").get_data(as_text=True)
    assert "1/2" in html and "Comptoir 1" in html and "hors comptoir" in html
