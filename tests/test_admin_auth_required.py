"""Régression : l'authentification est obligatoire pour l'administration (point 1.2).

Toute route ``/admin`` exige **toujours** une session authentifiée, quelle que
soit la valeur (dépréciée) de ``SECURITY_LOGIN_ADMIN``. Le refus prend la forme
adaptée au client :

- navigateur non connecté -> **redirection** vers la page de connexion ;
- appel AJAX/HTMX non connecté -> **401** JSON ;
- utilisateur connecté -> accès normal (200). Le **403** « connecté sans
  permission » relève des décorateurs de permission (``require_permission_api``),
  couverts par ``test_params_registry.py`` ; on le documente ici sans le dupliquer.

Trois niveaux, exécutables **sans MySQL ni serveur** :

1. Primitive réelle ``auth_utils.wants_json_response`` (détection AJAX/HTMX/JSON).
2. Comportement de la garde : une mini-app reproduit à l'identique la garde de
   production (``require_login_for_admin``) en s'appuyant sur la **vraie**
   primitive, avec ``SECURITY_LOGIN_ADMIN=False`` pour prouver qu'il ne peut plus
   ouvrir l'admin.
3. Régression statique : app.py n'ouvre plus l'admin selon SECURITY_LOGIN_ADMIN,
   le namespace Socket.IO admin exige l'auth, et l'UI/registre marquent la
   dépréciation. (app.py exige MySQL, d'où la vérification sur source.)
"""

import os
import re

import pytest
from flask import Flask, jsonify, redirect, request, url_for

# auth_utils est importable seul (contrairement à app.py qui exige MySQL).
from auth_utils import wants_json_response


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- 1. Primitive réelle : détection AJAX/HTMX/JSON ---------------------------

@pytest.fixture
def ctx_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def _wants_json(app, headers):
    with app.test_request_context("/admin/x", headers=headers):
        return wants_json_response(request)


def test_htmx_request_wants_json(ctx_app):
    assert _wants_json(ctx_app, {"HX-Request": "true"}) is True


def test_xhr_request_wants_json(ctx_app):
    assert _wants_json(ctx_app, {"X-Requested-With": "XMLHttpRequest"}) is True


def test_json_accept_wants_json(ctx_app):
    assert _wants_json(ctx_app, {"Accept": "application/json"}) is True


def test_browser_navigation_does_not_want_json(ctx_app):
    # Un navigateur qui navigue envoie text/html en tête de préférence.
    assert _wants_json(ctx_app, {"Accept": "text/html,application/xhtml+xml"}) is False


def test_no_accept_header_defaults_to_browser(ctx_app):
    # Absence d'Accept : traité comme navigation (redirection, pas 401).
    assert _wants_json(ctx_app, {}) is False


# --- 2. Comportement de la garde (reproduction fidèle de app.py) --------------

def _make_app(authenticated):
    """Mini-app reproduisant ``require_login_for_admin`` avec la vraie primitive.

    ``SECURITY_LOGIN_ADMIN`` est volontairement **False** : la garde ne doit PAS
    le consulter et doit quand même exiger l'authentification sur /admin."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-admin-auth"
    app.config["SECURITY_LOGIN_ADMIN"] = False  # déprécié → sans effet

    @app.route("/login")
    def login():
        return "login page", 200

    @app.route("/admin/security")
    def admin_page():
        return "admin ok", 200

    def _deny_unauthenticated_access():
        if wants_json_response(request):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("login", next=request.url))

    @app.before_request
    def require_login_for_admin():
        if request.path.startswith("/admin"):
            if not authenticated:
                return _deny_unauthenticated_access()

    return app


@pytest.fixture
def anon_client():
    return _make_app(authenticated=False).test_client()


@pytest.fixture
def auth_client():
    return _make_app(authenticated=True).test_client()


def test_unauthenticated_browser_is_redirected(anon_client):
    resp = anon_client.get("/admin/security")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_unauthenticated_htmx_gets_401(anon_client):
    resp = anon_client.get("/admin/security", headers={"HX-Request": "true"})
    assert resp.status_code == 401


def test_unauthenticated_xhr_gets_401(anon_client):
    resp = anon_client.get("/admin/security",
                           headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 401


def test_login_admin_flag_false_does_not_bypass(anon_client):
    """SECURITY_LOGIN_ADMIN=False ne rend PAS l'admin anonyme (régression 1.2)."""
    resp = anon_client.get("/admin/security")
    assert resp.status_code == 302  # toujours refusé malgré le drapeau False


def test_authenticated_user_reaches_admin(auth_client):
    resp = auth_client.get("/admin/security")
    assert resp.status_code == 200
    assert b"admin ok" in resp.data


# --- 3. Régression statique ---------------------------------------------------

def test_admin_guard_no_longer_gated_by_login_admin_flag():
    source = _read("app.py")
    # Isole la branche /admin de la garde.
    m = re.search(
        r"if request\.path\.startswith\('/admin'\):(.*?)elif request\.path\.startswith\('/counter'\):",
        source, re.DOTALL)
    assert m, "Branche /admin de require_login_for_admin introuvable"
    branch = m.group(1)
    assert "SECURITY_LOGIN_ADMIN" not in branch, (
        "La garde /admin ne doit plus dépendre de SECURITY_LOGIN_ADMIN")
    assert "if not current_user.is_authenticated:" in branch
    assert "_deny_unauthenticated_access()" in branch


def test_deny_helper_returns_401_or_redirect():
    source = _read("app.py")
    m = re.search(r"def _deny_unauthenticated_access\(\):(.*?)\n\n", source, re.DOTALL)
    assert m, "_deny_unauthenticated_access introuvable"
    body = m.group(1)
    assert "wants_json_response(request)" in body
    assert '{"error": "Unauthorized"}), 401' in body
    assert "redirect(url_for('admin_security.login'" in body


def test_debug_print_removed():
    source = _read("app.py")
    assert 'print("SECURITY_LOGIN_ADMIN"' not in source


def test_admin_socket_requires_authentication():
    source = _read("app.py")
    m = re.search(r"def connect_admin\(\):(.*?)def disconnect_admin", source, re.DOTALL)
    assert m, "connect_admin introuvable"
    body = m.group(1)
    assert "is_authenticated_request()" in body, (
        "Le namespace /socket_admin doit exiger l'authentification")
    assert '_socket_require("SECURITY_LOGIN_ADMIN"' not in body, (
        "Le namespace admin ne doit plus dépendre de SECURITY_LOGIN_ADMIN")


def test_registry_marks_login_admin_deprecated():
    source = _read("params_registry.py")
    # Le commentaire de dépréciation précède la clé.
    idx = source.index('"security_login_admin":')
    preceding = source[:idx]
    assert "DÉPRÉCIÉ" in preceding[-400:], (
        "La clé security_login_admin doit être marquée dépréciée dans le registre")


def test_ui_no_longer_offers_to_disable_admin_login():
    tpl = _read("templates/admin/security_general.html")
    assert 'macros.switch("security_login_admin"' not in tpl, (
        "L'UI ne doit plus proposer de désactiver le mot de passe admin")
    assert "authentification toujours requise" in tpl.lower()
