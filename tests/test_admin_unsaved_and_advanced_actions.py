"""Point 6 (audit Admin) — Protéger le travail en cours et les actions sensibles.

Deux améliorations :

1. **Détection des modifications non enregistrées** : un nouveau module
   ``admin_unsaved_changes.js`` suit en temps réel s'il existe des champs
   modifiés non encore enregistrés (bouton « Enregistrer » actif). Il avertit
   l'utilisateur avant de quitter la page (beforeunload) UNIQUEMENT s'il y a
   des changements en attente — pas inconditionnellement. Un badge discret
   dans la sidebar indique l'état.

2. **Actions avancées** : les boutons de suppression massive de patients
   (``queue.html``) sont déplacés dans une zone repliable « Actions avancées »,
   séparée visuellement (card border-danger), avec un avertissement explicite.
   Avant, ils étaient directement dans le parcours quotidien.

Vérifications statiques (on lit le source, comme les autres tests de
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
# 1. admin_unsaved_changes.js : détection beforeunload conditionnelle
# ---------------------------------------------------------------------------

def test_unsaved_changes_js_exists():
    """Le module de détection des modifications non enregistrées doit exister."""
    import os
    path = os.path.join(_SERVEUR, "static", "js", "admin_unsaved_changes.js")
    assert os.path.isfile(path), "static/js/admin_unsaved_changes.js doit exister"


def test_unsaved_changes_js_has_beforeunload():
    """Le module doit écouter beforeunload pour avertir avant de quitter."""
    js = _read("static/js/admin_unsaved_changes.js")
    assert "beforeunload" in js


def test_unsaved_changes_js_is_conditional():
    """L'avertissement beforeunload ne doit se déclencher QUE s'il y a des
    modifications non enregistrées (pas inconditionnellement)."""
    js = _read("static/js/admin_unsaved_changes.js")
    # Doit vérifier hasUnsavedChanges avant de déclencher l'avertissement.
    assert "hasUnsavedChanges" in js
    # Le beforeunload ne doit pas appeler event.preventDefault()
    # inconditionnellement.
    m = re.search(r"window\.addEventListener\('beforeunload'.*?\}\);",
                  js, re.DOTALL)
    assert m, "listener beforeunload introuvable"
    body = m.group(0)
    assert "if (hasUnsavedChanges)" in body or "if(hasUnsavedChanges)" in body


def test_unsaved_changes_js_scans_buttons():
    """Le module doit détecter les boutons « Enregistrer » actifs comme
    indicateur de modifications en cours."""
    js = _read("static/js/admin_unsaved_changes.js")
    assert 'button[id$="_button"]' in js or 'id$="_button"' in js
    assert "disabled" in js
    assert "Enregistrer" in js


def test_unsaved_changes_js_updates_badge():
    """Le module doit mettre à jour un badge visuel (#unsaved-changes-badge)."""
    js = _read("static/js/admin_unsaved_changes.js")
    assert "unsaved-changes-badge" in js
    assert "updateBadge" in js


# ---------------------------------------------------------------------------
# 2. base.html : script chargé + badge présent
# ---------------------------------------------------------------------------

def test_base_html_loads_unsaved_changes_js():
    src = _read("templates/admin/base.html")
    assert "admin_unsaved_changes.js" in src


def test_base_html_has_unsaved_changes_badge():
    src = _read("templates/admin/base.html")
    assert 'id="unsaved-changes-badge"' in src


# ---------------------------------------------------------------------------
# 3. queue.html : boutons de suppression dans une zone « Actions avancées »
# ---------------------------------------------------------------------------

def test_queue_html_has_advanced_actions_section():
    """Les boutons de suppression massive doivent être dans une zone repliable
    « Actions avancées », pas directement dans le parcours quotidien."""
    src = _read("templates/admin/queue.html")
    assert "Actions avancées" in src
    assert 'id="advancedActions"' in src
    assert 'data-bs-toggle="collapse"' in src


def test_queue_html_advanced_actions_has_warning():
    """La zone Actions avancées doit contenir un avertissement explicite."""
    src = _read("templates/admin/queue.html")
    # Trouver le bloc advancedActions
    m = re.search(r'id="advancedActions".*?</div>\s*</div>', src, re.DOTALL)
    assert m, "zone advancedActions introuvable"
    body = m.group(0)
    assert "irréversible" in body or "Attention" in body
    assert "alert-warning" in body


def test_queue_html_delete_buttons_inside_advanced_actions():
    """Les deux boutons de suppression doivent être DANS la zone
    advancedActions, pas en dehors."""
    src = _read("templates/admin/queue.html")
    # Position de la zone advancedActions
    zone_start = src.find('id="advancedActions"')
    zone_end = src.find('</div>\n    </div>', zone_start)
    assert zone_start != -1, "zone advancedActions introuvable"
    # Les boutons doivent être après le début de la zone.
    delete_with = src.find('confirm_delete_patient_table_with_saving')
    delete_without = src.find('confirm_delete_patient_table_without_saving')
    assert delete_with > zone_start, (
        "Le bouton 'avec sauvegarde' doit être dans la zone Actions avancées"
    )
    assert delete_without > zone_start, (
        "Le bouton 'sans sauvegarde' doit être dans la zone Actions avancées"
    )


def test_queue_html_advanced_actions_is_collapse():
    """La zone doit être repliée par défaut (classe collapse, pas show)."""
    src = _read("templates/admin/queue.html")
    m = re.search(r'id="advancedActions"[^>]*class="([^"]*)"', src)
    assert m, "classe de advancedActions introuvable"
    classes = m.group(1)
    assert "collapse" in classes
    assert "show" not in classes, (
        "La zone Actions avancées ne doit pas être ouverte par défaut"
    )


def test_queue_html_advanced_actions_has_border_danger():
    """La zone doit être visuellement distincte (border-danger)."""
    src = _read("templates/admin/queue.html")
    # La card contenant la zone doit avoir border-danger.
    m = re.search(r'<div class="card border-danger.*?id="advancedActions"', src, re.DOTALL)
    assert m, "La zone Actions avancées doit être dans une card border-danger"
