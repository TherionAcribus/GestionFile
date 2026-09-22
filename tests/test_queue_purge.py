"""Extraction du métier « purge de la file » hors de la route Admin.

Avant, la purge était une fonction de vue décorée par @require_permission :
- la variante « avec sauvegarde » l'appelait sans renvoyer sa réponse -> 500 ;
- le planificateur l'appelait hors requête HTTP -> current_user indisponible.

Le service ``services.queue_service.purge_all_patients`` est désormais le seul
point d'entrée métier : pas de décorateur de permission, pas de dépendance à la
requête. La route protégée traduit son résultat en réponse HTTP (toast), le job
planifié l'appelle sous ``app.app_context()`` et consigne ``JobExecutionLog``.
"""

import ast
import inspect
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_queue as admin_queue
import scheduler_functions
import services.queue_service as queue_service
from app_holder import AppHolder
from audit_log import ACTION_CLEAR, OUTCOME_FAILURE, OUTCOME_SUCCESS
from models import (
    Activity,
    AuditLog,
    JobExecutionLog,
    Patient,
    PatientHistory,
    Role,
    User,
    db,
)
from services.queue_service import archive_and_purge_all_patients, purge_all_patients


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, template_folder="templates")
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        # db.metadatas est partagé entre fichiers de test : les fichiers
        # précédents ont déjà enregistré le bind 'users' — sans cette entrée,
        # db.create_all() lève UnboundExecutionError (convention de la suite).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        CRON_TRANSFER_PATIENT_TO_HISTORY=False,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_queue.admin_queue_bp)

    # Stub Flask-Security (non initialisée dans cette app de test minimale) :
    # require_permission redirige les anonymes vers security.login.
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    # Connexion de test, parallèle à security_oidc.py::_oidc_login.
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
        admin.roles.append(Role(name="admin", admin_queue=True))
        activity = Activity(name="Act", letter="A")
        db.session.add_all([admin, activity])
        db.session.commit()

        db.session.add(
            Patient(
                call_number="A1",
                status="standing",
                activity_id=activity.id,
                timestamp=datetime.now(),
            )
        )
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    client.post("/_test/login")


# ---------------------------------------------------------------------------
# Service métier : exécutable hors requête HTTP (chemin du planificateur)
# ---------------------------------------------------------------------------

def test_service_runs_without_request_context(app):
    """Le service fonctionne sous app_context seul : aucune requête, aucun
    current_user — c'est le contexte exact dans lequel tourne APScheduler."""
    with app.app_context():
        assert Patient.query.count() == 1
        removed = purge_all_patients()
        assert removed == 1
        assert Patient.query.count() == 0
        # Audit succès consigné par le service, sans requête ni utilisateur.
        audit = AuditLog.query.filter_by(action=ACTION_CLEAR).one()
        assert audit.outcome == OUTCOME_SUCCESS
        assert audit.username is None  # aucun current_user hors requête


def test_service_rolls_back_audits_failure_and_reraises(app):
    """Un échec du DELETE : rollback, audit OUTCOME_FAILURE (garanti aussi pour
    le planificateur), puis relance — l'appelant choisit la traduction."""
    with app.app_context():
        fake_db = MagicMock()
        fake_db.session.commit.side_effect = RuntimeError("boom")
        with patch.object(queue_service, "db", fake_db):
            with pytest.raises(RuntimeError):
                purge_all_patients()
        fake_db.session.rollback.assert_called_once()

        audit = AuditLog.query.filter_by(action=ACTION_CLEAR).one()
        assert audit.outcome == OUTCOME_FAILURE
        assert Patient.query.count() == 1  # rien n'a été supprimé


# ---------------------------------------------------------------------------
# archive_and_purge_all_patients : copie + purge en UNE transaction
# ---------------------------------------------------------------------------

def test_archive_and_purge_copies_then_deletes_atomically(app):
    """Historique rempli, file vidée, clé d'idempotence renseignée, audit."""
    with app.app_context():
        # L'id est capturée avant : après un DELETE bulk (synchronize_session
        # off), l'objet ORM expiré n'est plus relisable.
        patient_id = Patient.query.one().id
        removed = archive_and_purge_all_patients()
        assert removed == 1
        assert Patient.query.count() == 0
        history = PatientHistory.query.one()
        assert history.call_number == "A1"
        assert history.patient_source_id == patient_id
        audit = AuditLog.query.filter_by(action=ACTION_CLEAR).one()
        assert audit.outcome == OUTCOME_SUCCESS


def test_archive_and_purge_rolls_back_everything_on_duplicate(app):
    """Idempotence : une ligne d'historique portant déjà le patient_source_id
    du patient fait échouer le transfert — rollback complet, ni doublon ni
    suppression partielle (le défaut historique de la copie non atomique)."""
    with app.app_context():
        patient = Patient.query.one()
        db.session.add(PatientHistory(
            call_number="A1", timestamp=patient.timestamp,
            day_of_week="Monday", status="called",
            activity_id=patient.activity_id,
            patient_source_id=patient.id,
        ))
        db.session.commit()

        with pytest.raises(Exception):
            archive_and_purge_all_patients()

        # Rollback complet : la file est intacte, un seul enregistrement.
        assert Patient.query.count() == 1
        assert PatientHistory.query.count() == 1
        audit = AuditLog.query.filter_by(action=ACTION_CLEAR).one()
        assert audit.outcome == OUTCOME_FAILURE


# ---------------------------------------------------------------------------
# Routes : toujours une réponse, jamais un tuple tombé dans le vide
# ---------------------------------------------------------------------------

def test_route_clear_all_returns_response(app, client):
    _login(client)
    response = client.post("/admin/database/clear_all_patients")
    assert response.status_code == 204
    with app.app_context():
        assert Patient.query.count() == 0


def test_route_clear_all_with_saving_returns_response(app, client):
    """Régression : la variante « avec sauvegarde » appelait la purge sans
    renvoyer sa réponse — la suppression réussissait puis Flask renvoyait 500
    (« view did not return »)."""
    _login(client)
    response = client.post("/admin/database/clear_all_patients_with_saving")
    assert response.status_code == 204
    with app.app_context():
        assert Patient.query.count() == 0
        history = PatientHistory.query.one()
        assert history.call_number == "A1"
        # Clé d'idempotence : la ligne d'historique porte l'id du patient
        # dont elle provient.
        assert history.patient_source_id is not None


def test_route_with_saving_aborts_when_archive_fails(app, client):
    """Si l'archivage+purge échoue, la file reste intacte (la transaction
    unique a fait rollback) et la vue renvoie une réponse (toast d'erreur)."""
    _login(client)
    with patch(
        "routes.admin_queue.archive_and_purge_all_patients",
        side_effect=RuntimeError("db down"),
    ):
        response = client.post("/admin/database/clear_all_patients_with_saving")
    assert response.status_code == 200
    with app.app_context():
        assert Patient.query.count() == 1
        assert PatientHistory.query.count() == 0


def test_route_returns_toast_when_purge_fails(app, client):
    """Une exception métier ne produit plus le 500 HTML générique de Flask :
    la vue répond une erreur toast déterministe (``("", 200)``, convention
    display_toast de l'application)."""
    _login(client)
    with patch(
        "routes.admin_queue.purge_all_patients",
        side_effect=RuntimeError("db down"),
    ):
        response = client.post("/admin/database/clear_all_patients")
    assert response.status_code == 200
    with app.app_context():
        assert Patient.query.count() == 1


def test_routes_require_authentication(client):
    """Les deux routes restent derrière @require_permission : anonyme →
    redirection vers l'écran de connexion."""
    for url in (
        "/admin/database/clear_all_patients",
        "/admin/database/clear_all_patients_with_saving",
    ):
        response = client.post(url)
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Job planifié : service sous app_context, jamais la vue décorée
# ---------------------------------------------------------------------------

def test_scheduler_job_executes_real_purge(app):
    """Régression principale : le job exécute réellement la purge sous
    app_context — sans requête ni current_user. Si le code repassait par la
    vue @require_permission, ce test échouerait (current_user indisponible)."""
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ):
        scheduler_functions.clear_all_patients_job()

    with app.app_context():
        assert Patient.query.count() == 0
        log = JobExecutionLog.query.filter_by(job_id="Clear Patient Table").one()
        assert log.status == "success"
        assert AuditLog.query.filter_by(action=ACTION_CLEAR).count() == 1


def test_scheduler_job_logs_failure(app):
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.purge_all_patients",
        side_effect=RuntimeError("db down"),
    ):
        scheduler_functions.clear_all_patients_job()

    with app.app_context():
        log = JobExecutionLog.query.filter_by(job_id="Clear Patient Table").one()
        assert log.status == "failed"
        assert "db down" in log.error_message
        assert Patient.query.count() == 1  # purge non effectuée


def test_scheduler_job_archives_before_purging(app):
    """CRON_TRANSFER_PATIENT_TO_HISTORY choisit la variante atomique
    archive+purge ; sans le drapeau, la purge simple."""
    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = True
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.archive_and_purge_all_patients"
    ) as mock_archive, patch(
        "scheduler_functions.purge_all_patients"
    ) as mock_purge:
        scheduler_functions.clear_all_patients_job()
        mock_archive.assert_called_once_with()
        mock_purge.assert_not_called()

    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = False
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.archive_and_purge_all_patients"
    ) as mock_archive, patch(
        "scheduler_functions.purge_all_patients"
    ) as mock_purge:
        scheduler_functions.clear_all_patients_job()
        mock_archive.assert_not_called()
        mock_purge.assert_called_once_with()


def test_scheduler_job_fails_when_archive_fails(app):
    """Un échec de l'archivage atomique est consigné 'failed' — et comme la
    transaction est unique, la file reste intacte (rien à relancer)."""
    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = True
    with patch.object(AppHolder, "get_app", return_value=app), patch(
        "scheduler_functions._refresh_config"
    ), patch(
        "scheduler_functions.archive_and_purge_all_patients",
        side_effect=RuntimeError("db down"),
    ):
        scheduler_functions.clear_all_patients_job()

    with app.app_context():
        log = JobExecutionLog.query.filter_by(job_id="Clear Patient Table").one()
        assert log.status == "failed"
        assert Patient.query.count() == 1


# ---------------------------------------------------------------------------
# Gardes statiques : le découplage ne doit pas régresser
# ---------------------------------------------------------------------------

def test_service_has_no_request_or_permission_dependency():
    """Le service ne doit ni importer request/current_user ni porter de
    décorateur de permission : sinon le job planifié casse à nouveau."""
    source = inspect.getsource(queue_service)
    assert "from flask import request" not in source
    assert "from flask_login" not in source
    assert "import require_permission" not in source

    purge_source = inspect.getsource(queue_service.purge_all_patients)
    assert "@require_permission" not in purge_source
    assert "current_user" not in purge_source
    assert "request" not in purge_source


def test_scheduler_uses_service_not_route_view():
    """scheduler_functions appelle le service et n'importe plus de fonction de
    vue protégée depuis routes.admin_queue."""
    source = inspect.getsource(scheduler_functions)
    assert "from services.queue_service import" in source
    assert "purge_all_patients" in source
    assert "from routes.admin_queue import" not in source
    assert "purge_all_patients()" in source


def test_routes_call_service_not_sibling_view():
    """Aucune route ne doit appeler une autre vue (pattern qui a produit le
    bug « réponse non renvoyée » + le couplage scheduler→vue décorée)."""
    def _is_route_decorator(d):
        target = d.func if isinstance(d, ast.Call) else d
        return isinstance(target, ast.Attribute) and target.attr == "route"

    tree = ast.parse(inspect.getsource(admin_queue))
    view_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(_is_route_decorator(d) for d in node.decorator_list)
    }
    assert view_names  # garde-fou : des vues ont bien été détectées
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        called = {
            sub.func.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
        }
        assert not (called & view_names), (
            f"{node.name} appelle la vue {called & view_names} "
            "au lieu du service")


def test_routes_keep_permission_decorators():
    """Les deux routes HTTP restent protégées par @require_permission."""
    source = inspect.getsource(admin_queue)
    assert source.count("@require_permission('queue')") >= 2


def test_two_phase_transfer_removed():
    """Régression : plus de copie validée séparément de la purge — les deux
    appelants passent par le service atomique."""
    for mod in (admin_queue, scheduler_functions):
        src = inspect.getsource(mod)
        assert "transfer_patients_to_history" not in src
        assert "archive_and_purge_all_patients" in src


# ---------------------------------------------------------------------------
# Purge de démarrage (clear_old_patients_table) : même service que le job
# ---------------------------------------------------------------------------

def _add_old_patient(app):
    with app.app_context():
        activity = Activity.query.first()
        db.session.add(Patient(
            call_number="A9", status="standing", activity_id=activity.id,
            timestamp=datetime.now() - timedelta(days=3),
        ))
        db.session.commit()


def test_startup_cleanup_archives_old_patients_when_transfer_enabled(app):
    """Régression : la purge de démarrage contournait l'historisation — les
    patients de la veille étaient perdus malgré le drapeau de transfert."""
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = True
    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = True
    _add_old_patient(app)

    with app.app_context():
        scheduler_functions.clear_old_patients_table(app)
        # Le patient d'hier est historisé (avec sa clé source), celui
        # d'aujourd'hui reste en file.
        history = PatientHistory.query.one()
        assert history.call_number == "A9"
        assert history.patient_source_id is not None
        assert Patient.query.count() == 1
        assert AuditLog.query.filter_by(action=ACTION_CLEAR).count() == 1


def test_startup_cleanup_purges_without_history_when_transfer_disabled(app):
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = True
    app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"] = False
    _add_old_patient(app)

    with app.app_context():
        scheduler_functions.clear_old_patients_table(app)
        assert PatientHistory.query.count() == 0
        assert Patient.query.count() == 1  # seul le patient du jour reste


def test_startup_cleanup_disabled_flag_deletes_nothing(app):
    app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"] = False
    _add_old_patient(app)

    with app.app_context():
        scheduler_functions.clear_old_patients_table(app)
        assert Patient.query.count() == 2
        assert PatientHistory.query.count() == 0


def test_startup_cleanup_uses_transactional_service():
    """Garde statique : clear_old_patients_table ne supprime plus elle-même —
    elle délègue aux services (historisation/audit/rollback garantis)."""
    source = inspect.getsource(scheduler_functions.clear_old_patients_table)
    assert "archive_and_purge_old_patients" in source
    assert "purge_old_patients" in source
    assert ".delete(" not in source


def test_public_purge_functions_delegate_to_audited_cores():
    """L'audit succès/échec vit dans _purge/_archive_and_purge : chaque
    fonction publique doit déléguer à l'un d'eux (cf. test_audit_wiring)."""
    delegates = {
        "purge_all_patients": "_purge(",
        "archive_and_purge_all_patients": "_archive_and_purge(",
        "purge_old_patients": "_purge(",
        "archive_and_purge_old_patients": "_archive_and_purge(",
    }
    for func, core in delegates.items():
        body = inspect.getsource(getattr(queue_service, func))
        assert core in body, f"{func} ne délègue plus à {core} (audit perdu)"
