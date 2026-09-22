# Point « CSP stricte » — test navigateur : toute violation CSP fait échouer.
#
# Test de bout en bout Playwright : exige un serveur déjà démarré, un
# navigateur installé (`playwright install chromium`) et MySQL. Marqué `e2e`
# -> exclu de la suite par défaut (voir pytest.ini). Lancer : pytest -m e2e
#
# La CSP impose `script-src 'self'` sans 'unsafe-inline' ni 'unsafe-eval'.
# Ce test écoute la console du navigateur : Chromium y rapporte chaque
# violation (« Refused to execute inline event handler … »,
# « Evaluating a string as JavaScript violates … »). On navigue sur les
# pages admin et on exerce les interactions HTMX (recherche, tri,
# pagination, interrupteurs) puis on échoue si la moindre violation a été
# rapportée.
#
# Prérequis identiques à test_admin_e2e_journeys.py :
#   - Serveur démarré sur http://127.0.0.1:5000
#   - MySQL configuré, compte admin/admin avec toutes les permissions
#   - Playwright : pip install playwright && playwright install chromium
#
# Lancer : pytest -m e2e tests/test_admin_csp_e2e.py

import os

import pytest

pytestmark = pytest.mark.e2e

pytest.importorskip('playwright.sync_api', reason='playwright non installé')

from playwright.sync_api import Page

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")

# Pages admin à couvrir : chacune utilise htmx et charge les scripts
# externalisés. Une violation sur l'une d'elles fait échouer le test.
PAGES_ADMIN = [
    "/admin",
    "/admin/queue",
    "/admin/data",
    "/admin/security",
    "/admin/staff",
    "/admin/counter",
    "/admin/patient",
    "/admin/app",
    "/admin/activity",
    "/admin/algo",
    "/admin/announce",
    "/admin/music",
    "/admin/stats",
    "/admin/stats/history",
    "/admin/translations",
    "/admin/phone",
    "/admin/database",
    "/admin/admin_options",
    "/admin/info",
]


def _est_violation_csp(texte):
    """Repère les messages de console émis par le navigateur pour une
    violation CSP (Chromium : « …Content Security Policy… », Firefox :
    « Content-Security-Policy: … »)."""
    t = texte.lower()
    return "content security policy" in t or "content-security-policy" in t


@pytest.fixture(scope="session")
def browser(playwright):
    browser = playwright.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture(scope="session")
def admin_page(browser):
    page = browser.new_page()
    page.goto(f"{BASE_URL}/admin")
    page.wait_for_selector('input[name="username"]')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin')
    page.click('input[type="submit"][value="Login"]')
    page.wait_for_url(f"{BASE_URL}/admin")
    yield page
    page.close()


@pytest.fixture()
def violations(admin_page):
    """Collecte les violations CSP (console) et erreurs JS de la page."""
    trouvees = []

    def on_console(msg):
        if _est_violation_csp(msg.text):
            trouvees.append(f"[console:{msg.type}] {msg.text}")

    def on_pageerror(exc):
        texte = str(exc)
        # eval() / Function() bloqués par la CSP lèvent une EvalError dont
        # le message mentionne aussi la directive.
        if _est_violation_csp(texte) or "unsafe-eval" in texte:
            trouvees.append(f"[pageerror] {texte}")

    admin_page.on("console", on_console)
    admin_page.on("pageerror", on_pageerror)
    yield trouvees
    admin_page.remove_listener("console", on_console)
    admin_page.remove_listener("pageerror", on_pageerror)


# ---------------------------------------------------------------------------
# Balayage des pages admin
# ---------------------------------------------------------------------------

class TestCSPPagesAdmin:
    """Aucune violation CSP au chargement des pages admin."""

    @pytest.mark.parametrize("chemin", PAGES_ADMIN)
    def test_page_sans_violation_csp(self, admin_page: Page, violations, chemin):
        admin_page.goto(f"{BASE_URL}{chemin}")
        admin_page.wait_for_load_state("networkidle")
        assert not violations, (
            f"Violations CSP sur {chemin} :\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Interactions HTMX : recherche / tri / pagination / interrupteurs
# ---------------------------------------------------------------------------

class TestCSPInteractions:
    """Les interactions qui utilisaient hx-vals js:/hx-on/onclick ne doivent
    produire aucune violation CSP — et toujours fonctionner."""

    def test_recherche_queue(self, admin_page: Page, violations):
        """La recherche de la file passe désormais par data-param-*, pas par
        hx-vals js: — la requête doit partir sans erreur CSP."""
        admin_page.goto(f"{BASE_URL}/admin/queue")
        admin_page.wait_for_load_state("networkidle")
        search = admin_page.locator('#queue-search')
        if search.count() == 0:
            pytest.skip("Barre de recherche absente de cette version")
        search.fill("e2e")
        # Laisser le debounce keyup déclencher la requête HTMX.
        admin_page.wait_for_timeout(600)
        assert not violations, (
            "Violations CSP lors de la recherche :\n" + "\n".join(violations)
        )

    def test_tri_queue(self, admin_page: Page, violations):
        """Le clic sur un en-tête triable met à jour les champs cachés via
        l'écouteur délégué et envoie la requête — sans onclick inline."""
        admin_page.goto(f"{BASE_URL}/admin/queue")
        admin_page.wait_for_load_state("networkidle")
        th = admin_page.locator('th.sortable[data-sort-key]')
        if th.count() == 0:
            pytest.skip("Aucun en-tête triable sur cette page")
        th.first.click()
        admin_page.wait_for_timeout(500)
        # Le champ caché de tri doit refléter le clic.
        sort_field = admin_page.locator('#queue-sort')
        if sort_field.count():
            assert sort_field.input_value() == th.first.get_attribute('data-sort-key')
        assert not violations, (
            "Violations CSP lors du tri :\n" + "\n".join(violations)
        )

    def test_per_page_queue(self, admin_page: Page, violations):
        """Le sélecteur « Par page » déclenche une requête avec les bons
        paramètres — sans hx-vals js:."""
        admin_page.goto(f"{BASE_URL}/admin/queue")
        admin_page.wait_for_load_state("networkidle")
        sel = admin_page.locator('#queue-per_page')
        if sel.count() == 0:
            pytest.skip("Sélecteur per_page absent")
        sel.select_option("10")
        admin_page.wait_for_timeout(500)
        assert not violations, (
            "Violations CSP lors du changement de page size :\n"
            + "\n".join(violations)
        )

    def test_sauvegarde_config_patient(self, admin_page: Page, violations):
        """Un bouton « Enregistrer » de bloc simple doit fonctionner sans
        hx-on:: ni hx-vals js: (page Patient)."""
        admin_page.goto(f"{BASE_URL}/admin/patient")
        admin_page.wait_for_load_state("networkidle")
        button = admin_page.locator('#page_patient_title_button')
        if button.count() == 0:
            pytest.skip("Bouton de sauvegarde absent de cette page")
        button.click()
        admin_page.wait_for_timeout(500)
        assert not violations, (
            "Violations CSP lors de la sauvegarde :\n" + "\n".join(violations)
        )

    def test_htmx_eval_disallowed_arme(self, admin_page: Page):
        """Défense en profondeur : htmx.config.allowEval doit être false —
        un hx-vals js:/hx-on résiduel échouerait sans violer la CSP."""
        admin_page.goto(f"{BASE_URL}/admin")
        admin_page.wait_for_load_state("networkidle")
        allow_eval = admin_page.evaluate("window.htmx && htmx.config && htmx.config.allowEval")
        assert allow_eval is False, (
            "htmx.config.allowEval devrait être false (htmx_params.js)"
        )
