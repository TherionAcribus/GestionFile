"""Contrats IHM de la page Admin > Sécurité (utilisateurs / rôles).

Deux familles de vérifications :

1. **Contrats de réponse** des routes ``role_update`` et ``save_role``, sur une
   application Flask minimale + SQLite en mémoire (même patron que
   ``test_session_revocation.py``) : validation stricte du dictionnaire de
   permissions (liste blanche ``PERMISSION_FIELDS`` + valeurs strictement
   booléennes), validation du nom, et formes de réponse compatibles avec le
   câblage HTMX (JSON d'erreur pour ``htmx:responseError``, ``("", 204)`` plutôt
   qu'un corps vide en 200 qui viderait la table cible, fragment + swap-oob en
   cas de succès).

2. **Assertions statiques** sur les gabarits (même style que
   ``test_login_route_hardening.py``) : identifiants HTML dédupliqués entre les
   onglets et formulaires (les collecteurs ``data-param-*`` lisaient le mauvais
   élément), suppression de la « voie de garage » ``#invisible`` et cible/swap
   corrects des boutons d'enregistrement.
"""

import json
import os
import re
import types

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager, login_user

from models import db, Role, User

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _make_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    app.config.update(
        # Pas de SQLALCHEMY_BINDS ici : aucun modèle n'a de __bind_key__, et un
        # bind fantôme resterait dans db.metadatas (extension partagée) et
        # casserait les db.create_all() des tests collectés après celui-ci.
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-security-contracts",
        # Les tests connectent l'utilisateur via session['_user_id'] directement ;
        # la protection de session exigerait aussi le '_id' posé par login_user.
        SESSION_PROTECTION="none",
        # Réglages rendus par l'onglet « Général » de la page Sécurité.
        SECURITY_LOGIN_ADMIN=True,
        SECURITY_LOGIN_COUNTER=True,
        SECURITY_LOGIN_SCREEN=True,
        SECURITY_LOGIN_PATIENT=True,
        SECURITY_REMEMBER_DURATION=30,
    )
    db.init_app(app)

    # La page /admin/security étend base.html, qui appelle csrf_token()
    # (Flask-WTF absent de l'app minimale) et user_has_permission() (context
    # processor enregistré par l'app réelle).
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"

    @app.context_processor
    def _inject_security_helpers():
        from routes.admin_security import user_has_permission
        return {"user_has_permission": user_has_permission}

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=str(user_id)).first()
        if user and user.active:
            return user
        return None

    from routes.admin_security import admin_security_bp
    app.register_blueprint(admin_security_bp, url_prefix="")

    # Stub de l'endpoint Flask-Security (security.login), cible des redirections
    # anonymes de require_permission.
    security_stub = Blueprint("security", __name__)

    @security_stub.route("/login", endpoint="login")
    def _fs_login():
        return "login", 200

    app.register_blueprint(security_stub)

    with app.app_context():
        db.create_all()
        admin_role = Role(name="admin", description="Administrateur",
                          admin_security=True)
        other_role = Role(name="operateur", description="Exploitant")
        root = User(username="root", email="root@example.test", active=True)
        root.set_password("Root-pass-2026!")
        root.roles.append(admin_role)
        db.session.add_all([admin_role, other_role, root])
        db.session.commit()
    return app


@pytest.fixture
def app(monkeypatch):
    app = _make_app()
    import routes.admin_security as security
    # Le toast part par WebSocket/HX-Trigger en production : inopérant ici.
    monkeypatch.setattr(security, "display_toast", lambda **kwargs: ("", 204))
    monkeypatch.setattr(security, "record_audit", lambda *a, **k: None)
    return app


@pytest.fixture
def client(app):
    client = app.test_client()
    with app.app_context():
        uniquifier = User.query.filter_by(username="root").first().fs_uniquifier
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uniquifier)
    return client


def _role_id(app, name):
    with app.app_context():
        return Role.query.filter_by(name=name).first().id


def _role(app, role_id):
    with app.app_context():
        return db.session.get(Role, role_id)


def _role_count(app):
    with app.app_context():
        return Role.query.count()


def _post_update(client, role_id, **fields):
    payload = {"name": "Gestion", "description": "d", "permissions": "{}"}
    payload.update(fields)
    return client.post(f"/admin/security/role_update/{role_id}", data=payload)


# ---------------------------------------------------------------------------
# role_update : validation stricte des permissions et du nom
# ---------------------------------------------------------------------------

def test_role_update_applique_les_permissions_booleennes(client, app):
    rid = _role_id(app, "operateur")
    resp = _post_update(
        client, rid,
        permissions=json.dumps({"admin_queue": True, "admin_staff": False}))
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}
    role = _role(app, rid)
    assert role.admin_queue is True
    assert role.admin_staff is False
    assert role.name == "Gestion"


def test_role_update_rejette_une_cle_inconnue(client, app):
    resp = _post_update(
        client, _role_id(app, "operateur"),
        permissions=json.dumps({"admin_queue": True, "superuser": True}))
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_role_update_rejette_une_valeur_non_booleenne(client, app):
    # "false" en chaîne : bool("false") valait True sous l'ancien code.
    resp = _post_update(
        client, _role_id(app, "operateur"),
        permissions=json.dumps({"admin_queue": "false"}))
    assert resp.status_code == 400


def test_role_update_rejette_permissions_non_dict(client, app):
    # '["admin_queue"]' décodé en liste : .items() levait une 500 avant.
    resp = _post_update(
        client, _role_id(app, "operateur"),
        permissions='["admin_queue"]')
    assert resp.status_code == 400


def test_role_update_rejette_un_nom_vide(client, app):
    resp = _post_update(client, _role_id(app, "operateur"), name="   ")
    assert resp.status_code == 400


def test_role_update_rejette_un_nom_duplique(client, app):
    resp = _post_update(client, _role_id(app, "operateur"), name="admin")
    assert resp.status_code == 400


def test_role_update_permissions_ne_peuvent_pas_ecraser_description(client, app):
    rid = _role_id(app, "operateur")
    resp = _post_update(
        client, rid,
        permissions=json.dumps({"description": "pirate", "admin_queue": True}))
    assert resp.status_code == 400
    assert _role(app, rid).description == "Exploitant"


# ---------------------------------------------------------------------------
# save_role : formes de réponse compatibles avec hx-target="#div_role_table"
# ---------------------------------------------------------------------------

def _post_save(client, **fields):
    payload = {"name": "Nouveau rôle", "description": "d",
               "permissions": "{}"}
    payload.update(fields)
    return client.post("/admin/security/save_role", data=payload)


def test_save_role_succes_renvoi_table_et_oob(client, app):
    resp = _post_save(
        client, permissions=json.dumps({"admin_queue": True}))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Nouveau rôle" in body
    assert 'hx-swap-oob="innerHTML:#div_add_role_form"' in body
    with app.app_context():
        role = Role.query.filter_by(name="Nouveau rôle").first()
    assert role is not None and role.admin_queue is True


def test_save_role_nom_vide_renvoi_204_sans_creer(client, app):
    avant = _role_count(app)
    resp = _post_save(client, name="   ")
    assert resp.status_code == 204
    assert resp.data == b""
    assert _role_count(app) == avant


def test_save_role_nom_duplique_renvoi_204_sans_creer(client, app):
    avant = _role_count(app)
    resp = _post_save(client, name="admin")
    assert resp.status_code == 204
    assert resp.data == b""
    assert _role_count(app) == avant


@pytest.mark.parametrize("permissions", [
    "{pas du json",
    '["admin_queue"]',
    json.dumps({"admin_queue": "oui"}),
    json.dumps({"admin_queue": True, "name": "pirate"}),
])
def test_save_role_permissions_invalides_renvoi_204(client, app, permissions):
    avant = _role_count(app)
    resp = _post_save(client, permissions=permissions)
    assert resp.status_code == 204
    assert resp.data == b""
    assert _role_count(app) == avant


# ---------------------------------------------------------------------------
# add_new_user : validations serveur (champs normalisés, rôle requis)
# ---------------------------------------------------------------------------

def _post_user(client, **fields):
    payload = {"username": "alice", "email": "", "role_ids": json.dumps([1]),
               "password1": "Alice-pass-2026!", "password2": "Alice-pass-2026!"}
    payload.update(fields)
    return client.post("/admin/security/add_new_user", data=payload)


def test_add_user_username_vide_refuse(client, app):
    with app.app_context():
        avant = User.query.count()
    resp = _post_user(client, username="   ")
    # Échec : 204 + corps vide -> pas de swap HTMX (saisies préservées).
    assert resp.status_code == 204
    assert resp.data == b""
    with app.app_context():
        assert User.query.count() == avant


def test_add_user_role_vide_refuse(client, app):
    resp = _post_user(client, role_ids="[]")
    assert resp.status_code == 204
    assert resp.data == b""
    with app.app_context():
        assert User.query.filter_by(username="alice").first() is None


def test_add_user_multi_roles(client, app):
    admin_rid = _role_id(app, "admin")
    op_rid = _role_id(app, "operateur")
    resp = _post_user(client, role_ids=json.dumps([admin_rid, op_rid]))
    assert resp.status_code == 200
    alice_id = _user_id(app, "alice")
    assert sorted(_user_role_names(app, alice_id)) == ["admin", "operateur"]


def test_add_user_email_vide_devient_null(client, app):
    resp = _post_user(client)
    assert resp.status_code == 200
    with app.app_context():
        alice = User.query.filter_by(username="alice").first()
    assert alice is not None
    assert alice.email is None  # '' casserait la contrainte unique au 2e sans email


# ---------------------------------------------------------------------------
# Assertions statiques : identifiants et cibles des gabarits
# ---------------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _ids(template):
    return set(re.findall(r'id="([^"]+)"', template))


def test_users_table_roles_en_cases_et_switch_actif():
    tpl = _read("templates/admin/security_htmx_table.html")
    assert "user-role-checkbox" in tpl
    assert "user-active-" in tpl
    assert "data-param-role_id" not in tpl


def test_add_user_form_roles_en_cases():
    tpl = _read("templates/admin/security_add_user_form.html")
    assert "user-role-checkbox" in tpl
    assert "data-param-role_id" not in tpl


def test_user_save_cible_la_table_et_pas_invisible():
    tpl = _read("templates/admin/security_htmx_table.html")
    assert 'hx-target="#div_user_table"' in tpl
    assert "#invisible" not in tpl


def test_role_table_scope_et_swap():
    tpl = _read("templates/admin/security_htmx_role_table.html")
    assert 'id="role-item-' in tpl
    assert 'data-params-scope="#role-item-' in tpl
    assert 'hx-swap="none"' in tpl
    assert "#invisible" not in tpl


def test_change_password_et_add_user_sans_id_commun():
    ids_pwd = _ids(_read("templates/admin/security_change_password.html"))
    ids_add = _ids(_read("templates/admin/security_add_user_form.html"))
    assert ids_pwd.isdisjoint(ids_add), (
        f"identifiants partagés : {sorted(ids_pwd & ids_add)}")


def test_invisible_absent_des_onglets():
    for rel in ("templates/admin/security_roles.html",
                "templates/admin/security_user.html"):
        assert "invisible" not in _read(rel), rel


def test_invisible_present_une_seule_fois_dans_la_page():
    # Requis par les macros partagées (switch de l'onglet Général) ; unique pour
    # éviter les cibles htmx ambiguës.
    assert _read("templates/admin/security.html").count('id="invisible"') == 1


def test_add_user_form_un_seul_tr_dans_thead():
    tpl = _read("templates/admin/security_add_user_form.html")
    thead = re.search(r"<thead>(.*?)</thead>", tpl, re.DOTALL)
    assert thead
    assert thead.group(1).count("<tr") == 1


# ---------------------------------------------------------------------------
# Invariant « dernier accès Sécurité » : il doit toujours rester au moins un
# utilisateur ACTIF dont un rôle accorde admin_security (remplace l'ancienne
# convention « rôle nommé admin »), et rôle assigné non supprimable.
# ---------------------------------------------------------------------------

def _add_role(app, name, **permissions):
    with app.app_context():
        role = Role(name=name, description=name)
        for key, value in permissions.items():
            setattr(role, key, value)
        db.session.add(role)
        db.session.commit()
        return role.id


def _add_user(app, username, role_id=None, active=True):
    with app.app_context():
        user = User(username=username, email=None)
        user.set_password("Motdepasse-2026!")  # >= 10 caractères
        user.active = active
        if role_id is not None:
            user.roles.append(db.session.get(Role, role_id))
        db.session.add(user)
        db.session.commit()
        return user.id


def _user_id(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).first().id


def _user(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id)


def _user_role_names(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        return None if user is None else [r.name for r in user.roles]


def _post_user_update(client, user_id, **fields):
    payload = {"username": "root", "email": "", "role_ids": json.dumps([1])}
    payload.update(fields)
    return client.post(f"/admin/security/user_update/{user_id}", data=payload)


def test_delete_role_refuse_un_role_assigne(client, app):
    rid = _role_id(app, "admin")
    resp = client.delete(f"/admin/security/delete_role/{rid}")
    # Refus : 204, la modale reste ouverte (pas de swap).
    assert resp.status_code == 204
    assert resp.data == b""
    assert _role(app, rid) is not None


def test_delete_role_supprime_un_role_non_assigne(client, app):
    rid = _add_role(app, "temp")
    resp = client.delete(f"/admin/security/delete_role/{rid}")
    assert resp.status_code == 200
    assert _role(app, rid) is None


def test_role_update_refuse_de_retirer_la_derniere_permission_securite(client, app):
    rid = _role_id(app, "admin")
    resp = _post_update(client, rid, name="admin",
                        permissions=json.dumps({"admin_security": False}))
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert _role(app, rid).admin_security is True


def test_role_update_autorise_le_retrait_si_un_autre_capable(client, app):
    admin_rid = _role_id(app, "admin")
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    bob_id = _add_user(app, "bob", sec2_rid)
    resp = _post_update(client, admin_rid, name="admin",
                        permissions=json.dumps({"admin_security": False}))
    assert resp.status_code == 200
    assert _role(app, admin_rid).admin_security is False
    with app.app_context():
        bob = db.session.get(User, bob_id)
        assert any(r.admin_security for r in bob.roles)


def test_role_update_refuse_de_renommer_admin(client, app):
    rid = _role_id(app, "admin")
    resp = _post_update(client, rid, name="boss", permissions="{}")
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert _role(app, rid).name == "admin"


def test_user_update_refuse_de_demoir_le_dernier_capable(client, app):
    root_id = _user_id(app, "root")
    resp = _post_user_update(client, root_id,
                             role_ids=json.dumps([_role_id(app, "operateur")]))
    assert resp.status_code == 204  # refus : pas de swap, saisies préservées
    assert resp.data == b""
    assert _user_role_names(app, root_id) == ["admin"]


def test_user_update_autorise_la_demotion_si_un_autre_capable(client, app, monkeypatch):
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    _add_user(app, "bob", sec2_rid)
    root_id = _user_id(app, "root")
    # Une fois démoti, root n'a plus la permission : le rendu de la table en
    # fin de vue renverrait l'écran de refus (csrf_token absent de l'app
    # minimale) — hors sujet ici, on vérifie la mutation elle-même.
    import routes.admin_security as security
    monkeypatch.setattr(security, "display_security_table", lambda: "TABLE")
    resp = _post_user_update(client, root_id,
                             role_ids=json.dumps([_role_id(app, "operateur")]))
    assert resp.status_code == 200
    assert _user_role_names(app, root_id) == ["operateur"]


def test_delete_user_refuse_le_dernier_capable(client, app):
    root_id = _user_id(app, "root")
    resp = client.post(f"/admin/security/delete_user/{root_id}")
    assert resp.status_code == 204  # refus : la modale reste ouverte
    assert resp.data == b""
    assert _user(app, root_id) is not None


def test_delete_user_autorise_si_un_autre_capable(client, app):
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    _add_user(app, "bob", sec2_rid)
    root_id = _user_id(app, "root")
    resp = client.post(f"/admin/security/delete_user/{root_id}")
    assert resp.status_code == 200
    assert _user(app, root_id) is None


def test_delete_user_inactif_capable_autorise(client, app):
    # Supprimer un utilisateur capable mais INACTIF ne touche pas à l'invariant
    # (seuls les actifs comptent) : autorisé même si root est le seul capable actif.
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    bob_id = _add_user(app, "bob", sec2_rid, active=False)
    resp = client.post(f"/admin/security/delete_user/{bob_id}")
    assert resp.status_code == 200
    assert _user(app, bob_id) is None


def test_user_update_ne_compte_que_les_utilisateurs_actifs(client, app):
    # bob possède la permission Sécurité mais est inactif : il ne compte pas.
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    _add_user(app, "bob", sec2_rid, active=False)
    root_id = _user_id(app, "root")
    resp = _post_user_update(client, root_id,
                             role_ids=json.dumps([_role_id(app, "operateur")]))
    assert resp.status_code == 204
    assert resp.data == b""
    assert _user_role_names(app, root_id) == ["admin"]  # toujours refusé


def test_role_update_retire_une_permission_hors_securite(client, app):
    # Régression : retirer admin_queue au dernier capable ne touche pas à
    # l'invariant Sécurité → autorisé.
    rid = _role_id(app, "admin")
    with app.app_context():
        db.session.get(Role, rid).admin_queue = True
        db.session.commit()
    resp = _post_update(client, rid, name="admin",
                        permissions=json.dumps({"admin_queue": False}))
    assert resp.status_code == 200
    role = _role(app, rid)
    assert role.admin_queue is False
    assert role.admin_security is True


# ---------------------------------------------------------------------------
# Multi-rôle, cycle de vie actif/inactif et refus de connexion des suspendus
# ---------------------------------------------------------------------------

def _uniquifier(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id).fs_uniquifier


def test_user_update_assigne_plusieurs_roles(client, app):
    op_rid = _role_id(app, "operateur")
    sec2_rid = _add_role(app, "sec2", admin_security=True)
    bob_id = _add_user(app, "bob", op_rid)
    resp = _post_user_update(client, bob_id, username="bob",
                             role_ids=json.dumps([op_rid, sec2_rid]))
    assert resp.status_code == 200
    assert sorted(_user_role_names(app, bob_id)) == ["operateur", "sec2"]


def test_user_update_sans_role_refuse(client, app):
    root_id = _user_id(app, "root")
    resp = _post_user_update(client, root_id, role_ids="[]")
    assert resp.status_code == 204
    assert resp.data == b""
    assert _user_role_names(app, root_id) == ["admin"]  # inchangés


def test_user_update_role_inconnu_refuse(client, app):
    root_id = _user_id(app, "root")
    resp = _post_user_update(client, root_id, role_ids="[999]")
    assert resp.status_code == 204
    assert resp.data == b""
    assert _user_role_names(app, root_id) == ["admin"]  # inchangés


def test_user_update_desactivation_revoque_les_sessions(client, app):
    op_rid = _role_id(app, "operateur")
    bob_id = _add_user(app, "bob", op_rid)
    avant = _uniquifier(app, bob_id)
    resp = _post_user_update(client, bob_id, username="bob",
                             role_ids=json.dumps([op_rid]), active="false")
    assert resp.status_code == 200
    with app.app_context():
        bob = db.session.get(User, bob_id)
        assert bob.active is False
        # fs_uniquifier tourné -> session + cookie remember révoqués.
        assert bob.fs_uniquifier != avant


def test_user_update_desactiver_le_dernier_capable_refuse(client, app):
    root_id = _user_id(app, "root")
    admin_rid = _role_id(app, "admin")
    resp = _post_user_update(client, root_id,
                             role_ids=json.dumps([admin_rid]), active="false")
    assert resp.status_code == 204
    assert resp.data == b""
    assert _user(app, root_id).active is True


def test_update_password_ne_reactive_pas_un_compte_inactif(client, app):
    # set_password est découplé du cycle de vie : un admin peut définir le mot
    # de passe d'un compte suspendu sans le réactiver.
    op_rid = _role_id(app, "operateur")
    bob_id = _add_user(app, "bob", op_rid, active=False)
    avant = _uniquifier(app, bob_id)
    resp = client.post(f"/admin/security/update_password/{bob_id}",
                       data={"password1": "Nouveau-pass-2026!",
                             "password2": "Nouveau-pass-2026!"})
    assert resp.status_code == 200
    with app.app_context():
        bob = db.session.get(User, bob_id)
        assert bob.active is False
        assert bob.fs_uniquifier != avant
        assert bob.verify_password("Nouveau-pass-2026!")


# ---------------------------------------------------------------------------
# login : un compte inactif est refusé comme un mot de passe erroné
# ---------------------------------------------------------------------------

class _StubField:
    """Champ WTForms minimal : ``.data`` pour la vue, rendu neutre pour le
    gabarit de connexion (``label``, ``errors``, appel)."""
    def __init__(self, data=""):
        self.data = data
        self.label = ""
        self.errors = []

    def __call__(self, **kwargs):
        return ""

    def __str__(self):
        return ""


def _post_login(app, monkeypatch, username, password):
    """POST /login avec ``ExtendedLoginForm`` simulé : la validation WTForms /
    CSRF est hors sujet ici, on teste la décision de la route."""
    import routes.admin_security as security
    form = types.SimpleNamespace(
        username=_StubField(username),
        password=_StubField(password),
        remember=_StubField(False),
        next=_StubField(""),
        errors={},
        validate_on_submit=lambda: True,
        hidden_tag=lambda: "",
    )
    monkeypatch.setattr(security, "ExtendedLoginForm", lambda: form)
    # flask_security.login_user exige l'extension Security (absente de l'app
    # minimale) ; flask_login.login_user fait exactement le nécessaire ici.
    monkeypatch.setattr(security, "login_user", login_user)
    client = app.test_client()
    resp = client.post("/login", data={"username": username, "password": password})
    return client, resp


def test_login_refuse_un_compte_inactif(app, monkeypatch):
    op_rid = _role_id(app, "operateur")
    _add_user(app, "bob", op_rid, active=False)
    client, resp = _post_login(app, monkeypatch, "bob", "Motdepasse-2026!")
    # Mot de passe correct mais compte suspendu : même refus générique, page
    # de connexion re-rendue (pas de redirection, pas de session ouverte).
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is None


def test_login_accepte_un_compte_actif(app, monkeypatch):
    op_rid = _role_id(app, "operateur")
    _add_user(app, "bob", op_rid, active=True)
    client, resp = _post_login(app, monkeypatch, "bob", "Motdepasse-2026!")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None


# ---------------------------------------------------------------------------
# Contrat succès/échec HTMX : échec -> ("", 204) sans swap (la modale et les
# saisies sont préservées) ; succès -> 200 + fragment de table.
# ---------------------------------------------------------------------------

def test_update_password_mismatch_renvoi_204(client, app):
    op_rid = _role_id(app, "operateur")
    bob_id = _add_user(app, "bob", op_rid)
    resp = client.post(f"/admin/security/update_password/{bob_id}",
                       data={"password1": "Nouveau-pass-2026!",
                             "password2": "different-pass-2026!"})
    assert resp.status_code == 204
    assert resp.data == b""
    with app.app_context():
        # Mot de passe inchangé : la saisie est préservée côté client.
        assert db.session.get(User, bob_id).verify_password("Motdepasse-2026!")


def test_update_password_champs_vides_renvoi_204(client, app):
    op_rid = _role_id(app, "operateur")
    bob_id = _add_user(app, "bob", op_rid)
    resp = client.post(f"/admin/security/update_password/{bob_id}",
                       data={"password1": "", "password2": ""})
    assert resp.status_code == 204
    assert resp.data == b""


def test_delete_user_inconnu_renvoi_204(client, app):
    resp = client.post("/admin/security/delete_user/9999")
    assert resp.status_code == 204
    assert resp.data == b""


# ---------------------------------------------------------------------------
# Onglet actif rendu côté serveur (?tab=…)
# ---------------------------------------------------------------------------

def _pane_tag(body, pane_id):
    m = re.search(r'<div[^>]*id="' + pane_id + r'"[^>]*>', body)
    assert m, f"pane {pane_id} introuvable"
    return m.group(0)


def test_page_securite_onglet_roles_actif_via_url(client):
    resp = client.get("/admin/security?tab=roles")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "show active" in _pane_tag(body, "tab_roles")
    btn = re.search(r'<button[^>]*id="roles-tab"[^>]*>', body)
    assert btn and "active" in btn.group(0)
    assert 'aria-selected="true"' in btn.group(0)
    # Les autres onglets restent inactifs.
    assert "show active" not in _pane_tag(body, "tab_general")


def test_page_securite_onglet_inconnu_replie_sur_general(client):
    resp = client.get("/admin/security?tab=bogus")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "show active" in _pane_tag(body, "tab_general")
    assert "show active" not in _pane_tag(body, "tab_roles")
    assert "show active" not in _pane_tag(body, "tab_users")


def test_page_securite_onglet_users_actif_via_url(client):
    resp = client.get("/admin/security?tab=users")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "show active" in _pane_tag(body, "tab_users")
    btn = re.search(r'<button[^>]*id="users-tab"[^>]*>', body)
    assert btn and "active" in btn.group(0)


# ---------------------------------------------------------------------------
# Modale de confirmation : impact visible avant suppression
# ---------------------------------------------------------------------------

def test_confirm_delete_role_signale_les_utilisateurs_assignes(client, app):
    # Le rôle « admin » est porté par root : la modale annonce l'impact et le
    # refus à venir (le serveur refuse désormais de supprimer un rôle assigné).
    rid = _role_id(app, "admin")
    resp = client.get(f"/admin/security/confirm_delete_role/{rid}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "attribué" in body
    assert "1 utilisateur" in body
    assert "alert-warning" in body


def test_confirm_delete_user_liste_les_roles(client, app):
    root_id = _user_id(app, "root")
    resp = client.get(f"/admin/security/confirm_delete_user/{root_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Rôles" in body
    assert "admin" in body


# ---------------------------------------------------------------------------
# Assertions statiques : fermeture de modale par succès, aides de saisie,
# badges de risque, boutons d'ouverture en <button>
# ---------------------------------------------------------------------------

def test_boutons_action_des_modales_ne_ferment_pas_en_avance():
    # Un échec renvoie ("", 204) sans swap : si le bouton d'action portait
    # data-bs-dismiss, la modale se fermerait AVANT le résultat. Seul un 200
    # (succès) la ferme — voir le handler htmx:afterRequest de admin.js.
    for rel, label in [
        ("templates/admin/security_modal_confirm_delete_user.html", "Supprimer"),
        ("templates/admin/security_modal_confirm_delete_role.html", "Supprimer"),
        ("templates/admin/security_change_password.html", "Enregistrer"),
    ]:
        tpl = _read(rel)
        m = re.search(r'<button[^>]*>\s*' + label + r'\s*</button>', tpl, re.DOTALL)
        assert m, f"{rel}: bouton {label} introuvable"
        assert 'data-bs-dismiss' not in m.group(0), rel
        # L'annulation reste une fermeture pure (elle n'émet pas de requête).
        assert tpl.count('data-bs-dismiss="modal"') == 1, rel


def test_actions_des_modales_conservent_l_etat_de_la_toolbar():
    # Les requêtes mutantes re-rendent la table avec l'état courant de la barre
    # d'outils (recherche/taille de page/tri) au lieu de retomber sur la 1re page.
    for rel, prefix in [
        ("templates/admin/security_modal_confirm_delete_user.html", "users"),
        ("templates/admin/security_change_password.html", "users"),
        ("templates/admin/security_modal_confirm_delete_role.html", "roles"),
        ("templates/admin/security_add_role_form.html", "roles"),
    ]:
        tpl = _read(rel)
        for field in ("search", "per_page", "sort", "dir"):
            assert f'data-param-{field}="#{prefix}-{field}"' in tpl, (
                f"{rel}: data-param-{field} manquant")
    tpl = _read("templates/admin/security_htmx_table.html")
    for field in ("search", "per_page", "sort", "dir"):
        assert f'data-param-{field}="#users-{field}"' in tpl, (
            f"security_htmx_table.html: data-param-{field} manquant")


def test_change_password_propose_un_toggle_de_visibilite():
    tpl = _read("templates/admin/security_change_password.html")
    assert 'data-toggle-password="#pwd_change_1"' in tpl
    assert 'data-toggle-password="#pwd_change_2"' in tpl
    assert tpl.count("input-group") >= 2


def test_permissions_a_risque_badgees_critique():
    for rel in ("templates/admin/security_htmx_role_table.html",
                "templates/admin/security_add_role_form.html"):
        tpl = _read(rel)
        assert 'perm.risk == "high"' in tpl, rel
        assert "Critique" in tpl, rel
        assert "badge" in tpl, rel


def test_ouvreurs_de_formulaires_sont_des_boutons():
    # Un <a> sans href n'est pas un bouton (ni focus clavier, ni sémantique).
    for rel in ("templates/admin/security_user.html",
                "templates/admin/security_roles.html"):
        tpl = _read(rel)
        assert '<button type="button" class="btn btn-primary"' in tpl, rel
        assert '<a class="btn btn-primary"' not in tpl, rel
