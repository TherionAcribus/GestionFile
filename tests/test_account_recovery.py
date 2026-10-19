"""Récupération de compte sans e-mail (account_recovery.py).

Trois voies, et le mécanisme commun « mot de passe temporaire » :

1. noyau pur : mot de passe temporaire lisible, empreinte de la variable
   ``ADMIN_RESET_PASSWORD``, pages accessibles avant le changement ;
2. service : commande de secours ``manage.py reset-password`` et application
   unique de ``ADMIN_RESET_PASSWORD`` au démarrage ;
3. routes, sur une application Flask minimale + SQLite en mémoire (patron de
   ``test_admin_security_ui_contracts.py``) : page « Mon mot de passe », garde
   qui y renvoie, réinitialisation par un admin et création de compte avec
   changement obligatoire ;
4. câblage statique : app.py, manage.py, compose Coolify, gabarits.
"""

import os
import re
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask, request
from flask_login import LoginManager

from account_recovery import (
    MUST_CHANGE_ALLOWED_ENDPOINTS,
    env_reset_marker,
    env_reset_needed,
    generate_temporary_password,
    is_allowed_while_must_change,
)
from models import Role, User, db
from password_policy import validate_password

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_ROOT_PWD = "Root-pass-2026!"
_BOB_PWD = "Bob-pass-2026!"
_NEW_PWD = "Nouveau-mot-2026"


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Noyau pur
# ---------------------------------------------------------------------------

def test_mot_de_passe_temporaire_lisible_et_conforme():
    pwd = generate_temporary_password()
    assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", pwd)
    # Pas de caractères ambigus à la dictée.
    assert not set(pwd) & set("01ilo")
    assert validate_password(pwd, username="admin") == []


def test_mots_de_passe_temporaires_differents():
    assert len({generate_temporary_password() for _ in range(50)}) == 50


def test_empreinte_vide_pour_valeur_absente():
    assert env_reset_marker(None) is None
    assert env_reset_marker("   ") is None


def test_empreinte_stable_et_sans_la_valeur():
    marker = env_reset_marker("Temp-pass-2026")
    assert marker == env_reset_marker("  Temp-pass-2026 ")
    assert len(marker) == 64
    assert "Temp-pass" not in marker
    assert marker != env_reset_marker("Autre-pass-2026")


def test_reinitialisation_necessaire_une_seule_fois():
    assert env_reset_needed("", None) is False
    assert env_reset_needed("Temp-pass-2026", None) is True
    applied = env_reset_marker("Temp-pass-2026")
    assert env_reset_needed("Temp-pass-2026", applied) is False
    # Une NOUVELLE valeur se réapplique (nouvel oubli).
    assert env_reset_needed("Autre-pass-2026", applied) is True


def test_pages_accessibles_avant_changement():
    assert is_allowed_while_must_change("admin_security.account_password")
    assert is_allowed_while_must_change("static")
    assert not is_allowed_while_must_change("admin_security.security")
    assert not is_allowed_while_must_change(None)
    assert MUST_CHANGE_ALLOWED_ENDPOINTS == {"admin_security.account_password", "static"}


# ---------------------------------------------------------------------------
# Application minimale
# ---------------------------------------------------------------------------

def _make_app():
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-account-recovery",
        SESSION_PROTECTION="none",
        WTF_CSRF_ENABLED=False,
    )
    db.init_app(app)
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"

    @app.context_processor
    def _inject_security_helpers():
        from routes.admin_security import user_has_permission
        return {"user_has_permission": user_has_permission}

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=str(user_id)).first()
        return user if user and user.active else None

    from routes.admin_security import (
        admin_security_bp,
        must_change_password_blocks,
        password_change_required_response,
    )
    app.register_blueprint(admin_security_bp, url_prefix="")

    # Même garde que require_login_for_admin (app.py), vérifiée plus bas.
    @app.before_request
    def _guard():
        if request.path.startswith("/admin") and must_change_password_blocks(request.endpoint):
            return password_change_required_response()

    security_stub = Blueprint("security", __name__)

    @security_stub.route("/fs-login", endpoint="login")
    def _fs_login():
        return "login", 200

    app.register_blueprint(security_stub)

    dashboard_stub = Blueprint("admin_dashboard", __name__)

    @dashboard_stub.route("/admin", endpoint="admin")
    def _admin_home():
        return "admin", 200

    app.register_blueprint(dashboard_stub)

    with app.app_context():
        db.create_all()
        admin_role = Role(name="admin", description="Administrateur", admin_security=True)
        other_role = Role(name="operateur", description="Exploitant")
        root = User(username="root", email="root@example.test", active=True)
        root.set_password(_ROOT_PWD)
        root.roles.append(admin_role)
        bob = User(username="bob", active=True)
        bob.set_password(_BOB_PWD)
        bob.roles.append(other_role)
        db.session.add_all([admin_role, other_role, root, bob])
        db.session.commit()
    return app


@pytest.fixture
def app(monkeypatch):
    app = _make_app()
    import routes.admin_security as security
    import services.account_recovery_service as service
    monkeypatch.setattr(security, "display_toast", lambda **kwargs: ("", 204))
    monkeypatch.setattr(security, "record_audit", lambda *a, **k: None)
    monkeypatch.setattr(service, "record_audit", lambda *a, **k: None)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _user(app, username):
    """Instantané du compte relu en base (None s'il n'existe pas).

    Pas de contexte d'application tenu ouvert pendant le test : les requêtes
    du client de test y partageraient ``g`` (utilisateur flask-login en cache).
    """
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            return None
        snap = SimpleNamespace(
            id=user.id, username=user.username, active=user.active,
            must_change_password=user.must_change_password,
            fs_uniquifier=user.fs_uniquifier,
            env_reset_marker=user.env_reset_marker,
            roles=[SimpleNamespace(name=r.name) for r in user.roles],
        )

    def verify_password(password):
        with app.app_context():
            return User.query.filter_by(username=username).first().verify_password(password)

    snap.verify_password = verify_password
    return snap


def _client_for(app, username):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(_user(app, username).fs_uniquifier)
    return client


def _set_must_change(app, username, value=True):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        user.must_change_password = value
        db.session.commit()


# ---------------------------------------------------------------------------
# 2. Service : commande de secours
# ---------------------------------------------------------------------------

def test_cli_genere_un_mot_de_passe_temporaire(app):
    from services.account_recovery_service import reset_password_from_cli
    old_uniquifier = _user(app, "bob").fs_uniquifier
    with app.app_context():
        pwd, created = reset_password_from_cli("bob")
    bob = _user(app, "bob")
    assert created is False
    assert bob.verify_password(pwd)
    assert not bob.verify_password(_BOB_PWD)
    assert bob.must_change_password is True
    assert bob.fs_uniquifier != old_uniquifier  # sessions révoquées
    assert [r.name for r in bob.roles] == ["operateur"]  # droits inchangés


def test_cli_mot_de_passe_impose_et_politique(app):
    from services.account_recovery_service import WeakPasswordError, reset_password_from_cli
    with app.app_context():
        with pytest.raises(WeakPasswordError):
            reset_password_from_cli("bob", password="court")
        pwd, _ = reset_password_from_cli("bob", password=_NEW_PWD)
    assert pwd == _NEW_PWD
    assert _user(app, "bob").verify_password(_NEW_PWD)


def test_cli_compte_inconnu(app):
    from services.account_recovery_service import UnknownUserError, reset_password_from_cli
    with app.app_context():
        with pytest.raises(UnknownUserError):
            reset_password_from_cli("fantome")
        assert User.query.filter_by(username="fantome").first() is None


def test_cli_admin_cree_le_compte_avec_le_role_admin(app):
    from services.account_recovery_service import reset_password_from_cli
    with app.app_context():
        pwd, created = reset_password_from_cli("secours", make_admin=True)
    user = _user(app, "secours")
    assert created is True
    assert user.verify_password(pwd)
    assert user.active is True
    assert user.must_change_password is True
    assert [r.name for r in user.roles] == ["admin"]


def test_cli_admin_reactive_et_rend_le_role(app):
    from services.account_recovery_service import reset_password_from_cli
    with app.app_context():
        bob = User.query.filter_by(username="bob").first()
        bob.active = False
        db.session.commit()
        reset_password_from_cli("bob", make_admin=True)
    bob = _user(app, "bob")
    assert bob.active is True
    assert {r.name for r in bob.roles} == {"operateur", "admin"}


def test_cli_admin_recree_le_role_admin_disparu(app):
    from services.account_recovery_service import reset_password_from_cli
    with app.app_context():
        root = User.query.filter_by(username="root").first()
        root.roles = []
        db.session.delete(Role.query.filter_by(name="admin").first())
        db.session.commit()
        reset_password_from_cli("root", make_admin=True)
    root = _user(app, "root")
    assert [r.name for r in root.roles] == ["admin"]


# ---------------------------------------------------------------------------
# 2 bis. Service : ADMIN_RESET_PASSWORD (une seule fois)
# ---------------------------------------------------------------------------

_TEMP = "Secours-temp-2026"


def test_env_absente(app):
    from services.account_recovery_service import apply_env_reset
    with app.app_context():
        assert apply_env_reset({}) == "absent"
        assert apply_env_reset({"ADMIN_RESET_PASSWORD": "  "}) == "absent"
    assert _user(app, "root").verify_password(_ROOT_PWD)


def test_env_appliquee_une_seule_fois(app):
    from services.account_recovery_service import apply_env_reset, set_user_password
    env = {"ADMIN_RESET_PASSWORD": _TEMP, "ADMIN_USERNAME": "root"}
    with app.app_context():
        assert apply_env_reset(env) == "applique"
    root = _user(app, "root")
    assert root.verify_password(_TEMP)
    assert root.must_change_password is True

    # L'utilisateur choisit son mot de passe ; la variable est restée en place.
    with app.app_context():
        root = User.query.filter_by(username="root").first()
        set_user_password(root, _NEW_PWD, must_change=False)
        db.session.commit()
        assert apply_env_reset(env) == "deja_applique"
    assert _user(app, "root").verify_password(_NEW_PWD)  # pas réécrasé

    # Nouvel oubli : nouvelle valeur => réappliquée.
    with app.app_context():
        assert apply_env_reset({**env, "ADMIN_RESET_PASSWORD": "Encore-oublie-2026"}) == "applique"
    assert _user(app, "root").verify_password("Encore-oublie-2026")


def test_env_vise_admin_par_defaut_et_le_cree(app):
    from services.account_recovery_service import apply_env_reset
    with app.app_context():
        assert apply_env_reset({"ADMIN_RESET_PASSWORD": _TEMP}) == "applique"
    admin = _user(app, "admin")
    assert admin.verify_password(_TEMP)
    assert admin.must_change_password is True
    assert [r.name for r in admin.roles] == ["admin"]


def test_env_reactive_et_rend_le_role_admin(app):
    from services.account_recovery_service import apply_env_reset
    with app.app_context():
        bob = User.query.filter_by(username="bob").first()
        bob.active = False
        db.session.commit()
        apply_env_reset({"ADMIN_RESET_PASSWORD": _TEMP, "ADMIN_USERNAME": "bob"})
    bob = _user(app, "bob")
    assert bob.active is True
    assert "admin" in {r.name for r in bob.roles}


def test_env_trop_faible_ignoree_sans_bloquer(app):
    from services.account_recovery_service import apply_env_reset
    with app.app_context():
        assert apply_env_reset({"ADMIN_RESET_PASSWORD": "court",
                                "ADMIN_USERNAME": "root"}) == "refuse"
    root = _user(app, "root")
    assert root.verify_password(_ROOT_PWD)
    assert root.env_reset_marker is None


def test_env_encore_definie():
    from services.account_recovery_service import env_reset_still_set
    assert env_reset_still_set({}) is False
    assert env_reset_still_set({"ADMIN_RESET_PASSWORD": " "}) is False
    assert env_reset_still_set({"ADMIN_RESET_PASSWORD": "x"}) is True


# ---------------------------------------------------------------------------
# 3. Routes : garde + « Mon mot de passe »
# ---------------------------------------------------------------------------

def test_garde_renvoie_vers_mon_mot_de_passe(app):
    _set_must_change(app, "root")
    client = _client_for(app, "root")
    resp = client.get("/admin/security")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/account/password")


def test_garde_htmx_et_json(app):
    _set_must_change(app, "root")
    client = _client_for(app, "root")
    resp = client.get("/admin/security/table", headers={"HX-Request": "true"})
    assert resp.status_code == 204
    assert resp.headers["HX-Redirect"].endswith("/admin/account/password")
    resp = client.post("/admin/security/role_update/1", json={})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "password_change_required"


def test_garde_inactive_sans_mot_de_passe_temporaire(app):
    client = _client_for(app, "root")
    assert client.get("/admin/security/table").status_code == 200


def test_page_accessible_sans_permission(app):
    client = _client_for(app, "bob")  # rôle sans aucune permission
    resp = client.get("/admin/account/password")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="current_password"' in html
    assert "temporaire" not in html


def test_changement_volontaire_exige_le_mot_de_passe_actuel(app):
    client = _client_for(app, "bob")
    resp = client.post("/admin/account/password", data={
        "current_password": "faux", "password1": _NEW_PWD, "password2": _NEW_PWD})
    assert resp.status_code == 400
    assert "actuel est incorrect" in resp.get_data(as_text=True)
    assert _user(app, "bob").verify_password(_BOB_PWD)

    resp = client.post("/admin/account/password", data={
        "current_password": _BOB_PWD, "password1": _NEW_PWD, "password2": _NEW_PWD})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/account/password?done=1")
    assert _user(app, "bob").verify_password(_NEW_PWD)
    # La session courante reste valide (ré-émise) et affiche la confirmation.
    resp = client.get("/admin/account/password?done=1")
    assert resp.status_code == 200
    assert "a été changé" in resp.get_data(as_text=True)


def test_changement_obligatoire_sans_mot_de_passe_actuel(app):
    _set_must_change(app, "root")
    client = _client_for(app, "root")
    html = client.get("/admin/account/password").get_data(as_text=True)
    assert "temporaire" in html
    assert 'name="current_password"' not in html

    resp = client.post("/admin/account/password", data={
        "password1": _NEW_PWD, "password2": _NEW_PWD})
    assert resp.status_code == 302
    root = _user(app, "root")
    assert root.verify_password(_NEW_PWD)
    assert root.must_change_password is False
    # Le reste de l'administration est de nouveau accessible.
    assert client.get("/admin/security/table").status_code == 200


def test_changement_obligatoire_refuse_le_meme_mot_de_passe(app):
    _set_must_change(app, "root")
    client = _client_for(app, "root")
    resp = client.post("/admin/account/password", data={
        "password1": _ROOT_PWD, "password2": _ROOT_PWD})
    assert resp.status_code == 400
    assert "différent" in resp.get_data(as_text=True)
    assert _user(app, "root").must_change_password is True


def test_changement_applique_la_politique_et_la_confirmation(app):
    client = _client_for(app, "bob")
    resp = client.post("/admin/account/password", data={
        "current_password": _BOB_PWD, "password1": "court", "password2": "court"})
    assert resp.status_code == 400
    assert "10 caractères" in resp.get_data(as_text=True)
    resp = client.post("/admin/account/password", data={
        "current_password": _BOB_PWD, "password1": _NEW_PWD, "password2": _NEW_PWD + "x"})
    assert resp.status_code == 400
    assert "ne correspondent pas" in resp.get_data(as_text=True)


def test_changement_revoque_les_autres_sessions(app):
    other_session = _client_for(app, "bob")
    client = _client_for(app, "bob")
    client.post("/admin/account/password", data={
        "current_password": _BOB_PWD, "password1": _NEW_PWD, "password2": _NEW_PWD})
    # L'ancien identifiant de session ne charge plus l'utilisateur.
    resp = other_session.get("/admin/account/password")
    assert resp.status_code == 302
    assert "login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# 3 bis. Réinitialisation par un administrateur / création de compte
# ---------------------------------------------------------------------------

def _admin_reset(client, app, username, **extra):
    user_id = _user(app, username).id
    data = {"password1": _NEW_PWD, "password2": _NEW_PWD, **extra}
    return client.post(f"/admin/security/update_password/{user_id}", data=data)


def test_reinitialisation_par_admin_temporaire_par_defaut(app):
    client = _client_for(app, "root")
    _admin_reset(client, app, "bob")
    bob = _user(app, "bob")
    assert bob.verify_password(_NEW_PWD)
    assert bob.must_change_password is True


def test_reinitialisation_par_admin_case_decochee(app):
    client = _client_for(app, "root")
    _admin_reset(client, app, "bob", must_change="false")
    assert _user(app, "bob").must_change_password is False


def test_son_propre_mot_de_passe_jamais_temporaire(app):
    client = _client_for(app, "root")
    _admin_reset(client, app, "root", must_change="true")
    assert _user(app, "root").must_change_password is False


def test_modale_admin_propose_la_case(app):
    client = _client_for(app, "root")
    bob_id = _user(app, "bob").id
    html = client.get(f"/admin/security/change_password/{bob_id}").get_data(as_text=True)
    assert 'id="pwd_change_must" checked' in html
    assert 'data-param-must_change="#pwd_change_must@checked"' in html
    root_id = _user(app, "root").id
    html = client.get(f"/admin/security/change_password/{root_id}").get_data(as_text=True)
    assert "pwd_change_must" not in html


def test_creation_de_compte_avec_changement_obligatoire(app):
    client = _client_for(app, "root")
    with app.app_context():
        operateur_id = Role.query.filter_by(name="operateur").first().id
    base = {"password1": _NEW_PWD, "password2": _NEW_PWD, "role_ids": f"[{operateur_id}]"}
    client.post("/admin/security/add_new_user", data={**base, "username": "alice"})
    client.post("/admin/security/add_new_user",
                data={**base, "username": "carole", "must_change": "false"})
    assert _user(app, "alice").must_change_password is True
    assert _user(app, "carole").must_change_password is False


# ---------------------------------------------------------------------------
# 4. Câblage statique
# ---------------------------------------------------------------------------

def test_app_branche_la_garde_dans_la_zone_admin():
    source = _read("app.py")
    m = re.search(r"def require_login_for_admin\(\):(.*?)elif request.path.startswith\('/spotify'\)",
                  source, re.DOTALL)
    assert m, "require_login_for_admin introuvable"
    body = m.group(1)
    assert body.index("_deny_unauthenticated_access()") < body.index(
        "must_change_password_blocks(request.endpoint)")
    assert "return password_change_required_response()" in body


def test_app_applique_la_variable_au_bootstrap():
    source = _read("app.py")
    m = re.search(r"if run_bootstrap:(.*?)init_days_of_week_db_from_json", source, re.DOTALL)
    assert m
    assert m.group(1).index("create_default_user()") < m.group(1).index("apply_env_reset()")


def test_connexion_renvoie_vers_le_changement_obligatoire():
    source = _read("routes/admin_security.py")
    m = re.search(r"def login\(\):(.*?)\n# Hash bcrypt figé", source, re.DOTALL)
    body = m.group(1)
    assert "if user.must_change_password:" in body
    assert body.index("login_user(user") < body.index("if user.must_change_password:")
    assert "url_for('admin_security.account_password')" in body


def test_compose_coolify_transmet_la_variable():
    compose = _read("docker-compose.coolify.yaml")
    for service in ("init", "web"):
        m = re.search(rf"\n  {service}:\n(.*?)\n  \w+:\n", compose, re.DOTALL)
        assert m, service
        assert "ADMIN_RESET_PASSWORD: ${ADMIN_RESET_PASSWORD:-}" in m.group(1), service


def test_manage_expose_les_commandes_de_secours():
    import manage
    source = _read("manage.py")
    assert '"reset-password"' in source and '"list-users"' in source
    with pytest.raises(SystemExit):
        manage.main(["reset-password", "--help"])


def test_lien_mon_mot_de_passe_et_bandeau_dans_base():
    base = _read("templates/admin/base.html")
    assert 'href="/admin/account/password"' in base
    assert "account_reset_env_set()" in base


def test_aide_mot_de_passe_oublie_sur_la_connexion():
    login = _read("templates/security/login.html")
    assert "Mot de passe oublié" in login
