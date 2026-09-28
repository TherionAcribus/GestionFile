"""Page /admin/stats refaite : tableau de bord + graphique personnalisé.

Couvre le noyau pur ``stats_insights`` (période, indicateurs, affluence par
heure / jour, répartition des attentes, détail par dimension) et la route
``/admin/stats/insights`` (données du jour + historique, filtres, note sur
les jours compressés, JSON des graphiques lisible).

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

import html
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_stats as admin_stats
import stats_insights as si
from models import (Activity, AggregatedStats, Counter, Language, Patient,
                    PatientHistory, Role, User, db)

SERVEUR_DIR = Path(__file__).resolve().parents[1]
TODAY = date(2024, 3, 10)  # un dimanche


def _get(values):
    return lambda key: values.get(key)


def _row(day, hour, wait=None, at_counter=None, status="done", activity=1,
         counter=1, language=1, overtaken=0):
    ts = datetime(2024, 3, day, hour, 0)
    ts_counter = ts + timedelta(minutes=wait) if wait is not None else None
    ts_end = ts_counter + timedelta(minutes=at_counter) if at_counter is not None else None
    return (ts, ts_counter, ts_end, status, activity, counter, language, overtaken)


# --- Période ---------------------------------------------------------------------

def test_periodes():
    p = si.parse_period(_get({}), TODAY)
    assert (p.key, p.date_from, p.days) == ("28", date(2024, 2, 12), 28)
    assert si.parse_period(_get({"period": "today"}), TODAY).is_today_only
    y = si.parse_period(_get({"period": "yesterday"}), TODAY)
    assert y.date_from == y.date_to == date(2024, 3, 9)
    prev = si.parse_period(_get({"period": "7"}), TODAY).previous()
    assert (prev.date_from, prev.date_to) == (date(2024, 2, 26), date(2024, 3, 3))
    c = si.parse_period(_get({"period": "custom", "start_date": "2024-03-05",
                              "end_date": "2024-03-01"}), TODAY)
    assert (c.date_from, c.date_to, c.error) == (date(2024, 3, 1), date(2024, 3, 5), None)
    assert si.parse_period(_get({"period": "custom"}), TODAY).error
    long = si.parse_period(_get({"period": "custom", "start_date": "2020-01-01",
                                 "end_date": "2024-03-01"}), TODAY)
    assert long.days == si.MAX_DAYS and long.error
    assert si.parse_period(_get({"period": "pirate"}), TODAY).key == "28"


# --- Indicateurs -----------------------------------------------------------------

def test_kpis():
    rows = [_row(9, 9, 2, 5), _row(9, 9, 10, 5), _row(9, 10, 20, 5, overtaken=2),
            _row(8, 10, 40, 5), _row(8, 11, status="cancelled")]
    k = si.kpis(rows)
    assert k["count"] == 5 and k["days"] == 2 and k["per_day"] == 2.5
    assert k["served_pct"] == 80 and k["cancelled_pct"] == 20
    assert k["avg_wait"] == 18 and k["median_wait"] in (10, 20)
    assert k["long_wait_pct"] == 50            # 20 et 40 min sur 4 attentes
    assert k["avg_counter"] == 5 and k["overtaken_patients"] == 1
    assert si.kpis([])["count"] == 0 and si.kpis([])["avg_wait"] is None


def test_delta():
    assert si.delta(12, 10) == 20 and si.delta(8, 10) == -20
    assert si.delta(None, 10) is None and si.delta(5, 0) is None


def test_affluence_par_heure_et_jour():
    rows = [_row(9, 9, 4), _row(9, 9, 6), _row(8, 9, 2), _row(8, 11, 30)]
    hours = si.by_hour(rows)
    assert [h["label"] for h in hours] == ["09h", "10h", "11h"]   # plage continue
    assert hours[0]["avg_patients"] == 1.5 and hours[0]["avg_wait"] == 4
    assert hours[1]["avg_patients"] == 0 and hours[1]["avg_wait"] is None
    assert si.peak(hours)["label"] == "09h"
    days = si.by_weekday(rows)                   # 8 mars = vendredi, 9 = samedi
    assert [(d["label"], d["avg_patients"]) for d in days] == [("Vendredi", 2), ("Samedi", 2)]


def test_repartition_des_attentes_et_detail():
    rows = [_row(9, 9, 2, activity=1), _row(9, 9, 7, activity=1),
            _row(9, 9, 20, activity=2), _row(9, 9, 45, activity=2), _row(9, 9, activity=2)]
    assert [b["count"] for b in si.wait_distribution(rows)] == [1, 1, 1, 1]
    detail = si.breakdown(rows, "activity", {1: "Ordonnance", 2: "Conseil"})
    assert [(d["name"], d["count"], d["pct"]) for d in detail] == [("Conseil", 3, 60), ("Ordonnance", 2, 40)]
    assert detail[1]["avg_wait"] == 4.5


# --- Route -----------------------------------------------------------------------

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

    today = datetime.now(admin_stats.time_tz).date()
    yesterday = today - timedelta(days=1)
    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_stats=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance <b>", letter="A")
        conseil = Activity(name="Conseil", letter="C")
        c1 = Counter(name="Comptoir 1", sort_order=0)
        fr = Language(code="fr", name="Français", translation="Français", sort_order=1)
        db.session.add_all([ordo, conseil, c1, fr])
        db.session.flush()
        at = lambda d, h, m=0: datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)  # noqa: E731
        db.session.add_all([
            PatientHistory(call_number="A-1", activity_id=ordo.id, counter_id=c1.id, language_id=fr.id,
                           timestamp=at(yesterday, 9), timestamp_counter=at(yesterday, 9, 10),
                           timestamp_end=at(yesterday, 9, 15), day_of_week="x", status="done"),
            PatientHistory(call_number="C-1", activity_id=conseil.id, counter_id=c1.id, language_id=fr.id,
                           timestamp=at(yesterday, 10), timestamp_counter=at(yesterday, 10, 30),
                           timestamp_end=at(yesterday, 10, 35), day_of_week="x", status="done"),
            # Jamais entré dans la file : exclu des statistiques.
            PatientHistory(call_number="A-2", activity_id=ordo.id, timestamp=at(yesterday, 11),
                           day_of_week="x", status="print_failed"),
            Patient(call_number="A-3", activity_id=ordo.id, language_id=fr.id, status="standing",
                    timestamp=at(today, 0, 1)),
        ])
        db.session.add(AggregatedStats(date=today - timedelta(days=20), category_type="global", count=5))
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _charts(page):
    return [json.loads(html.unescape(m)) for m in re.findall(r"data-chart='([^']*)'", page)]


def test_tableau_de_bord(client):
    page = client.get("/admin/stats/insights", query_string={"period": "7"}).get_data(as_text=True)
    # 2 patients d'hier + 1 d'aujourd'hui ; l'impression échouée est exclue.
    assert re.search(r'fs-3 fw-bold">3<', page)
    assert "20,0" not in page
    assert "20 min" in page                     # attente moyenne (10 et 30)
    assert "Ordonnance &lt;b&gt;" in page        # libellé échappé
    charts = _charts(page)
    assert charts and charts[0]["type"] == "bar"
    assert 'data-period="7"' in page
    assert "moyennes" not in page                # aucun jour compressé dans ces 7 jours


def test_tableau_de_bord_filtre_et_jours_compresses(app, client):
    with app.app_context():
        conseil = Activity.query.filter_by(name="Conseil").one().id
    page = client.get("/admin/stats/insights",
                      query_string={"period": "28", "activity_filter": conseil}).get_data(as_text=True)
    assert re.search(r'fs-3 fw-bold">1<', page)
    assert "ne sont plus conservés qu&#39;en moyennes" in page or "ne sont plus conservés qu'en moyennes" in page


def test_tableau_de_bord_vide(client):
    page = client.get("/admin/stats/insights",
                      query_string={"period": "custom", "start_date": "2001-01-01",
                                    "end_date": "2001-01-02"}).get_data(as_text=True)
    assert "Aucun patient sur cette période" in page
