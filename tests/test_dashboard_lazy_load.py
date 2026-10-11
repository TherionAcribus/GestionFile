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

import pytest

import scheduler_dashboard
from dashboard_catalog import CATALOG

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Front : chargement différé « à la révélation » (plus au chargement global)
# --------------------------------------------------------------------------

def test_catalogue_declare_route_et_declencheurs():
    """L'URL de contenu et les déclencheurs temps réel de chaque carte sont
    déclarés dans le catalogue (anciennement en dur dans les 11 gabarits
    dashboard_load_*.html, supprimés)."""
    attendu = {
        "today": ("/admin/today/dashboard",
                  ("refresh_queue_patient", "refresh_counter_dashboard")),
        "alerts": ("/admin/alerts/dashboard", ()),
        "security": ("/admin/security/dashboard", ()),
        "queue": ("/admin/queue/dashboard", ("refresh_queue_patient",)),
        "counter": ("/admin/counter/dashboard", ("refresh_counter_dashboard",)),
        "staff": ("/admin/staff/dashboard", ("refresh_counter_dashboard",)),
        "button": ("/admin/button/dashboard", ()),
        "printer": ("/admin/printer/dashboard", ("refresh_printer_dashboard",)),
        "player": ("/admin/music/dashboard", ()),
        "connection": ("/admin/communication/dashboard", ()),
        "appschedule": ("/admin/appschedule/dashboard", ()),
    }
    assert set(CATALOG) == set(attendu)
    for name, info in CATALOG.items():
        assert info.data_url.startswith("/admin/")
        assert info.data_url.endswith("/dashboard")
        assert isinstance(info.triggers, tuple)
        assert (info.data_url, info.triggers) == attendu[name]


def test_slot_template_uses_card_slot_macro():
    src = _read("templates/admin/dashboard_slot.html")
    assert "import card_slot" in src
    assert "card_slot(dashboardcard)" in src
    # Plus de `hx-trigger="load"` en dur : le déclencheur `revealed` vient du
    # macro, sinon les cartes hors écran émettraient une requête au chargement.
    assert 'hx-trigger="load' not in _read("templates/admin/_dashboard_cards.html")


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


def test_admin_page_has_shared_skeleton_template():
    """Le squelette de carte n'existe qu'une fois (macro ``skeleton``) : la
    page le rend dans un <template> que le JS clone pour les « Réessayer »."""
    html = _read("templates/admin/admin.html")
    assert 'id="dashboard-skeleton"' in html
    assert "import skeleton" in html
    js = _read("static/js/admin_home.js")
    assert "dashboard-skeleton" in js
    assert "skeleton-bar skeleton-title" not in js   # plus de HTML en dur


def test_dashboard_js_no_alert():
    """Les erreurs passent par AdminFeedback (toast + Réessayer), plus par
    des alert() bloquantes."""
    for rel in ("static/js/admin_home.js", "static/js/admin_dashboard_select.js"):
        assert "alert(" not in _read(rel)


def test_dashboard_realtime_refresh_wiring():
    """Propagation temps réel : l'évènement refresh_dashboard recharge les
    enveloppes via /admin/dashboard/cards, et le gestionnaire protège les
    modifications non enregistrées (data-dirty / badge)."""
    admin_js = _read("static/js/admin.js")
    assert "'refresh_dashboard'" in admin_js
    assert "/admin/dashboard/cards" in _read("static/js/admin_home.js")
    assert "data-dirty" in _read("static/js/admin_dashboard_select.js")
    assert "data-card-manager-dirty" in _read("templates/admin/dashboard_select.html")


def test_settings_realtime_wiring():
    """Réglages par carte : l'évènement refresh_dashboard_card recharge la
    seule carte concernée, repérée par data-card-name sur le slot."""
    assert "'refresh_dashboard_card'" in _read("static/js/admin.js")
    assert "data-card-name" in _read("static/js/admin_home.js")
    assert "data-card-name=" in _read("templates/admin/_dashboard_cards.html")


def test_card_shell_has_refresh_and_timestamp():
    """Chaque carte chargée affiche un bouton « Actualiser » et un horodatage
    « mis à jour il y a… » ; le polling des cartes sans évènement vient du
    catalogue (poll_seconds) — plus de setInterval dédié dans admin.js."""
    assert "data-card-refresh" in _read("templates/admin/_dashboard_cards.html")
    assert "data-card-updated" in _read("templates/admin/_dashboard_cards.html")
    js = _read("static/js/admin_home.js")
    assert "reloadDashboardSlot" in js
    assert "updateCardTimestamps" in js
    assert "data-updated-at" in js
    admin_js = _read("static/js/admin.js")
    assert admin_js.count("refresh_printer_dashboard()") == 2   # handler + onReconnect


def test_empty_card_response_hides_slot():
    """Carte « conditionnelle » (ex. Alertes sans alerte) : une réponse vide
    masque l'enveloppe dans la grille (sinon un emplacement vide subsiste) ;
    elle réapparaît si un contenu arrive ensuite (polling/évènement)."""
    js = _read("static/js/admin_home.js")
    assert "slot.hidden" in js


def test_manager_keyboard_and_reset():
    """Gestionnaire : déplacement au clavier (flèches) et bouton
    « Réinitialiser » confirmé, câblés côté JS."""
    html = _read("templates/admin/dashboard_select.html")
    assert 'data-card-move="up"' in html
    assert 'data-card-move="down"' in html
    assert "data-card-manager-reset" in html
    assert "hx-confirm" in html
    js = _read("static/js/admin_dashboard_select.js")
    assert "data-card-move" in js
    assert "data-card-manager-reset" in js


def test_manager_scope_wiring():
    """Le gestionnaire distingue disposition personnelle (me) et commune
    (all) : sélecteur d'étendue, bouton de retour à la commune et URL de
    sauvegarde dédiée."""
    html = _read("templates/admin/dashboard_select.html")
    assert "data-scope=" in html
    assert "scope=me" in html
    assert "/admin/dashboard/me/reset" in html
    js = _read("static/js/admin_dashboard_select.js")
    assert "/admin/dashboard/me/save" in js
    assert "display_select?scope=" in js


def test_admin_page_has_per_card_error_state():
    # Le script de la page a ete extrait vers static/js/admin_home.js (point 2).
    src = _read("templates/admin/admin.html") + _read("static/js/admin_home.js")
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
    src = _read("routes/admin_config.py")
    start = src.index("@admin_config_bp.route('/admin/appschedule/dashboard')")
    return src[start:start + 1500]


def test_appschedule_dashboard_uses_single_query_assembler():
    body = _appschedule_route_body()
    assert "build_jobs_info(" in body, "la route devrait déléguer à build_jobs_info"
    # Plus de requête par tâche dans une boucle.
    assert "JobExecutionLog.query.filter_by" not in body


def test_save_configuration_returns_lazy_slots_not_rebuilt_cards():
    """L'enregistrement de la configuration renvoie les enveloppes à
    chargement différé : chaque carte passe par SA route (contenu et
    permission). La reconstruction à la main (logique dupliquée, dont une
    branche visait un modèle SchedulerLog inexistant) a disparu."""
    src = _read("routes/admin_dashboard.py")
    assert "_render_card_slots(" in src
    assert "dashboard_slot.html" in src
    assert "SchedulerLog" not in src
    assert "build_jobs_info(" not in src


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
