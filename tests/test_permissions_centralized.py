"""Point 1.3 — Centralisation des permissions.

Vérifie deux choses, sans MySQL ni serveur :

1. **Couverture** : toute route d'un blueprint d'administration (``routes/admin_*``)
   porte une garde de permission explicite (un des décorateurs
   ``require_permission`` / ``require_permission_api`` / ``require_permission_dashboard``
   ou la garde jeton ``require_app_token_or_login``), sauf une courte liste
   d'exceptions **documentées** (pages publiques, page d'accueil du tableau de
   bord auth-only, routes à permission dépendante de la requête gardées
   *inline*). C'est la garantie « sous-routes, pas seulement la page principale ».

2. **Comportement** : les deux variantes partagent la même décision d'accès
   (source unique ``_permission_status``) et renvoient la bonne forme de refus :
   - variante PAGE : redirection (anonyme) / **403 HTML** (connecté sans droit) ;
   - variante API : **401 / 403 JSON**.

3. **Régressions** : les anciennes routes cassées/dangereuses
   (``/admin/check_default_admin``, ``/send_test_email``) ne sont plus exposées,
   et les routes CSS génériques d'``app.py`` posent une garde de permission.
"""

import ast
import os
import re

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user

from routes.admin_security import (
    require_permission,
    require_permission_api,
    permission_error_response,
    _permission_status,
)


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_ROUTES_DIR = os.path.join(_SERVEUR, "routes")


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Couverture statique : toute route admin porte une garde explicite
# ---------------------------------------------------------------------------

# Décorateurs considérés comme des gardes d'autorisation valides.
_GUARD_DECORATORS = {
    "require_permission",
    "require_permission_api",
    "require_permission_dashboard",
    "require_app_token_or_login",
}

# Exceptions documentées, par nom de fonction de vue. Chaque entrée est justifiée.
_ALLOWED_WITHOUT_GUARD = {
    # Authentification : doivent rester publiques.
    "login",
    "logout",
    # Sert un fichier audio statique à l'écran d'annonce (public, comme /static).
    "serve_sound",
    # Message d'erreur statique d'auth Spotify (aucune donnée).
    "error_page",
    # Page musique : contrôle d'accès *inline* (music_play OU music_options).
    "admin_music",
    # Accueil du tableau de bord : auth-only (garde globale /admin du point 1.2),
    # accessible à tout admin qui n'y voit que ses cartes.
    "admin",
}


def _iter_route_functions(tree):
    """Génère (func_name, decorator_names) pour chaque vue décorée par ``*.route``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        deco_names = []
        is_route = False
        for deco in node.decorator_list:
            # @bp.route(...) -> Call(func=Attribute(attr='route'))
            target = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(target, ast.Attribute):
                if target.attr == "route":
                    is_route = True
                deco_names.append(target.attr)
            elif isinstance(target, ast.Name):
                deco_names.append(target.id)
        if is_route:
            yield node.name, deco_names


def _admin_route_modules():
    for name in os.listdir(_ROUTES_DIR):
        if name.startswith("admin_") and name.endswith(".py"):
            yield os.path.join("routes", name)


def test_every_admin_route_has_explicit_permission_guard():
    offenders = []
    for rel in _admin_route_modules():
        tree = ast.parse(_read(rel), rel)
        for func_name, decos in _iter_route_functions(tree):
            if func_name in _ALLOWED_WITHOUT_GUARD:
                continue
            if not (_GUARD_DECORATORS & set(decos)):
                offenders.append(f"{rel}::{func_name}")

    assert not offenders, (
        "Routes d'administration sans garde de permission explicite "
        "(point 1.3 — chaque sous-route doit être protégée) :\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_broken_dangerous_routes_are_no_longer_exposed():
    source = _read("routes/admin_security.py")
    # check_default_admin : renvoyait un booléen (vue invalide) et divulguait le
    # compte par défaut ; send_test_email : signature invalide + envoi d'e-mail
    # non authentifié. Les deux sont désormais de simples fonctions utilitaires
    # (plus aucune ``@..._bp.route`` déclarée pour elles).
    assert not re.search(r"@\w+\.route\(\s*['\"]/admin/check_default_admin['\"]", source)
    assert not re.search(r"@\w+\.route\(\s*['\"]/send_test_email['\"]", source)
    assert "def check_default_admin(" in source  # conservée comme utilitaire
    assert "def send_test_email(" in source


def test_css_routes_in_app_are_permissioned():
    source = _read("app.py")
    # Les 3 routes CSS génériques posent une garde dont la ressource dépend de la
    # page ciblée (permission_error_response + table CSS_SOURCE_PERMISSION).
    assert "CSS_SOURCE_PERMISSION" in source
    for route in ("update_css_variable", "update_css_variable_old", "copy_colors"):
        m = re.search(
            r"def " + route + r"\(.*?\n(.*?)(?=\ndef |\n@app\.route)", source, re.DOTALL)
        assert m, f"fonction {route} introuvable"
        assert "permission_error_response(" in m.group(1), (
            f"{route} doit poser une garde de permission (inline, par source)")


# ---------------------------------------------------------------------------
# 2. Comportement : variantes PAGE et API sur une vraie app Flask
# ---------------------------------------------------------------------------

class _Role:
    def __init__(self, perm):
        self.admin_security = (perm == "security")
        self.admin_app = (perm == "app")

    def has_permission(self, name):  # compat éventuelle
        return getattr(self, f"admin_{name}", False)


class _User(UserMixin):
    def __init__(self, perm):
        self.perm = perm
        self.username = "tester"
        self.roles = [] if perm == "noroles" else [_Role(perm)]

    def get_id(self):
        return self.perm


def _make_app(template_folder=None):
    app = Flask(__name__, template_folder=template_folder or "templates")
    app.config["SECRET_KEY"] = "test-secret-1-3"
    app.config["TESTING"] = True
    lm = LoginManager()
    lm.init_app(app)

    @lm.user_loader
    def _load(uid):
        return _User(uid)

    # Route de connexion factice (cible de la redirection de la variante PAGE).
    # Le décorateur redirige vers l'endpoint 'security.login' ; on l'enregistre.
    from flask import Blueprint
    security_bp = Blueprint("security", __name__)

    @security_bp.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(security_bp)

    @app.route("/page")
    @require_permission("security")
    def page():
        return "page ok", 200

    @app.route("/api", methods=["POST"])
    @require_permission_api("security")
    def api():
        return jsonify(ok=True), 200

    @app.route("/login-as/<perm>", methods=["POST"])
    def login_as(perm):
        login_user(_User(perm))
        return "", 204

    return app


@pytest.fixture
def client(tmp_path):
    # Gabarit minimal pour la page d'erreur 403, écrit dans un dossier temporaire
    # (aucune écriture dans le dépôt).
    tpl_dir = tmp_path / "admin"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "permission_error.html").write_text("{{ error_message }}", encoding="utf-8")
    app = _make_app(template_folder=str(tmp_path))
    return app.test_client()


def test_status_helper_matches_variants():
    """La source unique de décision distingue bien 401 / 403 / autorisé."""
    app = _make_app()
    with app.test_request_context():
        assert _permission_status("security") == 401  # anonyme
    with app.test_request_context():
        login_user(_User("app"))
        assert _permission_status("security") == 403  # connecté, mauvaise perm
        assert _permission_status("app") is None       # connecté, bonne perm


def test_page_variant_redirects_anonymous(client):
    resp = client.get("/page")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_page_variant_forbids_connected_without_permission(client):
    client.post("/login-as/app")  # connecté mais sans 'security'
    resp = client.get("/page")
    assert resp.status_code == 403  # 403 explicite (plus de 200 muet)


def test_page_variant_allows_with_permission(client):
    client.post("/login-as/security")
    resp = client.get("/page")
    assert resp.status_code == 200
    assert b"page ok" in resp.data


def test_api_variant_401_403_200(client):
    # Anonyme -> 401 JSON
    r = client.post("/api")
    assert r.status_code == 401
    assert r.is_json
    # Connecté sans permission -> 403 JSON
    client.post("/login-as/app")
    r = client.post("/api")
    assert r.status_code == 403
    assert r.is_json
    # Connecté avec permission -> 200
    client.post("/login-as/security")
    r = client.post("/api")
    assert r.status_code == 200
