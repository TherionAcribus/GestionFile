"""Tests de la protection CSRF (point 2).

Deux niveaux, exécutables **sans MySQL ni serveur** :

1. Comportement réel : une petite app Flask monte le **vrai** ``CSRFProtect`` et
   reproduit à l'identique la garde de production
   (``csrf_protect_browser_requests`` + ``_csrf_is_exempt`` de app.py) :
   - une requête navigateur mutatrice sans jeton -> **400** ;
   - avec un jeton valide (en-tête ``X-CSRFToken``) -> **200** ;
   - endpoints machine/kiosque exemptés (``/api/``, ``/app/``, ``/patient``,
     ``/socket.io``) ou porteurs de ``X-App-Token`` -> **200** sans jeton.

2. Régression statique : app.py câble bien CSRFProtect (init, garde,
   gestionnaire d'erreur, préfixes exemptés) et les gabarits navigateur incluent
   le jeton + ``csrf.js``. (app.py exige MySQL, d'où la vérification sur source.)
"""

import os
import re

import pytest
from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- Reproduction fidèle de la garde de production (cf. app.py) ---
_CSRF_EXEMPT_PREFIXES = ("/socket.io", "/api/", "/app/", "/patient")


def _make_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret-csrf"
    app.config["TESTING"] = True
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    csrf = CSRFProtect(app)

    def _csrf_is_exempt():
        if request.path.startswith(_CSRF_EXEMPT_PREFIXES):
            return True
        if request.headers.get("X-App-Token"):
            return True
        return False

    @app.before_request
    def _guard():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        if _csrf_is_exempt():
            return
        csrf.protect()

    @app.errorhandler(CSRFError)
    def _csrf_error(error):
        return jsonify({"error": "CSRF validation failed"}), 400

    # Routes de test couvrant les cas navigateur et machine.
    @app.route("/token")
    def _token():
        return generate_csrf()

    @app.route("/admin/update_switch", methods=["POST"])
    def _admin_post():
        return jsonify({"ok": True})

    @app.route("/validate_and_call_next/<int:cid>", methods=["POST"])
    def _dual_post(cid):
        return jsonify({"ok": True})

    @app.route("/api/counter/validate_patient/<int:pid>", methods=["POST"])
    def _api_post(pid):
        return jsonify({"ok": True})

    @app.route("/app/counter/paper_add", methods=["POST"])
    def _app_post():
        return jsonify({"ok": True})

    @app.route("/patients_submit", methods=["POST"])
    def _patient_post():
        return jsonify({"ok": True})

    @app.route("/socket.io/", methods=["POST"])
    def _socket_post():
        return jsonify({"ok": True})

    return app


@pytest.fixture
def client():
    return _make_app().test_client()


# --- 1. Comportement réel ---

def test_browser_post_without_token_is_rejected(client):
    resp = client.post("/admin/update_switch", data={"key": "x", "value": "true"})
    assert resp.status_code == 400


def test_browser_post_with_valid_token_is_accepted(client):
    token = client.get("/token").get_data(as_text=True)
    resp = client.post("/admin/update_switch",
                       data={"key": "x", "value": "true"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200


def test_browser_post_with_invalid_token_is_rejected(client):
    resp = client.post("/admin/update_switch",
                       data={"key": "x"},
                       headers={"X-CSRFToken": "not-a-valid-token"})
    assert resp.status_code == 400


def test_dual_use_route_needs_token_in_browser(client):
    """Une route appelée par le navigateur ET l'App : sans jeton ni en-tête
    applicatif (donc navigateur) -> refusée."""
    resp = client.post("/validate_and_call_next/1")
    assert resp.status_code == 400


def test_dual_use_route_exempt_with_app_token(client):
    """La même route, appelée par App_Comptoir (X-App-Token) -> exemptée."""
    resp = client.post("/validate_and_call_next/1",
                       headers={"X-App-Token": "whatever-token"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", [
    "/api/counter/validate_patient/1",
    "/app/counter/paper_add",
    "/patients_submit",
    "/socket.io/",
])
def test_machine_and_kiosk_paths_are_exempt(client, path):
    resp = client.post(path)
    assert resp.status_code == 200


def test_get_is_never_checked(client):
    # Le point de terminaison /token est un GET : jamais soumis au CSRF.
    assert client.get("/token").status_code == 200


# --- 2. Régression statique ---

def test_app_wires_csrf_protect():
    source = _read("app.py")
    assert "from flask_wtf.csrf import CSRFProtect, CSRFError" in source
    assert "csrf = CSRFProtect()" in source
    assert "csrf.init_app(app)" in source
    assert 'app.config.setdefault("WTF_CSRF_CHECK_DEFAULT", False)' in source


def test_app_has_before_request_guard_and_error_handler():
    source = _read("app.py")
    assert "def csrf_protect_browser_requests" in source
    assert "csrf.protect()" in source
    assert "@app.errorhandler(CSRFError)" in source


def test_exempt_prefixes_cover_machine_and_kiosk():
    source = _read("app.py")
    m = re.search(r"_CSRF_EXEMPT_PREFIXES\s*=\s*\((.*?)\n\)", source, re.DOTALL)
    assert m, "_CSRF_EXEMPT_PREFIXES introuvable"
    body = m.group(1)
    for prefix in ('"/socket.io"', '"/api/"', '"/app/"', '"/patient"'):
        assert prefix in body, f"{prefix} doit être exempté"
    # Exemption par en-tête applicatif présente.
    assert 'request.headers.get("X-App-Token")' in source


def test_browser_templates_include_csrf_token_and_script():
    for tpl in ("templates/admin/base.html",
                "templates/counter/counter.html",
                "templates/counter/countert.html"):
        content = _read(tpl)
        assert 'name="csrf-token"' in content, f"meta csrf manquant dans {tpl}"
        assert "csrf_token()" in content, f"csrf_token() manquant dans {tpl}"
        assert "js/csrf.js" in content, f"csrf.js non inclus dans {tpl}"


def test_csrf_js_hooks_htmx_fetch_jquery():
    js = _read("static/js/csrf.js")
    assert "htmx:configRequest" in js
    assert "window.fetch" in js
    assert "ajaxSetup" in js
    assert "X-CSRFToken" in js
    # Ne doit jamais poser le jeton sur une autre origine.
    assert "isSameOrigin" in js
