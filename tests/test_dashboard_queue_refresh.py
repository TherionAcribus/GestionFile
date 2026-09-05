"""Point 3 (audit Admin) — Carte « Patients » du dashboard : rafraîchissement temps réel.

Trois bugs corrigés dans ``static/js/admin.js`` :

1. **L'abonnement à ``/socket_update_patient`` n'était actif que sur la page
   ``/admin/queue``** (gated sur ``#div_queue_table``), pas sur le dashboard.
   La carte « Patients » du dashboard ne recevait donc jamais les mises à jour
   temps réel de la file.

2. **Les fonctions ``refresh_*_dashboard()`` cherchaient des ids fixes**
   (``#card-queue``, ``#card-printer``, ``#card-counter``) qui n'existent pas :
   les cartes du dashboard ont un id numérique (``card-{{ dashboardcard.id }}``).
   ``htmx.trigger`` avec un sélecteur sans match ne fait rien — silencieusement.

3. **``console.log`` de debug** laissé dans ``refresh_queue()``.

Fix : on utilise ``data-card-url`` (présent dès le HTML initial sur le slot de
la carte, avant son chargement différé) comme sélecteur stable pour retrouver
chaque carte. L'abonnement à ``/socket_update_patient`` est désormais activé
aussi quand la carte queue du dashboard est présente.

Vérifications statiques (on lit le source du JS, comme les autres tests de
régression statique de ce dépôt).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Abonnement /socket_update_patient actif sur le dashboard
# ---------------------------------------------------------------------------

def test_queue_subscription_includes_dashboard():
    """L'abonnement à /socket_update_patient doit aussi s'activer quand la
    carte queue du dashboard est présente (data-card-url), pas seulement sur
    la page /admin/queue (#div_queue_table)."""
    js = _read("static/js/admin.js")
    # Le sélecteur data-card-url doit être présent dans le bloc d'abonnement.
    assert 'data-card-url="/admin/queue/dashboard"' in js
    # L'ancien code ne doit plus se limiter à #div_queue_table seul.
    # On vérifie que les deux sélecteurs sont présents (page + dashboard).
    assert '#div_queue_table' in js
    assert 'queueOnDashboard' in js


def test_queue_reconnect_rattrapage_exists():
    """Le rattrapage de reconnexion pour la file doit être présent."""
    js = _read("static/js/admin.js")
    assert 'onReconnect(NS_QUEUE' in js


# ---------------------------------------------------------------------------
# 2. Les fonctions refresh_*_dashboard utilisent data-card-url (pas d'id fixe)
# ---------------------------------------------------------------------------

def test_refresh_queue_uses_data_card_url():
    """refresh_queue() doit trouver la carte dashboard via data-card-url, pas
    via #card-queue (qui n'existe jamais — l'id est numérique)."""
    js = _read("static/js/admin.js")
    m = re.search(r"function refresh_queue\(\)\s*{(.*?)\n}", js, re.DOTALL)
    assert m, "refresh_queue() introuvable"
    body = m.group(1)
    assert 'data-card-url="/admin/queue/dashboard"' in body
    # #card-queue ne doit plus être utilisé comme sélecteur actif (querySelector
    # ou htmx.trigger). Un commentaire mentionnant l'ancien sélecteur est OK.
    assert "querySelector('#card-queue')" not in body
    assert "htmx.trigger('#card-queue'" not in body


def test_refresh_printer_dashboard_uses_data_card_url():
    js = _read("static/js/admin.js")
    m = re.search(r"function refresh_printer_dashboard\(\)\s*{(.*?)\n}", js, re.DOTALL)
    assert m, "refresh_printer_dashboard() introuvable"
    body = m.group(1)
    assert 'data-card-url="/admin/printer/dashboard"' in body
    assert '#card-printer' not in body


def test_refresh_counter_dashboard_uses_data_card_url():
    js = _read("static/js/admin.js")
    m = re.search(r"function refresh_counter_dashboard\(\)\s*{(.*?)\n}", js, re.DOTALL)
    assert m, "refresh_counter_dashboard() introuvable"
    body = m.group(1)
    assert 'data-card-url="/admin/counter/dashboard"' in body
    assert '#card-counter' not in body


# ---------------------------------------------------------------------------
# 3. Plus de console.log de debug dans refresh_queue
# ---------------------------------------------------------------------------

def test_refresh_queue_has_no_console_log():
    js = _read("static/js/admin.js")
    m = re.search(r"function refresh_queue\(\)\s*{(.*?)\n}", js, re.DOTALL)
    assert m, "refresh_queue() introuvable"
    body = m.group(1)
    assert 'console.log' not in body, (
        "refresh_queue() ne doit plus contenir de console.log de debug "
        "(il émettait 'card_queue null' sur le dashboard à chaque update)."
    )


# ---------------------------------------------------------------------------
# 4. Les templates dashboard_load_*.html passent le bon trigger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,url,trigger", [
    ("queue", "/admin/queue/dashboard", "refresh_queue_patient"),
    ("printer", "/admin/printer/dashboard", "refresh_printer_dashboard"),
    ("counter", "/admin/counter/dashboard", "refresh_counter_dashboard"),
])
def test_dashboard_load_template_passes_trigger(name, url, trigger):
    """Chaque template dashboard_load_*.html doit passer son trigger HTMX
    au macro card_slot, pour que l'évènement de rafraîchissement déclenche
    la bonne carte."""
    src = _read(f"templates/admin/dashboard_load_{name}.html")
    assert f"'{trigger}'" in src
    assert url in src


# ---------------------------------------------------------------------------
# 5. Le macro card_slot porte data-card-url (nécessaire pour le sélecteur JS)
# ---------------------------------------------------------------------------

def test_card_slot_macro_has_data_card_url():
    src = _read("templates/admin/_dashboard_cards.html")
    assert 'data-card-url="{{ url }}"' in src
