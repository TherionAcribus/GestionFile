"""« Mot de passe oublié » par e-mail (routes/password_reset.py).

Application Flask minimale + SQLite en mémoire ; flask-mailman en mode test
(backend ``locmem`` : les messages arrivent dans ``outbox``). L'envoi en tâche
de fond est remplacé par un appel direct.
"""

import os
import re
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager

from account_recovery import make_reset_token, read_reset_token, reset_email_content
from extensions import mail
from models import Role, User, db

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_PWD = "Ancien-pass-2026"
_NEW = "Nouveau-pass-2026"
_BASE = "https://file.ma-pharmacie.fr"


def _make_app(**config):
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"),
                static_folder=os.path.join(_SERVEUR, "static"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-password-reset",
        PHARMACY_NAME="Pharmacie du Test",
        MAIL_SERVER="smtp.example.fr", MAIL_PORT=587, MAIL_USE_TLS=True, MAIL_USE_SSL=False,
        MAIL_USERNAME="file@example.fr", MAIL_PASSWORD="x",
        MAIL_DEFAULT_SENDER="file@example.fr",
        MAIL_PASSWORD_RESET=True, MAIL_LINK_BASE_URL=_BASE,
    )
    app.config.update(config)
    db.init_app(app)
    mail.init_app(app)
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id):
        return User.query.filter_by(fs_uniquifier=str(user_id)).first()

    from routes.admin_security import admin_security_bp
    from routes.password_reset import password_reset_bp
    app.register_blueprint(admin_security_bp, url_prefix="")
    app.register_blueprint(password_reset_bp, url_prefix="")

    with app.app_context():
        db.create_all()
        role = Role(name="operateur")
        alice = User(username="alice", email="Alice@Example.fr", active=True)
        alice.set_password(_PWD)
        alice.roles.append(role)
        noemail = User(username="bob", email=None, active=True)
        noemail.set_password(_PWD)
        legacy = User(username="admin", email="admin", active=True)  # ancien bootstrap
        legacy.set_password(_PWD)
        off = User(username="carole", email="carole@example.fr", active=False)
        off.set_password(_PWD)
        db.session.add_all([role, alice, noemail, legacy, off])
        db.session.commit()
    return app


@pytest.fixture
def make_app(monkeypatch):
    import routes.password_reset as reset
    import services.account_recovery_service as service
    monkeypatch.setattr(reset, "_run_async", lambda fn, *args: fn(*args))
    monkeypatch.setattr(reset, "record_audit", lambda *a, **k: None)
    monkeypatch.setattr(service, "record_audit", lambda *a, **k: None)
    reset._request_throttle.clear()
    apps = []

    def factory(**config):
        app = _make_app(**config)
        apps.append(app)
        return app

    yield factory
    for app in apps:
        with app.app_context():
            db.session.remove()
            db.drop_all()


@pytest.fixture
def app(make_app):
    return make_app()


def _outbox(app):
    return getattr(app.extensions["mailman"], "outbox", [])


def _request(client, identifier, **kw):
    return client.post("/forgot_password", data={"identifier": identifier}, **kw)


def _link(app, index=-1):
    body = _outbox(app)[index].body
    return re.search(r"(https?://\S+/reset_password/\S+)", body).group(1)


def _user(app, username):
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        snap = SimpleNamespace(id=u.id, password=u.password, fs=u.fs_uniquifier,
                               must_change_password=u.must_change_password)

    def verify(pwd):
        with app.app_context():
            return User.query.filter_by(username=username).first().verify_password(pwd)

    snap.verify_password = verify
    return snap


# --- Disponibilité --------------------------------------------------------------

def test_page_formulaire_quand_disponible(app):
    html = app.test_client().get("/forgot_password").get_data(as_text=True)
    assert 'name="identifier"' in html
    assert "administrateur" in html  # les autres solutions restent affichées


@pytest.mark.parametrize("config", [
    {"MAIL_SERVER": ""},
    {"MAIL_DEFAULT_SENDER": "", "MAIL_USERNAME": "login-sans-arobase"},
    {"MAIL_PASSWORD_RESET": False},
])
def test_indisponible_sans_envoi_ou_option_coupee(make_app, config):
    app = make_app(**config)
    client = app.test_client()
    html = client.get("/forgot_password").get_data(as_text=True)
    assert 'name="identifier"' not in html
    assert "Pour retrouver l'accès" in html
    _request(client, "alice")
    assert _outbox(app) == []


def test_lien_sur_la_page_de_connexion():
    login = open(os.path.join(_SERVEUR, "templates/security/login.html"), encoding="utf-8").read()
    assert 'href="/forgot_password"' in login
    assert "request.args.get('reset') == 'done'" in login


# --- Demande de lien ------------------------------------------------------------

def test_envoi_par_identifiant(app):
    resp = _request(app.test_client(), "alice")
    assert resp.status_code == 200
    assert "Si un compte correspond" in resp.get_data(as_text=True)
    (msg,) = _outbox(app)
    assert msg.to == ["Alice@Example.fr"]
    assert msg.from_email == "file@example.fr"
    assert "Pharmacie du Test" in msg.subject
    assert "« alice »" in msg.body
    assert _link(app).startswith(_BASE + "/reset_password/")


def test_envoi_par_adresse_insensible_a_la_casse(app):
    _request(app.test_client(), "alice@EXAMPLE.fr")
    assert len(_outbox(app)) == 1


@pytest.mark.parametrize("identifier", ["inconnu", "bob", "admin", "carole", "nobody@example.fr"])
def test_aucun_envoi_mais_meme_reponse(app, identifier):
    client = app.test_client()
    known = _request(client, "alice").get_data(as_text=True)
    other = _request(client, identifier).get_data(as_text=True)
    assert other == known  # rien ne révèle l'existence du compte
    assert len(_outbox(app)) == 1  # seul alice a reçu un message


def test_lien_jamais_construit_depuis_l_en_tete_host(app):
    _request(app.test_client(), "alice", headers={"Host": "pirate.example"})
    assert _link(app).startswith(_BASE + "/")
    assert "pirate" not in _outbox(app)[0].body


def test_adresse_par_defaut_sans_reglage(make_app):
    app = make_app(MAIL_LINK_BASE_URL="")
    _request(app.test_client(), "alice", base_url="https://admin.local:5000")
    assert _link(app).startswith("https://admin.local:5000/reset_password/")


def test_demandes_limitees(app):
    client = app.test_client()
    responses = [_request(client, "alice").get_data(as_text=True) for _ in range(5)]
    assert len(_outbox(app)) == 3
    assert len(set(responses)) == 1


def test_identifiant_vide(app):
    resp = _request(app.test_client(), "  ")
    assert "Indiquez votre identifiant" in resp.get_data(as_text=True)


# --- Lien reçu ------------------------------------------------------------------

def _reset(client, link, p1=_NEW, p2=None):
    path = link.replace(_BASE, "")
    return client.post(path, data={"password1": p1, "password2": p2 or p1})


def test_parcours_complet(app):
    client = app.test_client()
    _request(client, "alice")
    link = _link(app)
    before = _user(app, "alice")

    page = client.get(link.replace(_BASE, ""))
    assert page.status_code == 200
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert "alice" in page.get_data(as_text=True)

    resp = _reset(client, link)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login?reset=done")
    after = _user(app, "alice")
    assert after.verify_password(_NEW)
    assert after.must_change_password is False
    assert after.fs != before.fs  # sessions révoquées

    # Usage unique : le même lien ne sert plus.
    again = client.get(link.replace(_BASE, ""))
    assert again.status_code == 400
    assert "plus valable" in again.get_data(as_text=True)


def test_lien_invalide_apres_changement_de_mot_de_passe(app):
    client = app.test_client()
    _request(client, "alice")
    link = _link(app)
    with app.app_context():
        from services.account_recovery_service import set_user_password
        user = User.query.filter_by(username="alice").first()
        set_user_password(user, "Autre-pass-2026", must_change=False)
        db.session.commit()
    assert client.get(link.replace(_BASE, "")).status_code == 400


def test_politique_et_confirmation(app):
    client = app.test_client()
    _request(client, "alice")
    link = _link(app)
    resp = _reset(client, link, "court")
    assert resp.status_code == 400
    assert "10 caractères" in resp.get_data(as_text=True)
    resp = _reset(client, link, _NEW, _NEW + "x")
    assert resp.status_code == 400
    assert "ne correspondent pas" in resp.get_data(as_text=True)
    assert _user(app, "alice").verify_password(_PWD)


def test_jeton_falsifie_ou_compte_desactive(app):
    client = app.test_client()
    assert client.get("/reset_password/nimporte-quoi").status_code == 400
    carole = _user(app, "carole")
    token = make_reset_token(app.config["SECRET_KEY"], carole.id, carole.password)
    assert client.get(f"/reset_password/{token}").status_code == 400
    # Signé avec une autre clé : refusé.
    alice = _user(app, "alice")
    forged = make_reset_token("autre-cle", alice.id, alice.password)
    assert client.get(f"/reset_password/{forged}").status_code == 400


# --- Noyau pur du jeton ---------------------------------------------------------

def test_jeton_expire():
    token = make_reset_token("k", 7, "hash")
    assert read_reset_token("k", token) == (7, read_reset_token("k", token)[1])
    assert read_reset_token("k", token, max_age=-1) is None
    assert read_reset_token("k", None) is None
    assert read_reset_token("k", "x" * 600) is None


def test_contenu_de_l_email():
    subject, body = reset_email_content("alice", "Pharmacie X", "https://h/reset_password/t")
    assert "Pharmacie X" in subject
    assert "1 heure" in body and "https://h/reset_password/t" in body
    assert "ignorez ce message" in body
