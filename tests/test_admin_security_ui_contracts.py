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

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager

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
    )
    db.init_app(app)

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
    payload = {"username": "alice", "email": "", "role_id": "1",
               "password1": "Alice-pass-2026!", "password2": "Alice-pass-2026!"}
    payload.update(fields)
    return client.post("/admin/security/add_new_user", data=payload)


def test_add_user_username_vide_refuse(client, app):
    with app.app_context():
        avant = User.query.count()
    resp = _post_user(client, username="   ")
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.count() == avant


def test_add_user_role_vide_refuse(client, app):
    resp = _post_user(client, role_id="")
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(username="alice").first() is None


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


def test_users_table_role_select_est_prefixe():
    tpl = _read("templates/admin/security_htmx_table.html")
    assert 'id="user-role-' in tpl
    assert 'id="role-' not in tpl
    assert 'data-param-role_id="#user-role-' in tpl


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
    payload = {"username": "root", "email": "", "role_id": "1"}
    payload.update(fields)
    return client.post(f"/admin/security/user_update/{user_id}", data=payload)


def test_delete_role_refuse_un_role_assigne(client, app):
    rid = _role_id(app, "admin")
    resp = client.delete(f"/admin/security/delete_role/{rid}")
    assert resp.status_code == 200  # fragment de table (HTMX), pas de JSON
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
                             role_id=str(_role_id(app, "operateur")))
    assert resp.status_code == 200  # fragment de table (HTMX), pas de JSON
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
                             role_id=str(_role_id(app, "operateur")))
    assert resp.status_code == 200
    assert _user_role_names(app, root_id) == ["operateur"]


def test_delete_user_refuse_le_dernier_capable(client, app):
    root_id = _user_id(app, "root")
    resp = client.post(f"/admin/security/delete_user/{root_id}")
    assert resp.status_code == 200  # fragment de table (HTMX), pas de JSON
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
                             role_id=str(_role_id(app, "operateur")))
    assert resp.status_code == 200
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
