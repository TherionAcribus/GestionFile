"""Point 5.3 — Tableau de bord : chargement différé et fin des N+1 scheduler.

Deux volets, testés ici :

1. **Front (statique)** — chaque carte du tableau de bord ne déclenche sa
   requête que lorsqu'elle entre dans la fenêtre (`revealed`), plus au
   chargement global (`load`) ; en attendant, un squelette occupe la place et un
   état d'erreur par carte est prévu.

2. **Back (statique + unitaire pur)** — la carte « Planifications » n'émet plus
   une requête SQL par tâche pour sa dernière exécution : la logique est
   centralisée dans ``scheduler_dashboard`` (une seule requête groupée), utilisée
   à la fois par la route dédiée et par la reconstruction des cartes.

Aucune base MySQL n'est requise : on lit le source des gabarits/routes et on
teste l'assemblage pur de ``build_jobs_info`` avec des jobs factices.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402

import scheduler_dashboard  # noqa: E402

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_LOAD_CARDS = [
    "queue", "counter", "printer", "staff", "button",
    "player", "connection", "security", "appschedule", "alerts",
]


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Front : chargement différé « à la révélation » (plus au chargement global)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", _LOAD_CARDS)
def test_load_templates_use_card_slot_macro(name):
    src = _read(f"templates/admin/dashboard_load_{name}.html")
    assert "import card_slot" in src, f"{name} devrait passer par le macro card_slot"
    assert "card_slot(dashboardcard" in src


@pytest.mark.parametrize("name", _LOAD_CARDS)
def test_load_templates_no_longer_trigger_on_load(name):
    """Plus de `hx-trigger="load"` en dur : le déclencheur `revealed` vient du
    macro, sinon les cartes hors écran émettraient une requête au chargement."""
    src = _read(f"templates/admin/dashboard_load_{name}.html")
    assert 'hx-trigger="load' not in src


def test_macro_defers_until_revealed_and_shows_skeleton():
    src = _read("templates/admin/_dashboard_cards.html")
    # Déclencheur d'intersection, une seule fois par carte.
    assert "revealed once" in src
    # Squelette d'attente (accessibilité : aria-busy) + barres animables.
    assert "card-skeleton" in src
    assert 'aria-busy="true"' in src
    assert "skeleton-bar" in src
    # URL mémorisée pour permettre un « Réessayer » sans rechargement de page.
    assert "data-card-url" in src


def test_admin_page_has_per_card_error_state():
    src = _read("templates/admin/admin.html")
    # Un échec de requête d'une carte bascule sur un état d'erreur ciblé…
    assert "htmx:responseError" in src
    assert "htmx:sendError" in src
    assert "dashboardCardError" in src
    # …avec un bouton « Réessayer » qui relance uniquement cette carte.
    assert "dashboardCardRetry" in src


def test_skeleton_and_error_styles_exist():
    css = _read("static/css/admin.css")
    assert ".card-skeleton" in css
    assert "skeleton-shimmer" in css
    assert ".card-load-error" in css


# --------------------------------------------------------------------------
# Back : fin du N+1 scheduler (une requête au lieu d'une par tâche)
# --------------------------------------------------------------------------

def _appschedule_route_body():
    """Corps de la route ``/admin/appschedule/dashboard`` (repérée par sa route,
    le nom de fonction étant ambigu dans app.py)."""
    src = _read("app.py")
    start = src.index("@app.route('/admin/appschedule/dashboard')")
    return src[start:start + 1500]


def test_appschedule_dashboard_uses_single_query_assembler():
    body = _appschedule_route_body()
    assert "build_jobs_info(" in body, "la route devrait déléguer à build_jobs_info"
    # Plus de requête par tâche dans une boucle.
    assert "JobExecutionLog.query.filter_by" not in body


def test_save_configuration_appschedule_uses_assembler_not_ghost_model():
    src = _read("routes/admin_dashboard.py")
    assert "build_jobs_info(" in src
    # SchedulerLog n'existe pas dans models : l'ancienne branche était cassée.
    assert "SchedulerLog" not in src


# --------------------------------------------------------------------------
# Assemblage pur de build_jobs_info (jobs factices, sans base)
# --------------------------------------------------------------------------

class _FakeJob:
    def __init__(self, job_id, next_run_time="10:00"):
        self.id = job_id
        self.next_run_time = next_run_time


class _FakeLog:
    def __init__(self, execution_time="t", status="success", error_message=None):
        self.execution_time = execution_time
        self.status = status
        self.error_message = error_message


def test_build_jobs_info_no_jobs_makes_no_query():
    # Liste vide -> pas de requête (latest_execution_by_job court-circuite).
    assert scheduler_dashboard.build_jobs_info([]) == ([], [])
    assert scheduler_dashboard.latest_execution_by_job([]) == {}


def test_build_jobs_info_splits_main_and_other(monkeypatch):
    jobs = [
        _FakeJob("Clear Patient Table"),
        _FakeJob("Some Other Job"),
    ]
    log = _FakeLog(status="failed", error_message="boom")
    # Une seule « requête » simulée renvoyant les dernières exécutions.
    monkeypatch.setattr(
        scheduler_dashboard,
        "latest_execution_by_job",
        lambda ids: {"Clear Patient Table": log},
    )

    main, other = scheduler_dashboard.build_jobs_info(jobs)

    assert [j["id"] for j in main] == ["Clear Patient Table"]
    assert [j["id"] for j in other] == ["Some Other Job"]
    # La tâche principale porte sa dernière exécution ; l'autre n'en a pas.
    assert main[0]["last_execution"] == {"time": "t", "status": "failed", "error": "boom"}
    assert other[0]["last_execution"] is None
