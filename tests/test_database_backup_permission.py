"""Point 1 (audit Admin) — Permission requise pour les sauvegardes brutes.

Les routes ``/admin/database/backup`` et ``/admin/database/restore`` permettent
respectivement de télécharger l'intégralité des bases de données et de les
écraser à partir d'un fichier uploadé. Historiquement, elles n'étaient protégées
que par l'authentification globale ``/admin`` (``require_login_for_admin``) :
n'importe quel utilisateur connecté — sans la permission ``app`` — pouvait les
appeler.

Ces routes sont déclarées dans ``app.py`` via ``app.add_url_rule`` (et non dans
un blueprint admin), donc le test statique de couverture
``test_permissions_centralized`` ne les couvre pas : il ne scanne que
``routes/admin_*.py``. On vérifie donc ici, statiquement, qu'elles sont bien
enveloppées par ``require_permission('app')`` au moment de leur enregistrement.

``app.py`` exige MySQL et n'est pas importable ici : on lit le source, comme les
autres tests de régression statique de ce dépôt (``test_post_only_routes``,
``test_config_change_feedback``).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _find_url_rule(source, endpoint):
    """Retourne le bloc ``app.add_url_rule(..., 'endpoint', view, ...)`` complet.

    On capture sur plusieurs lignes pour retrouver le view function passé en
    argument, même si l'appel est étalé sur plusieurs lignes.
    """
    pattern = re.compile(
        r"app\.add_url_rule\([^)]*?\b" + re.escape(endpoint) + r"\b[^)]*?\)",
        re.DOTALL,
    )
    m = pattern.search(source)
    return m.group(0) if m else None


@pytest.mark.parametrize("endpoint", ["backup_databases", "restore_databases"])
def test_database_route_is_wrapped_with_app_permission(endpoint):
    source = _read("app.py")
    block = _find_url_rule(source, endpoint)
    assert block is not None, (
        f"app.add_url_rule pour '{endpoint}' introuvable dans app.py — "
        "la route a-t-elle été déplacée ou renommée ?"
    )
    assert "require_permission('app')" in block, (
        f"La route '{endpoint}' doit être enveloppée par require_permission('app') "
        "au moment de son enregistrement (point 1 — audit Admin). Sans cette garde, "
        "tout utilisateur authentifié peut télécharger ou écraser la base."
    )


def test_require_permission_is_imported_in_app_py():
    """Sans l'import, le wrapping ne pourrait pas être référencé."""
    source = _read("app.py")
    assert re.search(
        r"from\s+routes\.admin_security\s+import\s+.*\brequire_permission\b",
        source,
    ), "require_permission doit être importé depuis routes.admin_security dans app.py"
