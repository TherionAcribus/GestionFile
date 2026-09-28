"""Page /admin/stats/history refaite : filtres, résumé, durées, export CSV.

Couvre le noyau pur ``history_explain`` (filtres, durées, résumé) et les
routes : table filtrée par période / motif / statut, résumé chiffré, export
CSV (séparateur « ; », BOM pour Excel).

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from datetime import date, datetime
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_stats as admin_stats
from history_explain import format_minutes, minutes, parse_filters, summarize
from models import Activity, Counter, PatientHistory, Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]
TODAY = date(2024, 3, 10)


def _get(values):
    return lambda key: values.get(key)


# --- Noyau pur ----------------------------------------------------------------

def test_filtres_par_defaut_et_prereglages():
    f = parse_filters(_get({}), TODAY)
    assert (f.date_from, f.date_to) == (date(2024, 2, 10), TODAY)
    f = parse_filters(_get({"preset": "yesterday"}), TODAY)
    assert f.date_from == f.date_to == date(2024, 3, 9)
    f = parse_filters(_get({"preset": "7"}), TODAY)
    assert f.date_from == date(2024, 3, 4)


def test_filtres_dates_inversees_invalides_et_statuts():
    f = parse_filters(_get({"date_from": "2024-03-05", "date_to": "2024-03-01"}), TODAY)
    assert (f.date_from, f.date_to) == (date(2024, 3, 1), date(2024, 3, 5))
    f = parse_filters(_get({"date_from": "n'importe"}), TODAY)
    assert f.error and f.date_to == TODAY
    f = parse_filters(_get({"status": "done,pirate"}), TODAY, known_statuses=("done", "cancelled"))
    assert f.statuses == ["done"]
    f = parse_filters(_get({}), TODAY, known_statuses=("done", "cancelled"),
                      getlist=lambda k: ["cancelled", "done"])
    assert f.statuses == ["cancelled", "done"]


def test_durees_et_resume():
    t = datetime(2024, 3, 9, 10, 0)
    assert minutes(t, datetime(2024, 3, 9, 10, 12)) == 12
    assert minutes(t, None) is None
    assert minutes(t, datetime(2024, 3, 9, 9, 0)) is None          # négatif
    assert minutes(t, datetime(2024, 3, 10, 10, 0)) is None        # aberrant (> 12 h)
    assert format_minutes(None) == "—" and format_minutes(0.4) == "< 1 min"
    assert format_minutes(12) == "12 min" and format_minutes(65) == "1 h 05"
    s = summarize([
        (t, datetime(2024, 3, 9, 10, 10), datetime(2024, 3, 9, 10, 15), "done", 0),
        (t, datetime(2024, 3, 9, 10, 20), datetime(2024, 3, 9, 10, 25), "done", 2),
        (t, None, None, "cancelled", 0),
    ])
    assert s["count"] == 3 and s["served"] == 2 and s["served_pct"] == 67
    assert s["avg_wait"] == 15 and s["avg_counter"] == 5
    assert s["max_wait"] == 20 and s["max_overtaken"] == 2


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

    app.register_blueprint(admin_stats.admin_stats_bp)
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
        admin.roles.append(Role(name="admin", admin_stats=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance", letter="A")
        conseil = Activity(name="Conseil", letter="C")
        c1 = Counter(name="Comptoir 1", sort_order=0)
        db.session.add_all([ordo, conseil, c1])
        db.session.flush()

        def h(num, act, day, wait, status="done", overtaken=0):
            ts = datetime(2024, 3, day, 10, 0)
            return PatientHistory(call_number=num, activity_id=act.id, counter_id=c1.id,
                                  timestamp=ts,
                                  timestamp_counter=ts.replace(minute=wait) if wait is not None else None,
                                  timestamp_end=ts.replace(minute=(wait or 0) + 5) if wait is not None else None,
                                  day_of_week="Sat", status=status, overtaken=overtaken)
        db.session.add_all([h("A-1", ordo, 9, 10), h("A-2", ordo, 9, 20, overtaken=3),
                            h("C-1", conseil, 9, None, status="cancelled"),
                            h("A-9", ordo, 1, 30)])
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def test_table_filtree_et_resume(app, client):
    html = client.get("/admin/stats/history/table",
                      query_string={"date_from": "2024-03-09", "date_to": "2024-03-09"}).get_data(as_text=True)
    assert "A-1" in html and "C-1" in html and "A-9" not in html
    assert "67 % servis" in html
    assert "15 min" in html        # attente moyenne (10 et 20)
    assert "Retiré" in html        # libellé du statut cancelled
    assert "export.csv?date_from=2024-03-09" in html
    assert 'hx-include="#history-filters"' in html


def test_table_filtre_motif_et_statut(app, client):
    with app.app_context():
        conseil = Activity.query.filter_by(name="Conseil").one().id
    html = client.get("/admin/stats/history/table",
                      query_string={"date_from": "2024-03-01", "date_to": "2024-03-09",
                                    "activity_id": conseil}).get_data(as_text=True)
    assert "C-1" in html and "A-1" not in html
    html = client.get("/admin/stats/history/table",
                      query_string=[("date_from", "2024-03-01"), ("date_to", "2024-03-09"),
                                    ("status", "done")]).get_data(as_text=True)
    assert "A-9" in html and "C-1" not in html


def test_export_csv(client):
    response = client.get("/admin/stats/history/export.csv",
                          query_string={"date_from": "2024-03-09", "date_to": "2024-03-09"})
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    body = response.get_data(as_text=True)
    assert body.startswith("﻿Date;Arrivée")
    assert "09/03/2024;10:00:00;10:20:00;10:25:00;A-2;Ordonnance;Comptoir 1;;Servi;20,0;5,0;3" in body
    assert "A-9" not in body
