"""Point 13 (audit Admin) — Hygiène frontend : console.log, handlers inline, CSP.

Trois améliorations :

1. **console.log inconditionnels retirés** : ``admin.js`` et
   ``patient_conclusion.js`` contenaient des ``console.log`` de production
   (debug galerie, FormData, audio, placeholders, color picker, CSS update).
   Ils exposaient des données internes dans la console du navigateur et
   polluaient les logs. Seuls les ``console.log`` gated par ``DEBUG``
   (Socket.IO) sont conservés.

2. **Handlers inline retirés de macros.html** : les boutons
   ``.variables_calling`` utilisaient ``onclick="insertPlaceholder(...)"``,
   et les champs ``textarea``/``input`` utilisaient
   ``oninput="handleInputChangeConfig(...)"`` et
   ``onkeydown="handleKeyPressConfig(...)"``. Remplacés par des
   ``data-attributes`` + ``addEventListener`` par délégation dans
   ``admin_macros.js``. Cela permet de verrouiller la CSP sans
   ``'unsafe-inline'`` pour les scripts.

3. **En-têtes de sécurité HTTP** : ``X-Content-Type-Options: nosniff``,
   ``X-Frame-Options: SAMEORIGIN``, et une ``Content-Security-Policy``
   conservative ajoutés via ``after_request`` dans ``app.py``.

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
# 1. console.log inconditionnels retirés de admin.js
# ---------------------------------------------------------------------------

def test_admin_js_no_unconditional_console_log():
    """admin.js ne doit plus contenir de console.log en dehors d'un bloc
    DEBUG (if (DEBUG) { ... })."""
    source = _read("static/js/admin.js")
    lines = source.split('\n')
    # Trouver le bloc if (DEBUG) et sa fin (la ligne avec juste "}" après).
    debug_start = None
    debug_end = None
    for i, line in enumerate(lines):
        if 'if (DEBUG)' in line:
            debug_start = i
        elif debug_start is not None and debug_end is None and line.strip() == '}':
            debug_end = i
    for i, line in enumerate(lines, 1):
        if 'console.log' in line:
            # Doit être dans le bloc DEBUG (entre debug_start et debug_end).
            assert debug_start is not None and debug_end is not None, (
                "Aucun bloc if (DEBUG) trouvé dans admin.js"
            )
            idx = i - 1  # 0-based
            assert debug_start < idx < debug_end, (
                f"console.log inconditionnel à la ligne {i} de admin.js : "
                f"{line.strip()}"
            )


def test_admin_js_debug_console_log_preserved():
    """Les console.log gated par DEBUG doivent être conservés (utiles pour
    le développement)."""
    source = _read("static/js/admin.js")
    assert "if (DEBUG)" in source
    assert "console.log" in source


def test_admin_colors_js_no_console_log():
    """Idem pour admin_colors.js, extrait de admin.js (Phase 8, point 1).

    Le découpage aurait sinon créé un trou : la moitié du code d'origine
    (sélecteurs de couleur, mise à jour CSS) sortait du champ de la
    vérification ci-dessus. Ce fichier n'a pas de bloc DEBUG : aucun
    console.log n'y est admis.
    """
    source = _read("static/js/admin_colors.js")
    fautifs = [f"ligne {i}: {ligne.strip()}"
               for i, ligne in enumerate(source.splitlines(), 1)
               if "console.log" in ligne]
    assert not fautifs, "console.log dans admin_colors.js :\n" + "\n".join(fautifs)


def test_patient_conclusion_no_console_log():
    """patient_conclusion.js ne doit plus contenir de console.log."""
    source = _read("static/js/patient_conclusion.js")
    assert "console.log" not in source, (
        "patient_conclusion.js ne doit plus contenir de console.log"
    )


# ---------------------------------------------------------------------------
# 2. Handlers inline retirés de macros.html
# ---------------------------------------------------------------------------

def test_macros_html_no_onclick():
    """macros.html ne doit plus contenir d'attributs onclick."""
    source = _read("templates/admin/macros.html")
    # On retire les commentaires avant de vérifier.
    code_only = re.sub(r'<!--.*?-->', '', source, flags=re.DOTALL)
    assert 'onclick=' not in code_only, (
        "macros.html ne doit plus contenir d'attributs onclick"
    )


def test_macros_html_no_oninput():
    """macros.html ne doit plus contenir d'attributs oninput."""
    source = _read("templates/admin/macros.html")
    code_only = re.sub(r'<!--.*?-->', '', source, flags=re.DOTALL)
    assert 'oninput=' not in code_only, (
        "macros.html ne doit plus contenir d'attributs oninput"
    )


def test_macros_html_no_onkeydown():
    """macros.html ne doit plus contenir d'attributs onkeydown."""
    source = _read("templates/admin/macros.html")
    code_only = re.sub(r'<!--.*?-->', '', source, flags=re.DOTALL)
    assert 'onkeydown=' not in code_only, (
        "macros.html ne doit plus contenir d'attributs onkeydown"
    )


def test_macros_html_uses_data_placeholder():
    """Les boutons .variables_calling doivent utiliser data-placeholder-key
    et data-placeholder-text au lieu de onclick."""
    source = _read("templates/admin/macros.html")
    assert 'data-placeholder-key' in source
    assert 'data-placeholder-text' in source


def test_macros_html_uses_data_config_key():
    """Les champs textarea/input doivent utiliser data-config-key au lieu
    de oninput/onkeydown."""
    source = _read("templates/admin/macros.html")
    assert 'data-config-key' in source


def test_admin_macros_js_has_delegated_listeners():
    """admin_macros.js doit attacher des listeners par délégation pour
    .variables_calling et [data-config-key]."""
    source = _read("static/js/admin_macros.js")
    assert "addEventListener('click'" in source
    assert "addEventListener('input'" in source
    assert "addEventListener('keydown'" in source
    assert ".variables_calling" in source
    assert "data-config-key" in source
    assert "data-placeholder-key" in source


# ---------------------------------------------------------------------------
# 3. En-têtes de sécurité dans app.py
# ---------------------------------------------------------------------------

def test_app_has_security_headers_after_request():
    """app.py doit définir un after_request qui ajoute les en-têtes de
    sécurité."""
    source = _read("app.py")
    assert "X-Content-Type-Options" in source
    assert "nosniff" in source
    assert "X-Frame-Options" in source
    assert "SAMEORIGIN" in source
    assert "Content-Security-Policy" in source


def _extract_csp(source):
    """Extrait la valeur de la CSP depuis app.py.

    La CSP est construite par concaténation de plusieurs chaînes entre
    parenthèses, donc on extrait tout le bloc entre les parenthèses de
    setheader et on reconstitue la chaîne complète.
    """
    # Trouver la ligne avec Content-Security-Policy
    m = re.search(r"Content-Security-Policy['\"]?,\s*(.*?)(?:\n\s*\)\s*$|\n\s*return)",
                  source, re.DOTALL | re.MULTILINE)
    if not m:
        # Approche alternative : capturer tout le bloc après setheader
        m = re.search(r"Content-Security-Policy.*?\n(.*?return response",
                      source, re.DOTALL)
    if not m:
        return None
    # Concaténer toutes les chaînes entre guillemets dans le bloc.
    block = m.group(1)
    strings = re.findall(r'"([^"]*)"', block)
    return ' '.join(strings) if strings else None


def test_app_csp_has_no_unsafe_inline_scripts():
    """La CSP ne doit pas autoriser 'unsafe-inline' pour les scripts
    (les handlers inline ont été retirés)."""
    source = _read("app.py")
    csp = _extract_csp(source)
    assert csp is not None, "CSP introuvable dans app.py"
    # script-src ne doit pas contenir 'unsafe-inline'.
    script_m = re.search(r"script-src\s+([^;]+)", csp)
    if script_m:
        script_src = script_m.group(1)
        assert "'unsafe-inline'" not in script_src, (
            "La CSP ne doit pas autoriser 'unsafe-inline' pour les scripts "
            "(les handlers inline ont été retirés)"
        )


def test_app_csp_has_self_for_scripts():
    """La CSP doit autoriser 'self' pour les scripts."""
    source = _read("app.py")
    csp = _extract_csp(source)
    assert csp is not None, "CSP introuvable"
    assert "script-src 'self'" in csp


def test_app_csp_allows_websocket():
    """La CSP doit autoriser ws: et wss: pour Socket.IO."""
    source = _read("app.py")
    csp = _extract_csp(source)
    assert csp is not None, "CSP introuvable"
    assert "ws:" in csp
    assert "wss:" in csp


def test_app_csp_disables_object_src():
    """La CSP doit désactiver object-src (pas de Flash/Java/plugins)."""
    source = _read("app.py")
    csp = _extract_csp(source)
    assert csp is not None, "CSP introuvable"
    assert "object-src 'none'" in csp


def test_app_csp_sets_frame_ancestors():
    """La CSP doit définir frame-ancestors 'self' (protection clickjacking)."""
    source = _read("app.py")
    csp = _extract_csp(source)
    assert csp is not None, "CSP introuvable"
    assert "frame-ancestors 'self'" in csp
