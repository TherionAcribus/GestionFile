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
from dashboard_catalog import (CATALOG, card_info, card_name_for_url,
                               card_settings, default_layout, missing_cards,
                               reorder_positions, validate_settings,
                               visible_cards)
from dashboard_service import ensure_catalog_cards
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
            db.session.add(DashboardCard(name=name, visible=True, position=position))
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def test_cartes_manquantes_creees_aujourd_hui_en_tete(app):
    with app.test_request_context():
        ensure_catalog_cards()
        cards = DashboardCard.query.order_by(DashboardCard.position).all()
        assert cards[0].name == "today" and cards[0].visible
        created = {c.name: c.visible for c in cards}
        assert set(CATALOG) <= set(created)
        assert created["player"] is False          # ajoutée masquée
        ensure_catalog_cards()                      # idempotent
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


def test_reorder_positions_pur():
    def cards(*pairs):
        return [SimpleNamespace(id=i, position=p) for i, p in pairs]

    result = reorder_positions(cards((1, 1), (2, 2), (3, 3), (4, 4)), [4, 2, 1])
    assert result == {4: 1, 2: 2, 3: 3, 1: 4}

    result = reorder_positions(cards((1, 0), (2, 1), (3, 1), (4, 2)), [])
    assert [result[i] for i in (1, 2, 3, 4)] == [1, 2, 3, 4]

    result = reorder_positions(cards((1, 1), (2, 2)), [2, 99, 2, 1])
    assert result == {2: 1, 1: 2}


def test_page_admin_rend_les_enveloppes_depuis_le_catalogue(app, client):
    """Les enveloppes de chargement différé tirent URL et triggers du
    catalogue (via le gabarit unique dashboard_slot.html)."""
    with app.test_request_context():
        ensure_catalog_cards()   # crée « today » notamment
        html = admin_dashboard._render_card_slots(DashboardCard.query.all())
    assert 'data-card-url="/admin/queue/dashboard"' in html
    assert 'data-card-url="/admin/counter/dashboard"' in html
    assert ('hx-trigger="revealed once, refresh_queue_patient, '
            'refresh_counter_dashboard"') in html   # carte « today »


def _card_id(name):
    return DashboardCard.query.filter_by(name=name).first().id


def test_save_order_conserve_les_cartes_non_citees(app, client):
    with app.app_context():
        DashboardCard.query.filter_by(name="counter").first().visible = False
        db.session.commit()
        ids = {name: _card_id(name)
               for name in ("security", "queue", "counter", "staff")}
    response = client.post("/admin/dashboard/save_order", json={"order": [
        {"id": ids["staff"], "position": 1},
        {"id": ids["queue"], "position": 2},
        {"id": ids["security"], "position": 3}]})
    assert response.status_code == 204
    with app.app_context():
        cards = DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()
        assert sorted(c.position for c in cards) == [1, 2, 3, 4]
        assert [c.name for c in cards] == ["staff", "queue", "counter", "security"]


def test_save_order_payload_invalide(app, client):
    assert client.post("/admin/dashboard/save_order", json={}).status_code == 400
    assert client.post("/admin/dashboard/save_order",
                       json={"order": [{"id": "abc", "position": 1}]}).status_code == 400
    assert client.post("/admin/dashboard/save_order",
                       json={"order": "nope"}).status_code == 400
    assert client.post("/admin/dashboard/save_order",
                       data="x", content_type="text/plain").status_code == 400
    assert client.post("/admin/dashboard/save_configuration",
                       json={"visible_cards": ["queue"],
                             "card_order": [{"id": 1}]}).status_code == 400
    with app.app_context():
        assert DashboardCard.query.filter_by(name="security").first().visible is True


def test_save_configuration_renumerote(client, app):
    with app.app_context():
        ids = {name: _card_id(name)
               for name in ("security", "queue", "counter", "staff")}
    card_order = [{"id": ids[name], "position": i}
                  for i, name in enumerate(("staff", "counter", "queue", "security"), start=1)]
    response = client.post("/admin/dashboard/save_configuration",
                           json={"visible_cards": ["queue", "staff"],
                                 "card_order": card_order})
    assert response.status_code == 200
    with app.app_context():
        cards = DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()
        assert [c.position for c in cards] == [1, 2, 3, 4]
        assert [c.name for c in cards] == ["staff", "counter", "queue", "security"]


def test_route_cards_renvoie_les_enveloppes_autorisees(client):
    with patch.object(admin_dashboard, "_can", lambda perm: perm == "queue"):
        response = client.get("/admin/dashboard/cards")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-card-url="/admin/queue/dashboard"' in body
    assert "/admin/security/dashboard" not in body


def test_mutations_diffusent_refresh_dashboard(app, client):
    with patch.object(admin_dashboard, "communikation") as mock:
        with app.app_context():
            queue_id = _card_id("queue")
        assert client.post("/admin/dashboard/hide",
                           data={"card_name": "queue"}).status_code == 200
        assert any(c.kwargs.get("event") == "refresh_dashboard"
                   for c in mock.call_args_list)
        mock.reset_mock()
        assert client.post("/admin/dashboard/save_order", json={"order": [
            {"id": queue_id, "position": 1}]}).status_code == 204
        assert any(c.kwargs.get("event") == "refresh_dashboard"
                   for c in mock.call_args_list)
        mock.reset_mock()
        assert client.post("/admin/dashboard/save_configuration",
                           json={"visible_cards": ["queue"],
                                 "card_order": []}).status_code == 200
        assert any(c.kwargs.get("event") == "refresh_dashboard"
                   for c in mock.call_args_list)


def test_card_name_for_url():
    assert card_name_for_url("/admin/music/dashboard") == "player"
    assert card_name_for_url("/nope") is None


def test_card_settings_pur():
    assert card_settings("queue", None) == {"max_rows": 10}
    assert card_settings("queue", {"max_rows": 999})["max_rows"] == 10
    assert card_settings("queue", {"max_rows": "7"})["max_rows"] == 7
    assert card_settings("alerts", {"x": 1}) == {}
    assert validate_settings("queue", {"max_rows": 5}) == {"max_rows": 5}
    with pytest.raises(ValueError):
        validate_settings("queue", {"max_rows": 0})
    with pytest.raises(ValueError):
        validate_settings("queue", {"inconnu": 1})
    with pytest.raises(ValueError):
        validate_settings("queue", "pas un dict")


def test_route_settings(app, client):
    with patch.object(admin_dashboard, "communikation") as mock:
        assert client.post("/admin/dashboard/settings/queue",
                           json={"max_rows": 3}).status_code == 204
        assert any(c.kwargs.get("event") == "refresh_dashboard_card"
                   for c in mock.call_args_list)
    with app.app_context():
        assert (DashboardCard.query.filter_by(name="queue").first().settings
                == {"max_rows": 3})
        # 4 patients en attente (2 dans le fixture + 2 ajoutés) : à max_rows=3,
        # la carte affiche « … et 1 autre ».
        ordo = Activity.query.first()
        fr = Language.query.first()
        now = datetime.now(admin_queue.time_tz).replace(tzinfo=None)
        db.session.add_all([
            Patient(call_number="A-5", status="standing", activity_id=ordo.id,
                    language_id=fr.id, timestamp=now),
            Patient(call_number="A-6", status="standing", activity_id=ordo.id,
                    language_id=fr.id, timestamp=now),
        ])
        db.session.commit()
    html = client.get("/admin/queue/dashboard").get_data(as_text=True)
    assert "… et 1 autre" in html

    assert client.post("/admin/dashboard/settings/queue",
                       json={"max_rows": 0}).status_code == 400
    assert client.post("/admin/dashboard/settings/inconnue",
                       json={"x": 1}).status_code == 404


def test_today_seuil_rouge(client, app):
    with app.app_context():
        db.session.add(DashboardCard(name="today", visible=True, position=10))
        db.session.commit()
    html = client.get("/admin/today/dashboard").get_data(as_text=True)
    assert "text-danger" in html          # attente 20 min ≥ seuil 15 par défaut
    assert client.post("/admin/dashboard/settings/today",
                       json={"wait_alert_minutes": 30}).status_code == 204
    html = client.get("/admin/today/dashboard").get_data(as_text=True)
    assert "text-danger" not in html      # 20 min < 30 : plus de rouge


def test_gestionnaire_affiche_les_reglages(client):
    body = client.get("/admin/dashboard/display_select").get_data(as_text=True)
    assert 'data-card-settings="queue"' in body
    assert 'name="max_rows"' in body
    assert 'data-card-settings="staff"' not in body


def test_default_layout():
    layout = default_layout()
    assert layout["today"] == (True, 1)
    assert layout["player"][0] is False
    assert sorted(p for _, p in layout.values()) == list(range(1, len(CATALOG) + 1))


def test_reset(app, client):
    with app.app_context():
        queue = DashboardCard.query.filter_by(name="queue").first()
        queue.visible = False
        queue.settings = {"max_rows": 5}
        queue.position = 42
        db.session.commit()
        ensure_catalog_cards()
    with patch.object(admin_dashboard, "communikation") as mock:
        response = client.post("/admin/dashboard/reset")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Seule « today » est visible par défaut (default_visible) et l'admin du
    # fixture a la permission 'queue' : c'est elle qui revient dans le HTML.
    assert 'data-card-url="/admin/today/dashboard"' in body
    events = [c.kwargs.get("event") for c in mock.call_args_list]
    assert "refresh_dashboard_select" in events
    assert "refresh_dashboard" in events
    with app.app_context():
        layout = default_layout()
        cards = {c.name: c for c in DashboardCard.query.all()}
        assert cards["queue"].visible is False       # default_visible False
        assert cards["queue"].settings is None
        assert cards["security"].visible is False
        assert cards["today"].visible is True
        for name, card in cards.items():
            if name in layout:
                assert card.position == layout[name][1]


def test_slot_polling_depuis_le_catalogue(app):
    with app.test_request_context():
        ensure_catalog_cards()
        connection = DashboardCard.query.filter_by(name="connection").first()
        queue = DashboardCard.query.filter_by(name="queue").first()
        html_connection = admin_dashboard._render_card_slots([connection])
        html_queue = admin_dashboard._render_card_slots([queue])
    assert "every 60s" in html_connection
    assert "document.visibilityState" in html_connection   # quotes échappées (&#39;)
    assert "every" not in html_queue


def test_nom_de_carte_unique(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        db.session.add(DashboardCard(name="queue", visible=True, position=99))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_carte_equipe(client):
    html = client.get("/admin/staff/dashboard").get_data(as_text=True)
    assert "1/2" in html and "Comptoir 1" in html and "hors comptoir" in html
