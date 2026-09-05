"""Point 3 (audit Admin) — JSON mal formé et fuite d'information dans admin_config.

Trois problèmes corrigés dans ``routes/admin_config.py`` :

1. **``update_css_variable``** : ``json.loads(request.form.get('dependencies', '[]'))``
   pouvait lever ``JSONDecodeError`` si le client envoyait un JSON mal formé,
   produisant un HTTP 500 non géré. Désormais le JSON est validé dans un
   ``try/except`` et un 400 générique est renvoyé.

2. **``copy_colors``** : ``request.get_json()`` pouvait retourner ``None``
   (Content-Type non JSON), puis ``data.get(...)}`` levait ``AttributeError``
   → 500. Désormais ``get_json(silent=True)`` + vérification ``isinstance``.

3. **Fuite d'information** : ``update_css_variable_old`` et ``copy_colors``
   renvoyaient ``str(e)`` au client (détails techniques possibles :
   chemin de fichier, nom de table, etc.). Remplacé par un message générique,
   le détail étant journalisé côté serveur.

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


def _extract_func(source, func_name):
    """Extrait le corps d'une fonction depuis le source (regex)."""
    pattern = rf"def {func_name}\([^)]*\):(.*?)(?=^(?:def |@|\Z))"
    m = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not m:
        pytest.fail(f"Fonction {func_name} introuvable")
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. update_css_variable : json.loads sécurisé
# ---------------------------------------------------------------------------

def test_update_css_variable_wraps_json_loads():
    """json.loads doit être dans un try/except pour éviter un 500 sur JSON
    mal formé."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable")
    assert "json.loads" in body
    assert "try" in body
    assert "JSONDecodeError" in body or "except" in body


def test_update_css_variable_returns_400_on_bad_json():
    """En cas de JSON invalide, la route doit renvoyer 400 (pas 500)."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable")
    # Doit contenir un return avec statut 400 dans le bloc de gestion d'erreur.
    assert "400" in body


def test_update_css_variable_validates_dependencies_is_list():
    """Les dependencies doivent être validées comme étant une liste."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable")
    assert "isinstance" in body
    assert "list" in body


# ---------------------------------------------------------------------------
# 2. copy_colors : request.get_json sécurisé
# ---------------------------------------------------------------------------

def test_copy_colors_uses_silent_json():
    """copy_colors doit utiliser get_json(silent=True) pour éviter une
    exception si le Content-Type n'est pas JSON."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "copy_colors")
    assert "get_json" in body
    assert "silent=True" in body


def test_copy_colors_validates_dict_type():
    """copy_colors doit vérifier que data est un dict avant d'appeler .get()."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "copy_colors")
    assert "isinstance" in body
    assert "dict" in body


def test_copy_colors_returns_400_on_missing_json():
    """En cas de JSON manquant, la route doit renvoyer 400 (pas 500)."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "copy_colors")
    # Le premier return d'erreur doit être un 400.
    assert "400" in body


# ---------------------------------------------------------------------------
# 3. Pas de str(e) renvoyé au client
# ---------------------------------------------------------------------------

def _strip_comments(code):
    """Retire les commentaires # et docstrings pour ne tester que le code."""
    # Retirer les docstrings
    code = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
    # Retirer les commentaires en ligne
    lines = []
    for line in code.split('\n'):
        # Conserver le code avant un # (sauf si # est dans une string)
        # Approche simple : on retire tout après un # qui n'est pas entre quotes
        stripped = re.sub(r'#.*$', '', line)
        lines.append(stripped)
    return '\n'.join(lines)


def test_no_str_e_in_update_css_variable_old():
    """update_css_variable_old ne doit pas renvoyer str(e) au client."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable_old")
    code_only = _strip_comments(body)
    # Vérifier que str(e) n'apparaît pas dans un return ou jsonify.
    returns = re.findall(r'return.*', code_only)
    for ret in returns:
        assert "str(e)" not in ret, (
            "update_css_variable_old renvoie str(e) au client "
            "(fuite d'information technique)"
        )


def test_no_str_e_in_copy_colors():
    """copy_colors ne doit pas renvoyer str(e) au client."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "copy_colors")
    code_only = _strip_comments(body)
    returns = re.findall(r'return.*', code_only)
    for ret in returns:
        assert "str(e)" not in ret, (
            "copy_colors renvoie str(e) au client "
            "(fuite d'information technique)"
        )


def test_no_str_e_in_update_css_variable():
    """update_css_variable ne doit pas renvoyer str(e) au client."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable")
    code_only = _strip_comments(body)
    returns = re.findall(r'return.*', code_only)
    for ret in returns:
        assert "str(e)" not in ret


def test_error_messages_are_generic():
    """Les messages d'erreur renvoyés au client doivent être génériques
    (pas de détail technique)."""
    source = _read("routes/admin_config.py")
    for func_name in ["update_css_variable_old", "copy_colors", "update_css_variable"]:
        body = _extract_func(source, func_name)
        code_only = _strip_comments(body)
        returns = re.findall(r'return.*', code_only)
        for ret in returns:
            assert "str(e)" not in ret


# ---------------------------------------------------------------------------
# 4. Les détails techniques sont journalisés
# ---------------------------------------------------------------------------

def test_update_css_variable_old_logs_error():
    """L'exception doit être journalisée côté serveur (app.logger.error)."""
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable_old")
    assert "logger.error" in body or "logger.warning" in body


def test_copy_colors_logs_error():
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "copy_colors")
    assert "logger.error" in body or "logger.warning" in body


def test_update_css_variable_logs_error():
    source = _read("routes/admin_config.py")
    body = _extract_func(source, "update_css_variable")
    assert "logger.error" in body or "logger.warning" in body
