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
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_data as admin_data
import scheduler_functions
import services.retention_tasks as retention_tasks
from app_holder import AppHolder
from audit_log import ACTION_DELETE
from models import (
    Activity,
    AggregatedStats,
    AuditLog,
    IdempotencyKey,
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


@pytest.fixture(autouse=True)
def _reset_retention_state():
    """L'état de la tâche de rétention est global au module — on le réinitialise
    entre les tests pour éviter toute contamination."""
    retention_tasks._state.clear()
    yield
    retention_tasks._state.clear()


def _login(client):
    client.post("/_test/login")


def _run_sync(target, args, name):
    """Remplace le worker thread par une exécution immédiate — les tests de la
    tâche de fond restent déterministes (pas de course avec l'assertion)."""
    target(*args)


def _sync_task():
    return patch("services.retention_tasks._spawn_worker", _run_sync)


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
# Tâche de fond : verrou, progression par journée, état pollable
# ---------------------------------------------------------------------------

def test_task_reports_progress_per_day(app, client):
    """Chaque journée commitée met à jour l'état : jours faits/total, lignes,
    détail par journée — puis résultat final."""
    _login(client)
    _add_second_old_day(app)  # deux journées distinctes à traiter
    with _sync_task():
        client.post("/admin/data/purge", data={"days": "365"})

    with app.app_context():
        state = retention_tasks.retention_task_state()
        assert state["status"] == "done"
        assert state["operation"] == "purge"
        assert state["days_total"] == 2
        assert state["days_done"] == 2
        assert state["rows_done"] == 3          # A1, A2, B1
        assert len(state["recent_days"]) == 2   # résultat par journée
        assert "Purged 3" in state["result"]


def test_task_lock_refuses_concurrent_run(app, client):
    """Régression : une relance pendant l'exécution ne doit pas lancer une
    seconde opération — le verrou DB (idempotency_key) le couvre aussi entre
    processus."""
    _login(client)
    with app.app_context():
        db.session.add(IdempotencyKey(key="retention_task_lock"))
        db.session.commit()

    response = client.post("/admin/data/purge", data={"days": "365"})
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["running"] is True

    with app.app_context():
        assert PatientHistory.query.count() == 3  # rien n'a été supprimé


def test_task_lock_released_after_run(app, client):
    _login(client)
    with _sync_task():
        client.post("/admin/data/purge", data={"days": "365"})
    with app.app_context():
        assert IdempotencyKey.query.get("retention_task_lock") is None


def test_task_state_idle_and_external(app, client):
    """Sans tâche locale : 'idle' ; si le verrou DB est détenu par un autre
    processus, l'état le signale au lieu de prétendre idle."""
    _login(client)
    with app.app_context():
        assert retention_tasks.retention_task_state()["status"] == "idle"
        db.session.add(IdempotencyKey(key="retention_task_lock"))
        db.session.commit()
        state = retention_tasks.retention_task_state()
        assert state["status"] == "running" and state["external"] is True
        db.session.delete(IdempotencyKey.query.get("retention_task_lock"))
        db.session.commit()


def test_task_records_job_log_and_audit(app, client):
    """La tâche consigne son issue : JobExecutionLog + audit — elle n'est pas
    silencieuse hors requête."""
    _login(client)
    with _sync_task():
        client.post("/admin/data/purge", data={"days": "365"})
    with app.app_context():
        log = JobExecutionLog.query.filter_by(job_id="Manual Purge").one()
        assert log.status == "success"
        assert "Purged 2" in log.error_message
        audits = AuditLog.query.filter_by(resource="patient_history").all()
        assert audits


def test_task_records_partial_failure(app, client):
    """create_daily_stats repose sur timestampdiff (MySQL) : sous SQLite
    l'archivage échoue — la tâche doit consigner l'échec PARTIEL."""
    _login(client)
    with _sync_task():
        client.post("/admin/data/archive", data={"days": "365"})
    with app.app_context():
        state = retention_tasks.retention_task_state()
        assert state["status"] == "failed"
        assert state["partial"] is True
        log = JobExecutionLog.query.filter_by(job_id="Manual Archive").one()
        assert log.status == "failed"
        assert "PARTIEL" in log.error_message


def test_progress_callback_called_per_day(app):
    """Le paramètre progress() des boucles est appelé après chaque journée
    commitée — c'est la source de la progression pollée."""
    _add_second_old_day(app)
    with app.app_context():
        seen = []
        scheduler_functions.purge_history(
            365, progress=lambda day, rows, n: seen.append((day, rows, n)))
        assert len(seen) == 2
        assert seen[-1][2] == 2  # compteur de journées incrémenté
        assert sum(r for _, r, _ in seen) == 3


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
    """La route ne purge plus dans la requête : elle lance la tâche de fond
    (rendue synchrone ici) et répond immédiatement."""
    _login(client)
    with _sync_task():
        response = client.post("/admin/data/purge", data={"days": "365"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["task"] == "started"
    with app.app_context():
        assert PatientHistory.query.count() == 1
        audits = AuditLog.query.filter_by(
            action=ACTION_DELETE, resource="patient_history").all()
        assert audits  # lancement tracé + issue tracée par le worker
        assert any(a.outcome == "success" for a in audits)


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
    assert client.get("/admin/data/task").status_code == 302
    assert client.get("/admin/data/storage").status_code == 302


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


def test_ensure_heartbeat_adds_job_when_absent(app):
    """Sans battement, le processus scheduler ne relirait le jobstore
    partagé qu'à son prochain réveil connu — une tâche ajoutée par le web
    entre-temps serait découverte trop tard et sautée (misfire)."""
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = None  # absent du jobstore

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        action = scheduler_functions.ensure_scheduler_heartbeat_job()
        assert action == "added"
        fake_scheduler.add_job.assert_called_once()
        kwargs = fake_scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == scheduler_functions.HEARTBEAT_JOB_ID
        assert kwargs["func"] is scheduler_functions.scheduler_heartbeat_job
        assert kwargs["trigger"] == "interval"
        assert kwargs["seconds"] == 60


def test_ensure_heartbeat_unchanged_when_present(app):
    fake_scheduler = MagicMock()
    fake_scheduler.get_job.return_value = MagicMock()  # présent

    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        assert (
            scheduler_functions.ensure_scheduler_heartbeat_job()
            == "unchanged"
        )
        fake_scheduler.add_job.assert_not_called()


def test_startup_reconcile_covers_all_jobs():
    """Au démarrage des rôles qui exécutent des tâches (scheduler / all),
    _reconcile_scheduler_jobs couvre TOUT : les tâches de scheduler_functions
    via reconcile_scheduled_jobs et celles des activités via
    reconcile_activity_jobs — plus seulement l'archivage."""
    source = _read("app.py")
    body = _func_body(source, "_reconcile_scheduler_jobs")
    assert "reconcile_scheduled_jobs" in body
    assert "reconcile_activity_jobs" in body


def test_reconcile_scheduled_jobs_covers_every_task():
    """Le point de convergence doit couvrir toutes les tâches du module —
    y compris le battement et les deux purges alignées sur l'interrupteur."""
    source = _read("scheduler_functions.py")
    body = _func_body(source, "reconcile_scheduled_jobs")
    for fn in ("ensure_scheduler_heartbeat_job", "reconcile_auto_archive_job",
               "ensure_messaging_cleanup_job", "reconcile_clear_patient_table_job",
               "reconcile_clear_announce_calls_job"):
        assert f"{fn}()" in body, f"{fn} absent de reconcile_scheduled_jobs"


def test_reconcile_clear_patient_table_job(app):
    """La réconciliation aligne « Clear Patient Table » sur l'interrupteur :
    un job restant d'une activation passée est retiré ; active, la tâche est
    (re)créée — horaire et fuseau courants inclus."""
    # Activé -> (re)création systématique.
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = True
    with app.app_context(), patch(
        "scheduler_functions.add_scheduler_clear_all_patients",
        return_value=True) as mock_add:
        assert (
            scheduler_functions.reconcile_clear_patient_table_job()
            == "added")
        mock_add.assert_called_once_with()

    fake_scheduler = MagicMock()

    # Désactivé + job présent -> retrait.
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = False
    fake_scheduler.get_job.return_value = MagicMock()
    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        assert (
            scheduler_functions.reconcile_clear_patient_table_job()
            == "removed")
        fake_scheduler.remove_job.assert_called_once_with(
            scheduler_functions.CLEAR_PATIENT_TABLE_JOB_ID)

    # Désactivé + job absent -> inchangé.
    fake_scheduler.get_job.return_value = None
    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        assert (
            scheduler_functions.reconcile_clear_patient_table_job()
            == "unchanged")
        fake_scheduler.add_job.assert_not_called()


def test_reconcile_clear_announce_calls_job(app):
    """« Clear Announce Calls » est une maintenance TOUJOURS active (comme la
    purge messagerie) : plus d'interrupteur — la réconciliation (re)crée la
    tâche sans condition et ne la retire jamais."""
    with app.app_context(), patch(
        "scheduler_functions.scheduler_clear_announce_calls",
        return_value=True) as mock_add:
        assert (
            scheduler_functions.reconcile_clear_announce_calls_job()
            == "added")
        mock_add.assert_called_once_with()

    with app.app_context(), patch(
        "scheduler_functions.scheduler_clear_announce_calls",
        return_value=False):
        assert (
            scheduler_functions.reconcile_clear_announce_calls_job()
            == "unchanged")


def test_scheduler_clear_announce_calls_fixed_time(app):
    """Régression : l'horaire n'est plus configurable — la maintenance passe
    à l'heure fixe 03:20 (fuseau épinglé du scheduler), indépendamment de
    l'ancienne configuration persistée."""
    fake_scheduler = MagicMock()
    # get_job : absent avant add_job, présent à la vérification post-création.
    fake_scheduler.get_job.side_effect = [None, MagicMock()]
    with app.app_context(), patch(
        "scheduler_functions.scheduler", fake_scheduler
    ):
        assert scheduler_functions.scheduler_clear_announce_calls() is True
        kwargs = fake_scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == scheduler_functions.CLEAR_ANNOUNCE_CALLS_JOB_ID
        assert kwargs["trigger"] == "cron"
        assert kwargs["hour"] == 3 and kwargs["minute"] == 20
        assert kwargs["func"] is scheduler_functions.clear_announce_calls_job


def test_config_change_delegates_to_reconcile():
    """Régression : changer l'horaire recréait la tâche même interrupteur
    éteint — le chemin input/switch délègue à la réconciliation (les options
    du cache d'annonces n'ont plus ni horaire ni interrupteur)."""
    source = _read("routes/admin_config.py")
    for func in ("special_functions_with_input", "call_function_with_switch"):
        body = _func_body(source, func)
        assert "reconcile_clear_patient_table_job" in body
    assert "cron_delete_announce_calls" not in source


def test_cron_jobs_check_enabled_flag_at_runtime():
    """Régression : le job patients purge même si l'interrupteur a été éteint
    — garde-fou à l'exécution, même motif que auto_archive_job. Le cache des
    annonces, lui, est une maintenance inconditionnelle : aucun garde-fou."""
    source = _read("scheduler_functions.py")
    body = _func_body(source, "clear_all_patients_job")
    assert "CRON_DELETE_PATIENT_TABLE_ACTIVATED" in body
    assert "'skipped'" in body
    body = _func_body(source, "clear_announce_calls_job")
    assert "ACTIVATED" not in body
    assert "'skipped'" not in body


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


def test_clear_announces_call_purge_sans_notification(app, tmp_path):
    """Régression : la purge des annonces renvoyait (« », 200) comme une vue
    alors que ce n'est pas une route, et diffusait un toast à TOUS les
    administrateurs en pleine nuit — fonction de fond : retour ``None``,
    aucune notification."""
    app.static_folder = str(tmp_path / "static")
    folder = tmp_path / "static" / "audio" / "annonces"
    folder.mkdir(parents=True)
    (folder / "patient_42.mp3").write_bytes(b"legacy")       # purgé
    cached = folder / ("a" * 64 + ".mp3")
    cached.write_bytes(b"cache")                           # conservé (récent)

    with app.app_context(), patch.object(
            scheduler_functions, "communikation") as mock_comm:
        result = scheduler_functions.clear_announces_call()

    assert result is None                      # pas une route : pas de réponse
    assert not (folder / "patient_42.mp3").exists()
    assert cached.exists()
    mock_comm.assert_not_called()              # aucune notification nocturne


def _setup_announce_cache(app, tmp_path):
    """Pointe le répertoire statique vers tmp_path et crée le dossier cache."""
    app.static_folder = str(tmp_path / "static")
    folder = tmp_path / "static" / "audio" / "annonces"
    folder.mkdir(parents=True)
    return folder


def _touch(path, age_seconds):
    """Écrit le fichier et fixe son mtime à ``age_seconds`` dans le passé."""
    path.write_bytes(b"x" * 200)
    old = os.path.getmtime(path) - age_seconds
    os.utime(path, (old, old))


def test_clear_announces_call_full_cleanup(app, tmp_path):
    """La maintenance supprime : cache expiré (> rétention), temporaire
    abandonné (> 1 h), legacy ``patient_*`` — et conserve le reste."""
    folder = _setup_announce_cache(app, tmp_path)
    day = 24 * 60 * 60
    _touch(folder / ("a" * 64 + ".mp3"), 40 * day)   # cache expiré -> purgé
    _touch(folder / ("b" * 64 + ".mp3"), 2 * day)    # cache récent -> conservé
    _touch(folder / ".abc.tmp.mp3", 2 * 3600)        # tmp abandonné -> purgé
    _touch(folder / ".def.tmp.mp3", 60)              # tmp récent -> conservé
    (folder / "patient_42.mp3").write_bytes(b"legacy")  # legacy -> purgé
    (folder / "notice.txt").write_text("autre")      # non géré -> conservé

    with app.app_context():
        scheduler_functions.clear_announces_call()

    assert not (folder / ("a" * 64 + ".mp3")).exists()
    assert (folder / ("b" * 64 + ".mp3")).exists()
    assert not (folder / ".abc.tmp.mp3").exists()
    assert (folder / ".def.tmp.mp3").exists()
    assert not (folder / "patient_42.mp3").exists()
    assert (folder / "notice.txt").exists()


def test_clear_announces_call_retention_from_config(app, tmp_path):
    """La durée de conservation vient de ANNOUNCE_CACHE_RETENTION_DAYS
    (31 jours par défaut)."""
    folder = _setup_announce_cache(app, tmp_path)
    cached = folder / ("c" * 64 + ".mp3")
    _touch(cached, 10 * 24 * 60 * 60)                # 10 jours

    with app.app_context():
        scheduler_functions.clear_announces_call()   # défaut 31 j -> conservé
    assert cached.exists()

    app.config["ANNOUNCE_CACHE_RETENTION_DAYS"] = 5
    with app.app_context():
        scheduler_functions.clear_announces_call()   # 5 j -> purgé
    assert not cached.exists()


def test_clear_announces_call_restats_before_delete(app, tmp_path):
    """La date est RELUE juste avant la suppression : un son relu entre le
    listage et le unlink (mtime rafraîchie par le cache) n'est pas effacé."""
    folder = _setup_announce_cache(app, tmp_path)
    stale = folder / ("d" * 64 + ".mp3")
    _touch(stale, 40 * 24 * 60 * 60)                 # expiré au listage

    with app.app_context(), patch(
        "os.path.getmtime", return_value=time.time()
    ):
        # getmtime relu « frais » : le fichier a été relu entre-temps.
        scheduler_functions.clear_announces_call()
    assert stale.exists()


def test_announce_cache_stats(app, tmp_path):
    """Statistiques de la section admin : familles gérées comptées, taille."""
    folder = _setup_announce_cache(app, tmp_path)
    (folder / ("e" * 64 + ".mp3")).write_bytes(b"x" * 2048)
    (folder / ".f.tmp.mp3").write_bytes(b"y" * 1024)
    (folder / "notice.txt").write_text("non géré")

    with app.app_context():
        stats = scheduler_functions.announce_cache_stats()

    assert stats["count"] == 2
    assert stats["size_bytes"] == 3072
    assert stats["size_label"] == "3 Ko"


def test_purge_announce_cache_empties_everything(app, tmp_path):
    """« Vider le cache maintenant » : toutes les familles, sans condition de
    date ; retourne le décompte et les octets libérés."""
    folder = _setup_announce_cache(app, tmp_path)
    (folder / ("a" * 64 + ".mp3")).write_bytes(b"x" * 100)
    (folder / ".b.tmp.mp3").write_bytes(b"y" * 50)
    (folder / "patient_1.mp3").write_bytes(b"z" * 25)
    (folder / "notice.txt").write_text("conservé")

    with app.app_context():
        deleted, freed = scheduler_functions.purge_announce_cache()

    assert deleted == 3 and freed == 175
    assert (folder / "notice.txt").exists()
    assert list(folder.iterdir()) == [folder / "notice.txt"]


def test_night_jobs_never_notify_admins():
    """Régression statique : aucune tâche planifiée ne diffuse de toast —
    ``display_toast`` émet vers TOUS les admins via ``communikation('admin',
    ...)`` : du bruit à 3 h du matin, plus des échecs masqués. Le retour se
    fait par JobExecutionLog + journaux + avertissement de route (point f)."""
    source = _read("scheduler_functions.py")
    assert "display_toast(" not in source
    assert "from ui_feedback import" not in source
    body = _func_body(source, "clear_announces_call")
    assert 'return ""' not in body
    assert "communikation" not in body
    assert "raise" in body                     # l'échec remonte au job englobant


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
    for route in ("/admin/data/archive", "/admin/data/purge",
                  "/admin/data/preview", "/admin/data/task"):
        assert route in source
    # Les handlers ne traitent plus dans la requête : ils délèguent à la
    # tâche de fond (verrou + progression) via _launch_retention.
    for func in ("manual_archive", "manual_purge"):
        body = _func_body(source, func)
        assert "_launch_retention" in body
        assert "aggregate_history" not in body and "purge_history" not in body
    body = _func_body(source, "_launch_retention")
    assert "_validate_days" in body
    assert "start_retention_task" in body
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
    # Exécution en tâche de fond : le client poll l'état de progression.
    assert "/admin/data/task" in js
    assert "pollRetentionTask" in js


def test_auto_archive_job_checks_enabled_flag():
    """Régression : le job ne doit plus jamais archiver sans vérifier
    DATA_AUTO_ARCHIVE_ENABLED."""
    source = _read("scheduler_functions.py")
    body = _func_body(source, "auto_archive_job")
    assert "DATA_AUTO_ARCHIVE_ENABLED" in body


def test_startup_reconciles_jobs():
    """Le démarrage des rôles qui exécutent des tâches (scheduler / all)
    réconcilie le jobstore persistant avec la configuration — via le point
    de convergence qui inclut l'archivage (cf. test_reconcile_scheduled_jobs_*
    et test_startup_reconcile_covers_all_jobs)."""
    source = _read("app.py")
    assert "_reconcile_scheduler_jobs()" in source
    body = _func_body(source, "_reconcile_scheduler_jobs")
    assert "reconcile_scheduled_jobs" in body


def test_update_config_returns_warning_on_scheduler_failure():
    """Un échec du scheduler ne doit plus être absorbé en silence : la route
    doit renvoyer un avertissement exploitable par l'interface."""
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    assert "scheduler_warning" in body
    assert "'warning'" in body


# ---------------------------------------------------------------------------
# Espace disque : logique réutilisable vs physique du tablespace
# ---------------------------------------------------------------------------

def test_storage_stats_sqlite(app):
    """Sur SQLite : taille du fichier vs pages de la freelist (réutilisables),
    VACUUM comme opération de maintenance."""
    with app.app_context():
        info = scheduler_functions.storage_stats()
        assert info["supported"] is True
        assert info["engine"] == "sqlite"
        table = info["tables"][0]
        assert table["physical_bytes"] > 0
        assert table["reusable_bytes"] >= 0
        assert info["maintenance"] == "VACUUM"


def test_storage_stats_distinguishes_logical_and_physical(app):
    """Le relevé expose séparément les deux notions — c'est le cœur du point."""
    with app.app_context():
        info = scheduler_functions.storage_stats()
        table = info["tables"][0]
        assert "physical_bytes" in table and "reusable_bytes" in table
        assert "physical" in table and "reusable" in table  # formats lisibles


def test_storage_route_returns_stats(app, client):
    _login(client)
    response = client.get("/admin/data/storage")
    info = response.get_json()
    assert info["success"] is True
    assert info["supported"] is True


def test_storage_route_requires_authentication(client):
    assert client.get("/admin/data/storage").status_code == 302


def test_no_automatic_optimize_after_purge():
    """Régression : OPTIMIZE TABLE/VACUUM ne doit JAMAIS être exécuté par les
    purges (opération longue et gourmande) — c'est une maintenance planifiée."""
    source = _read("scheduler_functions.py")
    for func in ("aggregate_history", "purge_history"):
        body = _func_body(source, func)
        assert "OPTIMIZE TABLE" not in body and "VACUUM" not in body
    qsrc = _read("services/queue_service.py")
    assert "OPTIMIZE TABLE" not in qsrc and "VACUUM" not in qsrc


def test_storage_stats_mysql_branch_uses_information_schema():
    source = _read("scheduler_functions.py")
    body = _func_body(source, "storage_stats")
    assert "information_schema.TABLES" in body   # taille physique MySQL
    assert "data_free" in body                   # espace réutilisable InnoDB
    assert "OPTIMIZE TABLE" in body              # maintenance planifiée (info)


def test_template_and_js_expose_storage_card():
    html = _read("templates/admin/data.html")
    assert "storageStats" in html
    assert "maintenance" in html.lower()
    assert "partitionn" in html  # piste long terme : partitionnement par date
    js = _read("static/js/admin_data.js")
    assert "/admin/data/storage" in js
    assert "loadStorageStats" in js


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
