"""Séparation archivage / purge de l'historique (audit « purge des données »).

Avant : ``archive_data(days, compress=False)`` supprimait les lignes
détaillées de ``PatientHistory`` sans créer aucune statistique — une purge
définitive présentée à l'IHM comme un « archivage », et l'action manuelle se
lançait sans confirmation ni décompte.

Désormais :

* ``aggregate_history`` : agrégation quotidienne PUIS suppression des détails
  (le vrai archivage — les dossiers individuels disparaissent, les moyennes
  par jour subsistent) ;
* ``purge_history`` : suppression définitive sans agrégation, avec export CSV
  optionnel vers ``instance/exports/`` ;
* ``count_history_before`` / ``count_aggregated_before`` : décomptes sans
  effet de bord, exposés par ``GET /admin/data/preview`` pour la modale de
  confirmation ;
* routes distinctes : ``POST /admin/data/archive`` et ``POST /admin/data/purge``.

Les tests réels tournent sur SQLite (purge, export, décompte, routes). La
fonction d'agrégation repose sur ``timestampdiff`` — spécifique MySQL — et
n'est donc vérifiée que statiquement, comme le reste de la suite.
"""

import csv
import os
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_data as admin_data
import scheduler_functions
from app_holder import AppHolder
from audit_log import ACTION_DELETE
from models import (
    Activity,
    AggregatedStats,
    AuditLog,
    JobExecutionLog,
    PatientHistory,
    Role,
    User,
    db,
)


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _func_body(source, func):
    m = re.search(r"def " + func + r"\(.*?\n(.*?)(?=\ndef |\Z)", source, re.DOTALL)
    assert m, f"fonction {func} introuvable"
    return m.group(1)


@pytest.fixture()
def app(tmp_path):
    app = Flask(
        __name__,
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
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

    app.register_blueprint(admin_data.admin_data_bp)
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
        admin.roles.append(Role(name="admin", admin_options=True))
        activity = Activity(name="Act", letter="A")
        db.session.add_all([admin, activity])
        db.session.commit()

        old = datetime.now() - timedelta(days=400)
        recent = datetime.now() - timedelta(days=10)
        db.session.add_all([
            PatientHistory(
                call_number="A1", timestamp=old, day_of_week="Monday",
                status="called", activity_id=activity.id,
            ),
            PatientHistory(
                call_number="A2", timestamp=old, day_of_week="Monday",
                status="called", activity_id=activity.id,
            ),
            PatientHistory(
                call_number="A3", timestamp=recent, day_of_week="Monday",
                status="called", activity_id=activity.id,
            ),
        ])
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    client.post("/_test/login")


# ---------------------------------------------------------------------------
# purge_history : suppression définitive, sans agrégation
# ---------------------------------------------------------------------------

def test_purge_history_deletes_old_rows_without_stats(app):
    """Régression principale : la purge ne crée AUCUNE statistique agrégée —
    c'est précisément ce qui la distingue de l'archivage."""
    with app.app_context():
        result = scheduler_functions.purge_history(365)
        assert PatientHistory.query.count() == 1  # seule la ligne récente reste
        assert AggregatedStats.query.count() == 0  # rien n'a été agrégé
        assert "Purged 2" in result


def test_purge_history_export_csv(app, tmp_path):
    """export_csv=True conserve une copie des lignes avant suppression."""
    with app.app_context():
        result = scheduler_functions.purge_history(365, export_csv=True)
        assert PatientHistory.query.count() == 1

        export_dir = tmp_path / "instance" / "exports"
        files = list(export_dir.glob("patient_history_*.csv"))
        assert len(files) == 1
        assert files[0].name in result

        with open(files[0], newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0][0] == "id"  # en-tête
        assert len(rows) == 3      # en-tête + 2 lignes purgées
        assert {r[1] for r in rows[1:]} == {"A1", "A2"}


def test_purge_history_no_export_by_default(app, tmp_path):
    with app.app_context():
        scheduler_functions.purge_history(365)
        assert not (tmp_path / "instance" / "exports").exists()


# ---------------------------------------------------------------------------
# Décomptes préalables (modale de confirmation)
# ---------------------------------------------------------------------------

def test_count_history_before(app):
    with app.app_context():
        info = scheduler_functions.count_history_before(365)
        assert info["rows"] == 2
        assert info["days"] == 1
        assert info["oldest"] is not None and info["newest"] is not None

        info = scheduler_functions.count_history_before(3650)
        assert info["rows"] == 0
        assert info["oldest"] is None


def test_count_aggregated_before(app):
    with app.app_context():
        db.session.add(AggregatedStats(
            date=datetime.now().date() - timedelta(days=800),
            category_type="global", category_id=None, count=5,
        ))
        db.session.commit()
        info = scheduler_functions.count_aggregated_before(365)
        assert info["rows"] == 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_purge_route_deletes_and_audits(app, client):
    _login(client)
    response = client.post("/admin/data/purge", data={"days": "365"})
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    with app.app_context():
        assert PatientHistory.query.count() == 1
        audit = AuditLog.query.filter_by(
            action=ACTION_DELETE, resource="patient_history").one()
        assert audit.outcome == "success"


def test_purge_route_rejects_invalid_days(app, client):
    _login(client)
    response = client.post("/admin/data/purge", data={"days": "-5"})
    assert response.get_json()["success"] is False
    with app.app_context():
        assert PatientHistory.query.count() == 3


def test_preview_route_returns_counts(app, client):
    _login(client)
    response = client.get("/admin/data/preview?days=365&target=history")
    info = response.get_json()
    assert info["success"] is True
    assert info["rows"] == 2


def test_preview_route_is_read_only(app, client):
    """La prévisualisation ne doit rien supprimer — elle alimente la modale."""
    _login(client)
    client.get("/admin/data/preview?days=365&target=history")
    with app.app_context():
        assert PatientHistory.query.count() == 3


def test_data_routes_require_authentication(client):
    assert client.post("/admin/data/archive", data={"days": "365"}).status_code == 302
    assert client.post("/admin/data/purge", data={"days": "365"}).status_code == 302
    assert client.get("/admin/data/preview?days=30").status_code == 302


# ---------------------------------------------------------------------------
# Job planifié : le choix agrégation/purge est explicite
# ---------------------------------------------------------------------------

def test_auto_archive_job_dispatches_on_compress(app):
    """compress=True -> agrégation ; compress=False -> purge définitive.
    L'ancien archive_data(compress=...) masquait cette différence."""
    app.config["DATA_ARCHIVE_DAYS"] = 365
    app.config["DATA_ARCHIVE_COMPRESSED"] = False

    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.aggregate_history"
    ) as mock_agg, patch(
        "scheduler_functions.purge_history", return_value="ok"
    ) as mock_purge:
        scheduler_functions.auto_archive_job()
        mock_purge.assert_called_once_with(365)
        mock_agg.assert_not_called()

    with app.app_context():
        log = JobExecutionLog.query.filter_by(job_id="Auto Archive Data").one()
        assert log.status == "success"

    app.config["DATA_ARCHIVE_COMPRESSED"] = True
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.aggregate_history", return_value="ok"
    ) as mock_agg, patch(
        "scheduler_functions.purge_history"
    ) as mock_purge:
        scheduler_functions.auto_archive_job()
        mock_agg.assert_called_once_with(365)
        mock_purge.assert_not_called()


# ---------------------------------------------------------------------------
# Gardes statiques : la séparation ne doit pas régresser
# ---------------------------------------------------------------------------

def test_explicit_functions_exist_and_archive_data_is_gone():
    source = _read("scheduler_functions.py")
    for func in ("aggregate_history", "purge_history",
                 "count_history_before", "count_aggregated_before",
                 "export_history_csv"):
        assert f"def {func}(" in source
    # L'ancienne API ambiguë a disparu : plus de compress=True/False implicite.
    assert "def archive_data(" not in source


def test_purge_never_aggregates():
    """purge_history ne doit jamais appeler create_daily_stats."""
    source = _read("scheduler_functions.py")
    body = _func_body(source, "purge_history")
    assert "create_daily_stats" not in body


def test_routes_use_explicit_functions():
    source = _read("routes/admin_data.py")
    for route in ("/admin/data/archive", "/admin/data/purge", "/admin/data/preview"):
        assert route in source
    body = _func_body(source, "manual_archive")
    assert "aggregate_history" in body
    body = _func_body(source, "manual_purge")
    assert "purge_history" in body
    assert "_validate_days" in body
    assert "record_audit" in body


def test_archive_and_purge_are_post_only():
    source = _read("routes/admin_data.py")
    for url in ("/admin/data/archive", "/admin/data/purge"):
        m = re.search(
            r"route\(\s*['\"]" + re.escape(url) + r"['\"]\s*,\s*methods\s*=\s*\[([^\]]*)\]",
            source)
        assert m, f"route {url} introuvable ou sans methods="
        methods = m.group(1).upper()
        assert "POST" in methods and "GET" not in methods


def test_template_separates_archive_and_purge():
    html = _read("templates/admin/data.html")
    # Deux actions distinctes, libellées explicitement.
    assert "btn-manual-archive" in html
    assert "btn-purge-history" in html
    assert "purgeDaysInput" in html
    # La case « compresser » ambiguë a disparu de l'action manuelle.
    assert "compressCheck" not in html
    # Sauvegarde CSV optionnelle sur les deux actions.
    assert "archiveBackupCheck" in html
    assert "purgeBackupCheck" in html


def test_js_confirms_with_preview():
    js = _read("static/js/admin_data.js")
    # Décompte préalable avant toute action manuelle.
    assert "/admin/data/preview" in js
    # Les deux opérations ont chacune leur endpoint.
    assert "/admin/data/archive" in js
    assert "/admin/data/purge" in js
    # Pas d'appel direct sans modale : les boutons passent par
    # openDataConfirmModal, l'exécution par data-confirm-*.
    assert "openDataConfirmModal" in js
    assert "data-confirm-days" in js
