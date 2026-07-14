"""Point 15 — Corriger la restauration depuis l'onglet Sauvegardes.

Exécutable **sans MySQL ni serveur**. Couvre :

1. ``_render_restore_report`` : rapport section par section, ton fidèle au
   résultat (jamais « réussie » si seule une partie a été restaurée), bouton de
   rechargement présent seulement si au moins une section a été restaurée,
   échappement des libellés inconnus.
2. Gabarit de prévisualisation : le bouton « Restaurer » est un vrai bouton
   HTMX (hx-post vers /admin/backup/import) avec confirmation — il n'est plus
   orphelin d'un formulaire.
3. Le gabarit du formulaire d'import est unique (fichier + prévisualisation dans
   le même ``#importForm``), sans le formulaire mort d'antan.
"""

import os

import pytest
from flask import Flask

from routes.admin_backup import (
    _render_restore_report,
    _PREVIEW_TEMPLATE,
)


@pytest.fixture
def app_ctx():
    """Contexte d'application minimal (render_template_string en a besoin)."""
    app = Flask(__name__)
    with app.app_context():
        yield app


# ---------------------------------------------------------------------------
# 1. _render_restore_report
# ---------------------------------------------------------------------------

def test_report_all_restored_is_success(app_ctx):
    report = {"success": True, "restored": ["staff", "counters"], "errors": []}
    html = _render_restore_report(report, ["staff", "counters"])
    assert "alert-success" in html
    assert "Restauration réussie" in html
    assert "partiel" not in html.lower()
    # Deux sections marquées « Restaurée », aucune « Non restaurée ».
    assert html.count("Restaurée") == 2
    assert "Non restaurée" not in html
    # Bouton de rechargement proposé.
    assert "window.location.reload()" in html


def test_report_partial_is_warning_and_explicit(app_ctx):
    report = {"success": True, "restored": ["staff"], "errors": ["Section 'counters': boom"]}
    html = _render_restore_report(report, ["staff", "counters"])
    # JAMAIS « succès » quand seule une partie est restaurée : ton d'avertissement.
    assert "alert-success" not in html
    assert "alert-warning" in html
    assert "partielle" in html
    assert "1 section(s) restaurée(s)" in html
    assert "1 en échec" in html
    # Rapport section par section : une restaurée, une non restaurée.
    assert "Restaurée" in html
    assert "Non restaurée" in html
    # Le détail technique de l'erreur ne fuite pas vers le navigateur.
    assert "boom" not in html
    # Au moins une section restaurée → bouton de rechargement présent.
    assert "window.location.reload()" in html


def test_report_none_restored_is_danger_no_reload(app_ctx):
    report = {"success": False, "restored": [], "errors": ["Section 'staff': boom"]}
    html = _render_restore_report(report, ["staff", "counters"])
    assert "alert-danger" in html
    assert "aucune section" in html.lower()
    assert "alert-success" not in html
    # Rien n'a été restauré → pas de bouton de rechargement.
    assert "window.location.reload()" not in html
    assert html.count("Non restaurée") == 2


def test_report_escapes_unknown_labels(app_ctx):
    # Une clé inconnue (ex. altérée côté client) prend son propre nom comme
    # libellé ; l'auto-échappement Jinja empêche toute injection HTML.
    report = {"success": False, "restored": [], "errors": []}
    html = _render_restore_report(report, ["<script>alert(1)</script>"])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# 2. Gabarit de prévisualisation : bouton de restauration fonctionnel
# ---------------------------------------------------------------------------

def test_preview_button_is_functional_htmx(app_ctx):
    from flask import render_template_string
    info = {
        "app": "GestionFile",
        "format_version": "2.0",
        "timestamp": "2026-07-14 10:00:00",
        "sections": [{"key": "staff", "label": "Équipe", "count": 3}],
        "excluded_secrets": [],
    }
    html = render_template_string(_PREVIEW_TEMPLATE, info=info)
    # Le bouton est relié à la route de restauration (plus orphelin).
    assert 'hx-post="/admin/backup/import"' in html
    assert 'hx-target="#importResult"' in html
    # Confirmation demandée avant restauration.
    assert "hx-confirm" in html
    # Bouton non-submit : la requête est pilotée par HTMX.
    assert 'type="button"' in html
    # Les cases portent bien le nom attendu par la route.
    assert 'name="restore_sections"' in html
    assert 'value="staff"' in html


# ---------------------------------------------------------------------------
# 3. Gabarit d'import : un seul formulaire, plus de formulaire mort
# ---------------------------------------------------------------------------

def test_import_template_uses_single_form():
    path = os.path.join(
        os.path.dirname(__file__), "..", "templates", "admin", "app_backups.html"
    )
    with open(path, encoding="utf-8") as fh:
        tpl = fh.read()

    # Le formulaire d'import unique contient le champ fichier ET la zone de
    # prévisualisation ; le bouton « Restaurer » (chargé dedans) renvoie donc le
    # fichier et les cases cochées.
    assert 'id="importForm"' in tpl
    assert 'id="importFileInput"' in tpl
    assert 'id="importPreviewResult"' in tpl
    # L'ancien formulaire mort et son champ fichier caché ont disparu.
    assert "importPreviewForm" not in tpl
    assert "importFileHidden" not in tpl
    # Le bouton « Prévisualiser » ne soumet plus nativement (piloté par HTMX).
    assert 'hx-post="/admin/backup/preview"' in tpl
