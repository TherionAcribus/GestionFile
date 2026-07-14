"""Tests du registre serveur des paramètres et de la sécurisation des routes
de modification des paramètres (point 1).

Trois niveaux, tous exécutables **sans MySQL ni serveur** :

1. Registre pur (``params_registry``) : complétude, cohérence des permissions,
   validateurs, rejet des clés inconnues.
2. Contrôle d'accès réel (``admin_security.user_has_permission`` /
   ``require_permission_api``) monté sur une petite app Flask : 401 sans
   session, 403 sans permission, 200 avec la bonne permission.
3. Régression statique : les routes ``update_*`` appellent bien la garde
   ``authorize_config_change`` et les routes ``/admin/app/*`` sensibles portent
   la permission ; le test d'e-mail est en POST. (``app.py`` exige MySQL et n'est
   pas importable ici, d'où la vérification sur le source.)
"""

import os
import re

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user

import params_registry as reg
from routes.admin_security import user_has_permission, require_permission_api


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Registre pur
# ---------------------------------------------------------------------------

def test_registry_not_empty_and_mappings_consistent():
    assert len(reg.PARAM_REGISTRY) > 100
    # Les clés chargées au démarrage == les clés modifiables : aucune dérive.
    assert set(reg.CONFIG_MAPPINGS) == set(reg.PARAM_REGISTRY)


def test_every_permission_is_a_known_resource():
    for key, spec in reg.PARAM_REGISTRY.items():
        assert spec.permission in reg.KNOWN_PERMISSIONS, (
            f"{key}: permission inconnue {spec.permission!r}")


def test_value_type_matches_validator():
    for key, spec in reg.PARAM_REGISTRY.items():
        if spec.value_type == "value_bool":
            assert spec.validator == "bool"
        elif spec.value_type == "value_int":
            assert spec.validator == "int"
        else:
            assert spec.validator in ("text", "welcome", "before_call", "after_call")


@pytest.mark.parametrize("key,permission", [
    ("security_login_admin", "security"),
    ("security_login_counter", "security"),
    ("security_remember_duration", "security"),
    ("network_adress", "app"),
    ("pharmacy_name", "app"),
    ("mail_server", "app"),
    ("mail_port", "app"),
    ("cron_delete_patient_table_hour", "schedule"),
    ("algo_activate", "algo"),
    ("announce_title", "announce"),
    ("counter_order", "counter"),
    ("admin_colors", "options"),
    ("music_spotify", "music_options"),
    ("phone_line1", "phone"),
    ("page_patient_title", "patient"),
    ("ticket_header", "patient"),
    ("printer_width", "patient"),
])
def test_key_permission_mapping(key, permission):
    spec = reg.get_spec(key)
    assert spec is not None
    assert spec.permission == permission


def test_security_keys_require_security_permission():
    """Verrou explicite : aucun paramètre de sécurité n'est modifiable via une
    autre permission (ex. 'app' ou 'patient')."""
    for key, spec in reg.PARAM_REGISTRY.items():
        if key.startswith("security_"):
            assert spec.permission == "security"


def test_unknown_keys_are_rejected():
    for bad in ["", "does_not_exist", "security_login_admin; DROP TABLE",
                "SECURITY_LOGIN_ADMIN", None, 42, "admin_security"]:
        assert reg.get_spec(bad) is None
        assert reg.is_known_key(bad) is False


def test_balise_validators_have_letters():
    assert reg.BALISE_LETTERS["welcome"] == "PDH"
    assert reg.BALISE_LETTERS["before_call"] == "PDHAN"
    assert reg.BALISE_LETTERS["after_call"] == "PDHANMC"


# ---------------------------------------------------------------------------
# 2. Contrôle d'accès réel
# ---------------------------------------------------------------------------

class _Role:
    def __init__(self, **perms):
        # Tous les admin_* à False par défaut, sauf ceux passés à True.
        for name in reg.KNOWN_PERMISSIONS:
            setattr(self, f"admin_{name}", False)
        for k, v in perms.items():
            setattr(self, k, v)


class _User(UserMixin):
    """Utilisateur factice dont l'``id`` encode la permission accordée, afin que
    le ``user_loader`` puisse le reconstruire d'une requête à l'autre (le client
    de test ne conserve que le cookie de session)."""
    def __init__(self, perm):
        self.perm = perm  # nom de permission, "none" ou "noroles"
        self.username = "tester"
        if perm == "noroles":
            self.roles = []
        elif perm == "none":
            self.roles = [_Role()]
        else:
            self.roles = [_Role(**{f"admin_{perm}": True})]

    def get_id(self):
        return self.perm


def _make_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    lm = LoginManager()
    lm.init_app(app)

    @lm.user_loader
    def _load(uid):
        return _User(uid)

    @app.route("/protected", methods=["POST"])
    @require_permission_api("app")
    def _protected():
        return jsonify({"ok": True}), 200

    # Route de login utilitaire pour établir une session avec un rôle donné.
    @app.route("/login/<perm>", methods=["POST"])
    def _login(perm):
        login_user(_User(perm))
        return "", 204

    @app.route("/login_noroles", methods=["POST"])
    def _login_noroles():
        login_user(_User("noroles"))
        return "", 204

    return app


def test_user_has_permission_logic():
    assert user_has_permission(None, "app") is False
    granted = _User("app")
    denied = _User("patient")
    noroles = _User("noroles")
    assert user_has_permission(granted, "app") is True
    assert user_has_permission(denied, "app") is False
    assert user_has_permission(noroles, "app") is False


def test_api_unauthenticated_gets_401():
    client = _make_app().test_client()
    resp = client.post("/protected")
    assert resp.status_code == 401


def test_api_authenticated_without_permission_gets_403():
    client = _make_app().test_client()
    client.post("/login/patient")  # connecté mais sans permission 'app'
    resp = client.post("/protected")
    assert resp.status_code == 403


def test_api_authenticated_without_roles_gets_403():
    client = _make_app().test_client()
    client.post("/login_noroles")
    resp = client.post("/protected")
    assert resp.status_code == 403


def test_api_authenticated_with_permission_gets_200():
    client = _make_app().test_client()
    client.post("/login/app")
    resp = client.post("/protected")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Régression statique sur le source
# ---------------------------------------------------------------------------

def _route_body(source, route):
    """Corps de la fonction de route ``route`` (jusqu'au prochain def/@app.route)."""
    m = re.search(r"def " + route + r"\(.*?\n(.*?)(?=\ndef |\n@app\.route)",
                  source, re.DOTALL)
    assert m, f"fonction {route} introuvable"
    return m.group(1)


def test_update_routes_call_the_guard():
    """Chaque route update_* doit appeler ``authorize_config_change`` : sans
    cet appel, la permission par clé n'est plus vérifiée."""
    source = _read("app.py")
    for route in ("update_switch", "update_input", "update_select"):
        assert "authorize_config_change(" in _route_body(source, route), (
            f"{route} doit appeler authorize_config_change")


def test_update_routes_rollback_on_exception():
    """Écritures atomiques (point 10) : chaque route update_* doit annuler la
    transaction (db.session.rollback()) en cas d'exception, pour ne pas laisser
    la base dans un état partiel."""
    source = _read("app.py")
    for route in ("update_switch", "update_input", "update_select"):
        assert "db.session.rollback()" in _route_body(source, route), (
            f"{route} doit appeler db.session.rollback() sur exception")


def test_update_input_has_single_commit():
    """Une seule opération logique => un seul commit : la version imprimante du
    ticket ne doit plus être committée séparément (pas de commit intermédiaire)."""
    body = _route_body(_read("app.py"), "update_input")
    assert body.count("db.session.commit()") == 1, (
        "update_input ne doit avoir qu'un seul db.session.commit()")


def test_update_routes_touch_app_config_after_commit():
    """app.config n'est mis à jour qu'APRÈS un commit réussi (point 10) : la
    mémoire ne doit pas refléter un changement non persisté."""
    source = _read("app.py")
    for route in ("update_switch", "update_input", "update_select"):
        body = _route_body(source, route)
        commit_idx = body.find("db.session.commit()")
        cfg_idx = body.find("app.config[spec.config_name]")
        assert commit_idx != -1, f"{route}: commit introuvable"
        assert cfg_idx != -1, f"{route}: mise à jour app.config introuvable"
        assert cfg_idx > commit_idx, (
            f"{route} doit mettre à jour app.config APRÈS le commit, pas avant")


def test_guard_returns_401_400_403():
    """La garde renvoie bien les trois statuts attendus."""
    source = _read("app.py")
    guard = re.search(r"def authorize_config_change\(.*?(?=\n@app\.route)",
                      source, re.DOTALL)
    assert guard, "authorize_config_change introuvable"
    body = guard.group(0)
    assert "401" in body and "400" in body and "403" in body


def test_mail_test_route_is_post_and_permissioned():
    source = _read("routes/admin_app.py")
    # Route déclarée en POST et décorée par require_permission_api('app').
    pattern = re.compile(
        r"@admin_app_bp\.route\(\s*['\"]/admin/app/mail/test['\"]\s*,\s*methods\s*=\s*\[[^\]]*POST[^\]]*\]\s*\)"
        r"\s*\n\s*@require_permission_api\(\s*['\"]app['\"]\s*\)")
    assert pattern.search(source), (
        "/admin/app/mail/test doit être POST et porter @require_permission_api('app')")
    assert "GET" not in re.search(
        r"/admin/app/mail/test['\"]\s*,\s*methods\s*=\s*(\[[^\]]*\])", source).group(1)


def test_get_connections_is_permissioned():
    source = _read("routes/admin_app.py")
    pattern = re.compile(
        r"@admin_app_bp\.route\(\s*['\"]/admin/app/get_connections['\"][^)]*\)"
        r"\s*\n\s*@require_permission_api\(\s*['\"]app['\"]\s*\)")
    assert pattern.search(source), (
        "/admin/app/get_connections doit porter @require_permission_api('app')")
