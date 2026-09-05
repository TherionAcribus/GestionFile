"""Point 14 (audit Admin) — Accessibilité du drag-and-drop.

Le drag-and-drop (Sortable.js) sur les listes d'ordre (comptoirs, boutons,
langues) n'était pas accessible au clavier ni aux lecteurs d'écran :

1. Les items n'étaient pas focusables (pas de ``tabindex``) → impossible
   de naviguer au clavier dans la liste.
2. Les items n'avaient pas de ``role="listitem"`` ni la liste de
   ``role="list"`` → les lecteurs d'écran ne annonçaient pas la structure.
3. Pas de ``aria-posinset``/``aria-setsize`` → le lecteur ne pouvait pas
   annoncer la position de l'item dans la liste.
4. Pas de navigation par flèches → seul le glisser-déposer souris permettait
   de réordonner.

Corrections :

- **Templates** : ``role="list"`` sur le ``<ul>``, ``role="listitem"`` et
  ``tabindex="0"`` sur chaque ``<li>``.
- **admin.js** : navigation par flèches haut/bas + Home/End sur les items,
  ``aria-posinset``/``aria-setsize`` mis à jour dynamiquement après chaque
  réordonnancement (boutons, flèches, ou glisser-déposer).
- Les boutons « Monter »/« Descendre » existaient déjà et restent disponibles.

Vérifications statiques (lecture du source).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Templates : role=list, role=listitem, tabindex
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("template", [
    "templates/admin/counter_order_counters.html",
    "templates/admin/patient_page_order_buttons.html",
    "templates/admin/translations_languages_order.html",
])
def test_order_template_has_role_list(template):
    """Le <ul> doit avoir role='list'."""
    source = _read(template)
    assert 'role="list"' in source


@pytest.mark.parametrize("template", [
    "templates/admin/counter_order_counters.html",
    "templates/admin/patient_page_order_buttons.html",
    "templates/admin/translations_languages_order.html",
])
def test_order_template_has_role_listitem(template):
    """Chaque <li> doit avoir role='listitem'."""
    source = _read(template)
    assert 'role="listitem"' in source


@pytest.mark.parametrize("template", [
    "templates/admin/counter_order_counters.html",
    "templates/admin/patient_page_order_buttons.html",
    "templates/admin/translations_languages_order.html",
])
def test_order_template_has_tabindex(template):
    """Chaque <li> doit avoir tabindex='0' pour être focusable au clavier."""
    source = _read(template)
    assert 'tabindex="0"' in source


@pytest.mark.parametrize("template", [
    "templates/admin/counter_order_counters.html",
    "templates/admin/patient_page_order_buttons.html",
    "templates/admin/translations_languages_order.html",
])
def test_order_template_mentions_keyboard(template):
    """Le texte doit mentionner l'alternative clavier (flèches/boutons)."""
    source = _read(template)
    # Doit mentionner soit "flèches" soit "clavier" soit "Monter"/"Descendre".
    assert "flèches" in source.lower() or "clavier" in source.lower() or \
           "monter" in source.lower() or "descendre" in source.lower()


# ---------------------------------------------------------------------------
# 2. admin.js : navigation clavier + aria-posinset
# ---------------------------------------------------------------------------

def _extract_top_level_func(source, func_name):
    """Extrait une fonction top-level (colonne 0) du source JS."""
    pattern = rf"^function {func_name}\([^)]*\)\s*\{{(.*?)(?=^function |\Z)"
    m = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not m:
        pytest.fail(f"Fonction {func_name} introuvable")
    return m.group(1)


def test_admin_js_has_keyboard_navigation():
    """addKeyboardReorderControls doit gérer les flèches haut/bas."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    assert "ArrowUp" in body
    assert "ArrowDown" in body


def test_admin_js_has_home_end_navigation():
    """Home/End doivent focaliser le premier/dernier item."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    assert "Home" in body
    assert "End" in body


def test_admin_js_sets_aria_posinset():
    """Les items doivent recevoir aria-posinset et aria-setsize."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    assert "aria-posinset" in body
    assert "aria-setsize" in body


def test_admin_js_has_update_aria_positions():
    """Une fonction updateAriaPositions doit exister pour rafraîchir les
    positions après un réordonnancement."""
    source = _read("static/js/admin.js")
    assert "function updateAriaPositions" in source
    body = _extract_top_level_func(source, "updateAriaPositions")
    assert "aria-posinset" in body
    assert "aria-setsize" in body


def test_admin_js_sortable_onEnd_updates_aria():
    """Le callback onEnd de Sortable doit appeler updateAriaPositions."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "sortable")
    assert "updateAriaPositions" in body


def test_admin_js_keyboard_buttons_update_aria():
    """Les boutons Monter/Descendre doivent appeler updateAriaPositions."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    # updateAriaPositions doit être appelé dans les handlers de clic.
    assert "updateAriaPositions(listEl)" in body


def test_admin_js_keyboard_arrows_update_aria():
    """Les flèches clavier doivent appeler updateAriaPositions."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    # Vérifier que updateAriaPositions est appelé dans le handler keydown.
    keydown_m = re.search(r"addEventListener\('keydown'.*?}\);", body, re.DOTALL)
    assert keydown_m, "Handler keydown introuvable"
    assert "updateAriaPositions" in keydown_m.group(0)


# ---------------------------------------------------------------------------
# 3. Les boutons existants sont conservés
# ---------------------------------------------------------------------------

def test_admin_js_keeps_up_down_buttons():
    """Les boutons Monter/Descendre existants doivent être conservés."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    assert "bi-arrow-up" in body
    assert "bi-arrow-down" in body
    assert "aria-label" in body


def test_admin_js_buttons_have_aria_label():
    """Les boutons doivent avoir un aria-label descriptif."""
    source = _read("static/js/admin.js")
    body = _extract_top_level_func(source, "addKeyboardReorderControls")
    assert "Monter" in body
    assert "Descendre" in body
