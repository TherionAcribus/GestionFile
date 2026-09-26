"""Liste des tâches planifiées : affichage lisible et fin du N+1 (point g).

Avant, ``/admin/database/schedule_tasks_list`` :

- affichait les internes APScheduler bruts (nom de fonction Python,
  ``cron[...]``, ``coalesce``, ``max_instances``) et l'heure sans fuseau ;
- colorait le statut ``skipped`` en rouge comme une erreur ;
- émettait une requête SQL par tâche pour ses 5 dernières exécutions.

La page réutilise désormais ``scheduler_dashboard.build_jobs_info`` — une seule
requête fenêtrée (``recent_executions_by_job``) — avec des champs détaillés
lisibles (``label``, ``schedule``, ``status_label``/``status_class``).

Couverts ici : requête groupée fonctionnelle (SQLite), assemblage détaillé,
traductions (déclencheur, libellé, statut) et gardes statiques sur la route et
le gabarit.
"""

import os
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import scheduler_dashboard
import scheduler_functions
from app_holder import AppHolder
from models import Activity, JobExecutionLog, db


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Fixture : app Flask + SQLite réels (recent_executions_by_job interroge la base)
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        # db.metadatas est partagé entre fichiers de test : le bind 'users'
        # est requis par db.create_all() (convention de la suite).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        db.session.add(Activity(name="Ordonnance", letter="O"))
        db.session.commit()
    yield app


def _log(job_id, status, error=None, when=None):
    return JobExecutionLog(
        job_id=job_id, status=status, error_message=error,
        execution_time=when or datetime.utcnow())


def _job(job_id, trigger=None, next_run_time=None):
    return SimpleNamespace(id=job_id, trigger=trigger, next_run_time=next_run_time)


# ---------------------------------------------------------------------------
# Requête groupée : N dernières exécutions par tâche en UNE requête
# ---------------------------------------------------------------------------

def test_recent_executions_by_job_grouped(app):
    with app.app_context():
        for i in range(7):
            db.session.add(_log("Clear Patient Table", "success",
                                when=datetime(2024, 1, 1, 0, 0, i)))
        db.session.add(_log("Other Job", "failed", "boom"))
        db.session.add(_log("Unrelated", "success"))
        db.session.commit()

        executions = scheduler_dashboard.recent_executions_by_job(
            ["Clear Patient Table", "Other Job"], per_job=5)

    assert len(executions["Clear Patient Table"]) == 5
    assert len(executions["Other Job"]) == 1
    assert "Unrelated" not in executions
    # Ordonnées du plus récent au plus ancien.
    times = [log.execution_time for log in executions["Clear Patient Table"]]
    assert times == sorted(times, reverse=True)
    assert executions["Other Job"][0].status == "failed"


def test_recent_executions_by_job_empty_ids():
    assert scheduler_dashboard.recent_executions_by_job([]) == {}


# ---------------------------------------------------------------------------
# Assemblage détaillé : champs lisibles + statuts traduits
# ---------------------------------------------------------------------------

def test_build_jobs_info_detailed(app, monkeypatch):
    with app.app_context():
        activity_id = Activity.query.first().id
        job_id = f"enable_{activity_id}_mon_0900"
        db.session.add(_log("Clear Patient Table", "skipped", "option off"))
        db.session.add(_log(job_id, "success"))
        db.session.commit()

        def fake_recent(ids, per_job):
            assert per_job == 5
            return {
                "Clear Patient Table": [JobExecutionLog.query.filter_by(
                    job_id="Clear Patient Table").first()],
                job_id: [JobExecutionLog.query.filter_by(job_id=job_id).first()],
            }

        monkeypatch.setattr(
            scheduler_dashboard, "recent_executions_by_job", fake_recent)

        main, other = scheduler_dashboard.build_jobs_info(
            [_job("Clear Patient Table"), _job(job_id)],
            per_job=5, detailed=True)

    assert [j["id"] for j in main] == ["Clear Patient Table"]
    assert main[0]["label"] == "Vidage quotidien de la file de patients"
    # « skipped » est un avertissement, pas une erreur (fin du rouge).
    assert main[0]["last_executions"][0]["status_label"] == "Ignorée"
    assert main[0]["last_executions"][0]["status_class"] == "text-warning"

    assert other[0]["label"] == "Ouverture de « Ordonnance » (lundi à 09:00)"
    assert other[0]["last_executions"][0]["status_label"] == "Réussie"
    # L'horodatage détaillé est converti en heure de Paris (fuseau porté).
    assert other[0]["last_executions"][0]["time"].tzinfo is not None


# ---------------------------------------------------------------------------
# Traductions pures : déclencheur, libellé, statut
# ---------------------------------------------------------------------------

def test_describe_trigger_cron_daily():
    from apscheduler.triggers.cron import CronTrigger
    assert scheduler_dashboard.describe_trigger(
        CronTrigger(hour=2, minute=0)) == "tous les jours à 02:00"


def test_describe_trigger_cron_weekdays():
    from apscheduler.triggers.cron import CronTrigger
    assert scheduler_dashboard.describe_trigger(
        CronTrigger(day_of_week="mon", hour=9, minute=0)
    ) == "chaque lundi à 09:00"
    assert scheduler_dashboard.describe_trigger(
        CronTrigger(day_of_week="mon,wed,fri", hour=23, minute=59)
    ) == "les lundi, mercredi et vendredi à 23:59"


def test_describe_trigger_interval():
    from apscheduler.triggers.interval import IntervalTrigger
    from datetime import timedelta
    assert scheduler_dashboard.describe_trigger(
        IntervalTrigger(seconds=60)) == "chaque minute"
    assert scheduler_dashboard.describe_trigger(
        IntervalTrigger(seconds=30)) == "toutes les 30 secondes"
    assert scheduler_dashboard.describe_trigger(
        IntervalTrigger(seconds=300)) == "toutes les 5 minutes"


def test_describe_job_labels_and_activity_pattern():
    assert scheduler_dashboard.describe_job("Scheduler Heartbeat") == \
        "Synchronisation du planificateur"
    assert scheduler_dashboard.describe_job(
        "disable_3_fri_2359", {3: "Ordonnance"}
    ) == "Fermeture de « Ordonnance » (vendredi à 23:59)"
    # Activité supprimée : repli explicite plutôt qu'une chaîne technique.
    assert scheduler_dashboard.describe_job("enable_9_mon_0800", {}) == \
        "Ouverture de « activité 9 » (lundi à 08:00)"
    # Inconnu : l'identifiant est retourné tel quel.
    assert scheduler_dashboard.describe_job("custom-job") == "custom-job"


def test_execution_status_display():
    assert scheduler_dashboard.execution_status_display("success") == \
        ("Réussie", "text-success")
    # « skipped » ne doit JAMAIS être rouge : ce n'est pas une erreur.
    assert scheduler_dashboard.execution_status_display("skipped") == \
        ("Ignorée", "text-warning")
    assert scheduler_dashboard.execution_status_display("failed")[1] == \
        "text-danger"


# ---------------------------------------------------------------------------
# Journalisation des jobs d'activité sous leur vrai identifiant APScheduler
# ---------------------------------------------------------------------------

def test_activity_job_records_under_real_job_id(app):
    """Les exécutions d'un job ``enable_{id}_{jour}_{HHMM}`` sont consignées
    sous CE nom (argument ``log_job_id`` passé à la création) — sinon la
    requête groupée ne pouvait jamais les rattacher (ancien id agrégé
    ``Enable_Buttons_Activity_{id}``)."""
    AppHolder.set_app(app)
    try:
        with patch.object(scheduler_functions, "communikation"):
            with app.app_context():
                activity = Activity.query.first()
                scheduler_functions.enable_buttons_for_activity_job(
                    activity.id, "enable_1_mon_0900")
                logged = JobExecutionLog.query.filter_by(
                    job_id="enable_1_mon_0900").one()
                assert logged.status == "success"
                # Sans log_job_id (job persisté hérité) : ancien id agrégé.
                scheduler_functions.enable_buttons_for_activity_job(activity.id)
                assert JobExecutionLog.query.filter_by(
                    job_id=f"Enable_Buttons_Activity_{activity.id}").one()
    finally:
        AppHolder.set_app(None)


def test_local_time_interprets_stored_utc(app):
    """``execution_time`` est stocké naïf en UTC ; ``local_time`` doit le
    convertir en heure de Paris quel que soit le fuseau de l'hôte."""
    with app.app_context():
        log = _log("T", "success", when=datetime(2024, 7, 1, 12, 0, 0))
        # 12:00 UTC en été -> 14:00 Europe/Paris (CEST), indépendant du TZ hôte.
        assert log.local_time.hour == 14
        assert log.local_time.tzinfo is not None


# ---------------------------------------------------------------------------
# Gardes statiques : route + gabarit
# ---------------------------------------------------------------------------

def _route_body(source, route):
    m = re.search(r"def " + route + r"\(.*?\n(.*?)(?=\ndef |\n@admin_config_bp)",
                  source, re.DOTALL)
    assert m, f"fonction {route} introuvable"
    return m.group(1)


def test_route_delegates_to_grouped_assembler():
    body = _route_body(_read("routes/admin_config.py"), "display_schedule_tasks_list")
    assert "build_jobs_info(" in body
    assert "per_job=5" in body and "detailed=True" in body
    # Plus de requête par tâche dans une boucle (N+1).
    assert "JobExecutionLog.query" not in body
    assert "for job in jobs" not in body


def test_template_shows_friendly_fields_not_internals():
    tpl = _read("templates/admin/database_schedule_tasks_list.html")
    # Champs lisibles fournis par build_jobs_info(detailed=True).
    for field in ("job.label", "job.schedule", "exec.status_label",
                  "exec.status_class"):
        assert field in tpl
    # Les internes APScheduler n'apparaissent plus.
    for internal in ("function_name", "coalesce", "max_instances",
                     "misfire_grace_time", "job.trigger"):
        assert internal not in tpl
    # Le statut n'est plus coloré « non-succès = rouge » : la classe vient du
    # serveur (skipped -> avertissement).
    assert "text-red-600" not in tpl
    # L'heure de la prochaine exécution porte son fuseau.
    assert "%Z" in tpl


def test_activity_jobs_pass_log_job_id():
    src = _read("routes/admin_activity.py")
    # Les deux appels add_job passent l'identifiant du job en 2e argument.
    assert "args=[activity.id, enable_id]" in src
    assert "args=[activity.id, disable_id]" in src
