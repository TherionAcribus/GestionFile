"""Tests de la protection CSRF (points 2.2 / 2.3).

Deux niveaux, exécutables **sans MySQL ni serveur** :

1. Comportement réel : une petite app Flask monte le **vrai** ``CSRFProtect`` et
   reproduit à l'identique la garde de production
   (``csrf_protect_browser_requests`` + ``_csrf_is_exempt`` de app.py), y compris
   le durcissement du point 2.2 :
   - une requête navigateur mutatrice sans jeton -> **400** ;
   - avec un jeton valide (en-tête ``X-CSRFToken``) -> **200** ;
   - endpoints machine/kiosque exemptés par PRÉFIXE (``/api/``, ``/app/``,
     ``/patient``, ``/socket.io``) -> **200** sans jeton ;
   - route à double usage (``/validate_and_call_next``) : exemptée **uniquement**
     avec un jeton applicatif **valide** ; un jeton bidon ne contourne PAS ;
   - l'administration (``/admin``) n'est **jamais** exemptée par un en-tête
     applicatif, même avec un jeton valide.

2. Régression statique : app.py câble bien CSRFProtect (init, garde,
   gestionnaire d'erreur, préfixes exemptés, durcissement 2.2) et les gabarits
   navigateur incluent le jeton + ``csrf.js``. Les formulaires natifs (point 2.3)
   portent un champ CSRF caché. (app.py exige MySQL, d'où la vérif sur source.)
"""

import os
import re
from datetime import datetime, timedelta

import jwt
import pytest
from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_SECRET = "test-secret-csrf"


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- Reproduction fidèle de la garde de production (cf. app.py, point 2.2) ---
_CSRF_EXEMPT_PREFIXES = ("/socket.io", "/api/", "/app/", "/patient")
_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES = ("/validate_and_call_next",)


def _verify_app_token(token):
    """Réplique fidèle de app.verify_app_token."""
    if not token:
        return False
    try:
        jwt.decode(token, _SECRET, algorithms=["HS256"])
        return True
    except jwt.InvalidTokenError:
        return False


def _valid_app_token():
    exp = datetime.utcnow() + timedelta(days=1)
    return jwt.encode({"exp": exp}, _SECRET, algorithm="HS256")


def _make_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = _SECRET
    app.config["TESTING"] = True
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    csrf = CSRFProtect(app)

    def _csrf_is_exempt():
        path = request.path
        if path.startswith(_CSRF_EXEMPT_PREFIXES):
            return True
        token = request.headers.get("X-App-Token")
        if token and _verify_app_token(token) and path.startswith(_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES):
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

    @app.route("/admin/security/delete_role/<int:rid>", methods=["DELETE"])
    def _admin_delete(rid):
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


def test_dual_use_route_exempt_with_valid_app_token(client):
    """La même route, appelée par App_Comptoir avec un jeton VALIDE -> exemptée."""
    resp = client.post("/validate_and_call_next/1",
                       headers={"X-App-Token": _valid_app_token()})
    assert resp.status_code == 200


# --- Point 2.2 : durcissement de l'exemption par en-tête applicatif ---

def test_invalid_app_token_does_not_bypass_csrf(client):
    """Un en-tête X-App-Token présent mais INVALIDE ne doit RIEN exempter :
    la seule présence de l'en-tête ne suffit plus (faille du point 2.2)."""
    resp = client.post("/validate_and_call_next/1",
                       headers={"X-App-Token": "jeton-bidon-non-signe"})
    assert resp.status_code == 400


def test_admin_route_never_exempt_by_app_token_even_valid(client):
    """Une route d'administration ne doit JAMAIS être exemptée par un simple
    en-tête applicatif, même avec un jeton parfaitement valide."""
    valid = _valid_app_token()
    resp_post = client.post("/admin/update_switch",
                            data={"key": "x"},
                            headers={"X-App-Token": valid})
    assert resp_post.status_code == 400

    resp_delete = client.delete("/admin/security/delete_role/1",
                                headers={"X-App-Token": valid})
    assert resp_delete.status_code == 400


def test_valid_app_token_only_exempts_allowlisted_route(client):
    """Un jeton valide sur une route hors allowlist (ici /admin) ne contourne
    pas ; il n'exempte que les routes machine explicitement prévues."""
    valid = _valid_app_token()
    # Route allowlistée -> exemptée
    assert client.post("/validate_and_call_next/1",
                       headers={"X-App-Token": valid}).status_code == 200
    # Route non allowlistée -> contrôlée malgré le jeton valide
    assert client.post("/admin/update_switch",
                       headers={"X-App-Token": valid}).status_code == 400


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


def test_app_token_exemption_is_hardened():
    """Point 2.2 : l'exemption par en-tête exige un jeton VALIDE (verify_app_token)
    ET une route allowlistée. L'administration n'est pas dans l'allowlist."""
    source = _read("app.py")
    # La branche d'exemption par en-tête vérifie désormais la validité du jeton
    # et l'appartenance à l'allowlist, plus la seule présence de l'en-tête.
    m = re.search(r"def _csrf_is_exempt\(\):(.*?)\n\ndef ", source, re.DOTALL)
    assert m, "_csrf_is_exempt introuvable"
    body = m.group(1)
    assert 'request.headers.get("X-App-Token")' in body
    assert "verify_app_token(token)" in body, "le jeton doit être VÉRIFIÉ, pas seulement présent"
    assert "_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES" in body, "l'exemption doit être limitée à une allowlist de routes"
    # L'allowlist ne doit pas ouvrir l'administration ni Spotify.
    m2 = re.search(r"_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES\s*=\s*\((.*?)\n\)", source, re.DOTALL)
    assert m2, "_CSRF_APP_TOKEN_ELIGIBLE_PREFIXES introuvable"
    allow = m2.group(1)
    assert "/admin" not in allow, "l'admin ne doit jamais être exemptée par en-tête"
    assert "/spotify" not in allow, "Spotify ne doit jamais être exemptée par en-tête"
    assert "/validate_and_call_next" in allow


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


# --- Point 2.3 : formulaires natifs (form.submit()) portent un champ CSRF caché ---

@pytest.mark.parametrize("tpl", [
    "templates/admin/patient_page_button_modal_gallery.html",
    "templates/admin/patient_page_button_modal_gallery_for_interface.html",
    "templates/admin/macros.html",
])
def test_native_forms_carry_hidden_csrf_field(tpl):
    content = _read(tpl)
    assert 'name="csrf_token"' in content, f"champ CSRF caché manquant dans {tpl}"
    assert "csrf_token()" in content, f"valeur csrf_token() manquante dans {tpl}"
