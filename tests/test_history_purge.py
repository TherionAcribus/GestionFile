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
from unittest.mock import MagicMock, patch

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
# Échec en cours de boucle : rollback explicite + résultat PARTIEL explicite
# ---------------------------------------------------------------------------

def _add_second_old_day(app):
    """Un second jour d'historique, pour provoquer un échec en cours de route."""
    with app.app_context():
        activity = Activity.query.first()
        db.session.add(PatientHistory(
            call_number="B1", timestamp=datetime.now() - timedelta(days=800),
            day_of_week="Monday", status="called", activity_id=activity.id,
        ))
        db.session.commit()


def test_purge_history_rolls_back_and_reports_partial(app):
    """Régression : sans rollback, la session restait en échec et la
    journalisation de l'erreur (job planifié) levait PendingRollbackError,
    masquant l'erreur d'origine. Le résultat partiel doit être explicite."""
    _add_second_old_day(app)

    with app.app_context():
        real_commit = db.session.commit
        calls = []

        def flaky_commit():
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("db down")
            return real_commit()

        with patch.object(db.session, "commit", side_effect=flaky_commit):
            with pytest.raises(scheduler_functions.PartialHistoryError) as exc:
                scheduler_functions.purge_history(365)

        message = str(exc.value)
        assert "PARTIEL" in message
        assert "1 journée(s)" in message          # premier jour déjà commité
        assert "db down" in message               # erreur d'origine conservée

        # Une seule journée supprimée (la première traitée — l'ordre des
        # journées n'est pas garanti), l'autre restaurée par le rollback ;
        # la ligne récente est toujours hors périmètre.
        assert PatientHistory.query.count() in (2, 3)
        assert PatientHistory.query.filter_by(call_number="A3").count() == 1
        # La session est saine : un commit ultérieur fonctionne (pas de
        # PendingRollbackError au moment de journaliser l'échec).
        scheduler_functions._record_job_execution("T", "failed", message)
        log = JobExecutionLog.query.filter_by(job_id="T").one()
        assert "PARTIEL" in log.error_message


def test_aggregate_history_partial_failure_reports_progress(app):
    """Même contrat côté archivage : un échec de create_daily_stats sur la
    seconde journée annule cette journée, conserve la première, signale le
    résultat partiel."""
    _add_second_old_day(app)

    with app.app_context():
        with patch.object(
            scheduler_functions, "create_daily_stats",
            side_effect=[None, RuntimeError("stats ko")],
        ):
            with pytest.raises(scheduler_functions.PartialHistoryError) as exc:
                scheduler_functions.aggregate_history(365)

        assert "PARTIEL" in str(exc.value)
        assert "stats ko" in str(exc.value)
        # Jour 1 supprimé (commité), jour 2 restauré par le rollback — l'ordre
        # des journées n'étant pas garanti, on vérifie qu'une seule journée
        # a disparu et que la ligne récente est intacte.
        assert PatientHistory.query.count() in (2, 3)
        assert PatientHistory.query.filter_by(call_number="A3").count() == 1


def test_record_job_execution_on_dirty_session(app):
    """Journaliser après une session en échec : sans le rollback interne, le
    commit du journal lèverait PendingRollbackError."""
    with app.app_context():
        # Violation de la contrainte unique -> flush en échec -> session
        # « pending rollback ».
        db.session.add_all([
            PatientHistory(call_number="X1", timestamp=datetime.now(),
                           day_of_week="Mon", status="called",
                           activity_id=1, patient_source_id=999),
            PatientHistory(call_number="X2", timestamp=datetime.now(),
                           day_of_week="Mon", status="called",
                           activity_id=1, patient_source_id=999),
        ])
        with pytest.raises(Exception):
            db.session.commit()

        scheduler_functions._record_job_execution("Test Job", "failed", "origine")
        log = JobExecutionLog.query.filter_by(job_id="Test Job").one()
        assert log.error_message == "origine"


def test_auto_archive_job_logs_partial_failure(app):
    """Un archivage partiel est journalisé 'failed' avec le détail — le
    résultat n'est pas présenté comme un succès ni masqué."""
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = True
    app.config["DATA_ARCHIVE_DAYS"] = 365
    app.config["DATA_ARCHIVE_COMPRESSED"] = False

    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.purge_history",
        side_effect=scheduler_functions.PartialHistoryError(
            "Purge PARTIEL : 3 journée(s) déjà validée(s)"),
    ):
        scheduler_functions.auto_archive_job()

    with app.app_context():
        log = JobExecutionLog.query.filter_by(
            job_id="Auto Archive Data").one()
        assert log.status == "failed"
        assert "PARTIEL" in log.error_message


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
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = True

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
# Désactivation automatique fiable : garde du job + réconciliation
# ---------------------------------------------------------------------------

def test_auto_archive_job_skips_when_disabled(app):
    """Un job resté dans le jobstore alors que l'option est désactivée ne doit
    PAS s'exécuter : il saute, consigne 'skipped' et se retire lui-même."""
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = False

    fake_scheduler = MagicMock()
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ), patch(
        "scheduler_functions.aggregate_history"
    ) as mock_agg, patch(
        "scheduler_functions.purge_history"
    ) as mock_purge:
        scheduler_functions.auto_archive_job()
        mock_agg.assert_not_called()
        mock_purge.assert_not_called()
        fake_scheduler.remove_job.assert_called_once_with(
            scheduler_functions.AUTO_ARCHIVE_JOB_ID)

    with app.app_context():
        log = JobExecutionLog.query.filter_by(job_id="Auto Archive Data").one()
        assert log.status == "skipped"


def test_auto_archive_job_runs_when_enabled(app):
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = True
    app.config["DATA_ARCHIVE_DAYS"] = 365
    app.config["DATA_ARCHIVE_COMPRESSED"] = True

    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.aggregate_history", return_value="ok"
    ) as mock_agg:
        scheduler_functions.auto_archive_job()
        mock_agg.assert_called_once_with(365)


def test_reconcile_adds_job_when_enabled(app):
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = True
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = None  # absent du jobstore

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        # Premier get_job (absent) -> add ; second (vérification) -> présent.
        fake_scheduler.get_job.side_effect = [None, MagicMock()]
        action = scheduler_functions.reconcile_auto_archive_job()
        assert action == "added"
        assert fake_scheduler.add_job.call_count == 1
        kwargs = fake_scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == scheduler_functions.AUTO_ARCHIVE_JOB_ID
        assert kwargs["func"] is scheduler_functions.auto_archive_job


def test_reconcile_removes_job_when_disabled(app):
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = False
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = MagicMock()  # présent

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        action = scheduler_functions.reconcile_auto_archive_job()
        assert action == "removed"
        fake_scheduler.remove_job.assert_called_once_with(
            scheduler_functions.AUTO_ARCHIVE_JOB_ID)


def test_reconcile_unchanged_when_consistent(app):
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = False
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = None

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        assert scheduler_functions.reconcile_auto_archive_job() == "unchanged"
        fake_scheduler.add_job.assert_not_called()
        fake_scheduler.remove_job.assert_not_called()


def test_reconcile_raises_when_add_does_not_take(app):
    """add_job suivi d'un get_job toujours vide -> exception (avertissement
    côté route, journal côté démarrage)."""
    app.config["DATA_AUTO_ARCHIVE_ENABLED"] = True
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = None  # toujours absent

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        with pytest.raises(RuntimeError):
            scheduler_functions.reconcile_auto_archive_job()


def test_update_config_warns_when_scheduler_fails(app, client):
    """Régression : un échec du scheduler ne doit plus être masqué par un
    succès — la réponse porte un avertissement explicite."""
    _login(client)
    with patch(
        "routes.admin_data.reconcile_auto_archive_job",
        side_effect=RuntimeError("jobstore down"),
    ):
        response = client.post("/admin/data/config", data={
            "archive_days": "365",
            "archive_compressed": "true",
            "auto_archive_enabled": "true",
        })
    payload = response.get_json()
    assert payload["success"] is True          # la config EST persistée
    assert "warning" in payload                # mais le client est prévenu


def test_update_config_no_warning_when_scheduler_ok(app, client):
    _login(client)
    with patch(
        "routes.admin_data.reconcile_auto_archive_job", return_value="added"
    ) as mock_reconcile:
        response = client.post("/admin/data/config", data={
            "archive_days": "365",
            "archive_compressed": "true",
            "auto_archive_enabled": "true",
        })
    payload = response.get_json()
    assert payload["success"] is True
    assert "warning" not in payload
    mock_reconcile.assert_called_once_with()


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


def test_auto_archive_job_checks_enabled_flag():
    """Régression : le job ne doit plus jamais archiver sans vérifier
    DATA_AUTO_ARCHIVE_ENABLED."""
    source = _read("scheduler_functions.py")
    body = _func_body(source, "auto_archive_job")
    assert "DATA_AUTO_ARCHIVE_ENABLED" in body


def test_startup_reconciles_jobs():
    """Le démarrage des rôles qui exécutent des tâches (scheduler / all)
    réconcilie le jobstore persistant avec la configuration."""
    source = _read("app.py")
    assert "_reconcile_scheduler_jobs()" in source
    body = _func_body(source, "_reconcile_scheduler_jobs")
    assert "reconcile_auto_archive_job" in body


def test_update_config_returns_warning_on_scheduler_failure():
    """Un échec du scheduler ne doit plus être absorbé en silence : la route
    doit renvoyer un avertissement exploitable par l'interface."""
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    assert "scheduler_warning" in body
    assert "'warning'" in body


def test_history_loops_rollback_and_mark_partial():
    """Régression : chaque boucle journée doit rollbacker sur échec et
    signaler explicitement le résultat partiel (commit par journée)."""
    source = _read("scheduler_functions.py")
    for func in ("aggregate_history", "purge_history"):
        body = _func_body(source, func)
        assert "db.session.rollback()" in body or "_raise_partial" in body
        assert "PARTIEL" in _func_body(source, "_raise_partial")


def test_jobs_log_via_clean_session_helper():
    """Tous les wrappers de job journalisent via _record_job_execution, qui
    rollback d'abord — jamais add+commit sur une session potentiellement en
    échec."""
    source = _read("scheduler_functions.py")
    for job in ("clear_all_patients_job", "clear_announce_calls_job",
                "auto_archive_job", "disable_buttons_for_activity_job",
                "enable_buttons_for_activity_job"):
        body = _func_body(source, job)
        assert "_record_job_execution" in body, (
            f"{job} journalise encore directement")
        assert "db.session.commit()" not in body
