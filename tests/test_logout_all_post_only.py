"""Point 1 (audit Admin) — /logout_all doit être POST-only avec CSRF.

La route ``/logout_all`` supprime toutes les sessions Flask : c'est une
opération destructive (déconnexion de tous les utilisateurs). Elle était
auparavant accessible en **GET**, ce qui permettait à un lien ou une image
intégrée de déclencher la déconnexion de tous les utilisateurs (CSRF par GET).

Corrigé :
1. La route est désormais ``methods=['POST']`` : Flask renvoie 405 sur GET.
2. Le CSRF global (``before_request`` dans ``app.py``) valide le jeton sur
   les POST navigateur.
3. Un bouton avec confirmation est ajouté dans ``security_general.html``,
   dans un formulaire POST avec ``csrf_token``.

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
# 1. La route est POST-only
# ---------------------------------------------------------------------------

def test_logout_all_is_post_only():
    """La route /logout_all doit être déclarée avec methods=['POST'].
    Sans methods explicite, Flask accepte GET par défaut."""
    source = _read("routes/admin_security.py")
    m = re.search(
        r"@\w+\.route\(\s*['\"]\/logout_all['\"]\s*,\s*methods\s*=\s*(\[[^\]]*\])",
        source,
    )
    assert m, (
        "La route /logout_all doit avoir methods=['POST'] explicite. "
        "Sans methods, Flask accepte GET par défaut (CSRF par lien/image)."
    )
    methods = m.group(1)
    assert "POST" in methods
    assert "GET" not in methods, (
        "/logout_all ne doit plus accepter GET (CSRF par lien/image possible)"
    )


def test_logout_all_still_has_permission_guard():
    """La route doit toujours être protégée par require_permission('security')."""
    source = _read("routes/admin_security.py")
    # Trouver le bloc route + décorateurs
    m = re.search(
        r"@\w+\.route\(\s*['\"]\/logout_all['\"].*?\n.*?\n.*?def logout_all",
        source, re.DOTALL)
    assert m, "route /logout_all introuvable"
    block = m.group(0)
    assert "require_permission('security')" in block


def test_logout_all_still_calls_record_audit():
    """La route doit toujours appeler record_audit (ne pas casser le test
    existant test_audit_wiring)."""
    source = _read("routes/admin_security.py")
    m = re.search(r"def logout_all\(\):(.*?)(?=\ndef |\Z)", source, re.DOTALL)
    assert m, "fonction logout_all introuvable"
    body = m.group(1)
    assert "record_audit" in body


# ---------------------------------------------------------------------------
# 2. Le template security_general.html contient un formulaire POST
# ---------------------------------------------------------------------------

def test_security_general_has_logout_all_form():
    """La page Sécurité doit proposer un formulaire POST vers /logout_all."""
    source = _read("templates/admin/security_general.html")
    assert "/admin/logout_all" in source
    assert 'method="post"' in source.lower() or "hx-post" in source


def test_security_general_form_has_csrf_token():
    """Le formulaire doit inclure un csrf_token (CSRF protection)."""
    source = _read("templates/admin/security_general.html")
    assert "csrf_token" in source


def test_security_general_form_has_confirmation():
    """Le formulaire doit demander une confirmation avant l'action."""
    source = _read("templates/admin/security_general.html")
    assert "hx-confirm" in source or "confirm" in source.lower()


def test_security_general_has_advanced_actions_card():
    """Le bouton doit être dans une zone visuellement distincte (danger)."""
    source = _read("templates/admin/security_general.html")
    assert "border-danger" in source or "bg-danger" in source


# ---------------------------------------------------------------------------
# 3. Aucun lien GET vers /logout_all dans les templates
# ---------------------------------------------------------------------------

def test_no_get_link_to_logout_all_in_templates():
    """Aucun template ne doit contenir un lien GET (href) vers /logout_all."""
    import os
    templates_dir = os.path.join(_SERVEUR, "templates")
    for root, dirs, files in os.walk(templates_dir):
        for fname in files:
            if not fname.endswith(".html"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding="utf-8") as fh:
                content = fh.read()
            # Un lien href="/admin/logout_all" ou href="/logout_all" est
            # interdit (GET). Le formulaire POST dans security_general.html
            # est OK (action="/admin/logout_all" + method="post").
            # On cherche les href uniquement.
            hrefs = re.findall(r'href=["\']([^"\']*logout_all[^"\']*)["\']', content)
            assert not hrefs, (
                f"{fname} contient un lien GET vers logout_all : {hrefs}. "
                "Cette route est désormais POST-only."
            )
