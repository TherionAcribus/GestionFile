"""Onglet Application › E-mails : envoi, test avant enregistrement, enregistrement.

Historique : l'envoi passait par flask-mailman configuré lors de
``mail.init_app`` — AVANT le chargement des réglages depuis la base. Les
e-mails partaient donc vers ``localhost:25`` quels que soient les réglages
saisis. ``services/mail_service.send_email`` construit désormais la connexion
à chaque envoi depuis les réglages courants (ou ceux du formulaire de test),
avec un délai maximal.

Application Flask minimale + SQLite (patron de ``test_view_app_page.py``).
"""

import smtplib
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from werkzeug.security import generate_password_hash

import routes.admin_app as admin_app
from extensions import mail
from models import ConfigOption, Role, User, db
from config_sync import CONFIG_GENERATION_KEY
from params_registry import PARAM_REGISTRY
from services import mail_service

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


class _Connections(list):
    """Connexions ouvertes pendant le test ; ``state['error']`` simule un échec."""

    def __init__(self):
        super().__init__()
        self.state = {"error": None}


class _FakeConnection:
    """Connexion SMTP factice : mémorise les messages ou lève ``error``."""

    def __init__(self, error=None, **kwargs):
        self.kwargs = kwargs
        self.error = error
        self.sent = []

    def send_messages(self, messages):
        if self.error:
            raise self.error
        self.sent.extend(messages)
        return len(messages)


@pytest.fixture()
def connections(monkeypatch):
    """Capture chaque connexion ouverte par send_email (ses réglages)."""
    made = _Connections()

    def fake_get_connection(backend=None, fail_silently=False, **kwargs):
        conn = _FakeConnection(error=made.state["error"], **kwargs)
        made.append(conn)
        return conn

    monkeypatch.setattr(mail, "get_connection", fake_get_connection)
    return made


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        PHARMACY_NAME="Pharmacie Test",
        MAIL_SERVER="smtp.enregistre.fr", MAIL_PORT=465, MAIL_USE_TLS=False, MAIL_USE_SSL=True,
        MAIL_USERNAME="file@enregistre.fr", MAIL_PASSWORD="secret-enregistre",
        MAIL_DEFAULT_SENDER="file@enregistre.fr",
    )
    db.init_app(app)
    mail.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_app.admin_app_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login(username="admin"):
        login_user(User.query.filter_by(username=username).first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login/<username>", view_func=test_login, methods=["POST"])
    monkeypatch.setattr(admin_app, "record_audit", lambda *a, **k: None)
    admin_app._email_test_last_sent.clear()

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="admin@enregistre.fr",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_app=True))
        noperm = User(username="noperm", password=generate_password_hash("x"), active=True)
        noperm.roles.append(Role(name="comptoir", admin_queue=True))
        db.session.add_all([admin, noperm])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login/admin")
    return c


_FORM = {"sender": "pharmacie@gmail.com", "host": "smtp.gmail.com", "port": "587",
         "security": "starttls", "username": "pharmacie@gmail.com", "password": "app-pass",
         "link_base_url": "https://file.pharmacie.fr/admin/app", "mail_adress": "moi@exemple.fr"}


# --- send_email : réglages courants, délai, erreurs expliquées -------------------

def test_envoi_utilise_les_reglages_courants(app, connections):
    with app.app_context():
        app.config["MAIL_SERVER"] = "smtp.modifie.fr"  # changé APRÈS init_app
        ok, error = mail_service.send_email("dest@x.fr", "Sujet", "Corps")
    assert (ok, error) == (True, None)
    (conn,) = connections
    assert conn.kwargs["host"] == "smtp.modifie.fr"
    assert conn.kwargs["port"] == 465
    assert conn.kwargs["use_ssl"] is True and conn.kwargs["use_tls"] is False
    assert conn.kwargs["password"] == "secret-enregistre"
    assert conn.kwargs["timeout"] == 10
    (message,) = conn.sent
    assert message.from_email == "file@enregistre.fr"
    assert message.to == ["dest@x.fr"]


def test_envoi_non_configure(app, connections):
    with app.app_context():
        app.config["MAIL_SERVER"] = ""
        ok, error = mail_service.send_email("dest@x.fr", "S", "C")
    assert ok is False and "pas configuré" in error
    assert connections == []


def test_envoi_en_echec_message_clair(app, connections):
    connections.state["error"] = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with app.app_context():
        ok, error = mail_service.send_email("dest@x.fr", "S", "C")
    assert ok is False
    assert "identifiant ou le mot de passe" in error
    assert "secret-enregistre" not in error


# --- Test avant enregistrement ---------------------------------------------------

def test_test_utilise_les_valeurs_du_formulaire(client, connections, app):
    resp = client.post("/admin/app/mail/test", data=_FORM)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "E-mail envoyé à moi@exemple.fr" in html and "alert-success" in html
    (conn,) = connections
    assert conn.kwargs["host"] == "smtp.gmail.com"
    assert conn.kwargs["use_tls"] is True and conn.kwargs["use_ssl"] is False
    assert conn.sent[0].from_email == "pharmacie@gmail.com"
    # Rien n'est enregistré par un test.
    assert app.config["MAIL_SERVER"] == "smtp.enregistre.fr"


def test_test_mot_de_passe_vide_reprend_celui_enregistre(client, connections):
    client.post("/admin/app/mail/test", data={**_FORM, "password": ""})
    assert connections[0].kwargs["password"] == "secret-enregistre"


def test_test_explique_l_echec(client, connections):
    connections.state["error"] = smtplib.SMTPAuthenticationError(535, b"5.7.8 not accepted")
    html = client.post("/admin/app/mail/test", data=_FORM).get_data(as_text=True)
    assert "alert-danger" in html
    assert "mot de passe d&#39;application" in html or "mot de passe d'application" in html


@pytest.mark.parametrize("override,fragment", [
    ({"mail_adress": ""}, "Indiquez l&#39;adresse"),
    ({"mail_adress": "pas-une-adresse"}, "destination n&#39;est pas valide"),
    ({"host": ""}, "Indiquez le serveur"),
])
def test_test_refuse_un_formulaire_incomplet(client, connections, override, fragment):
    html = client.post("/admin/app/mail/test", data={**_FORM, **override}).get_data(as_text=True)
    assert fragment in html
    assert connections == []


def test_test_limite_dans_le_temps(client, connections):
    client.post("/admin/app/mail/test", data=_FORM)
    html = client.post("/admin/app/mail/test", data=_FORM).get_data(as_text=True)
    assert "Patientez" in html
    assert len(connections) == 1


def test_permission_app_requise(app, connections):
    c = app.test_client()
    c.post("/_test/login/noperm")
    assert c.post("/admin/app/mail/test", data=_FORM).status_code == 403
    assert c.post("/admin/app/mail/save", data=_FORM).status_code == 403


# --- Enregistrement groupé -------------------------------------------------------

def _options(app):
    with app.app_context():
        return {o.config_key: o for o in ConfigOption.query.all()}


def test_enregistrement_ecrit_tout_en_une_fois(client, app):
    html = client.post("/admin/app/mail/save", data=_FORM).get_data(as_text=True)
    assert "Réglages enregistrés" in html
    opts = _options(app)
    assert opts["mail_server"].value_str == "smtp.gmail.com"
    assert opts["mail_port"].value_int == 587
    assert opts["mail_use_tls"].value_bool is True
    assert opts["mail_use_ssl"].value_bool is False
    assert opts["mail_password"].value_str == "app-pass"
    assert opts["mail_default_sender"].value_str == "pharmacie@gmail.com"
    # Adresse des liens réduite à schéma + hôte.
    assert opts["mail_link_base_url"].value_str == "https://file.pharmacie.fr"
    # Génération incrémentée : les autres processus rechargent la config.
    assert opts[CONFIG_GENERATION_KEY].value_int >= 1
    assert app.config["MAIL_SERVER"] == "smtp.gmail.com"
    assert app.config["MAIL_USE_SSL"] is False


def test_enregistrement_conserve_le_mot_de_passe_si_vide(client, app):
    client.post("/admin/app/mail/save", data={**_FORM, "password": ""})
    assert "mail_password" not in _options(app)
    assert app.config["MAIL_PASSWORD"] == "secret-enregistre"


def test_enregistrement_refuse_une_adresse_de_lien_invalide(client, app):
    html = client.post("/admin/app/mail/save",
                       data={**_FORM, "link_base_url": "javascript:alert(1)"}).get_data(as_text=True)
    assert "http:// ou https://" in html
    assert "mail_server" not in _options(app)


def test_enregistrement_refuse_un_formulaire_invalide(client, app):
    html = client.post("/admin/app/mail/save", data={**_FORM, "port": "0"}).get_data(as_text=True)
    assert "port" in html
    assert _options(app) == {}


# --- Registre et gabarit ---------------------------------------------------------

def test_nouvelles_cles_au_registre():
    assert PARAM_REGISTRY["mail_password_reset"].value_type == "value_bool"
    assert PARAM_REGISTRY["mail_password_reset"].permission == "app"
    assert PARAM_REGISTRY["mail_link_base_url"].config_name == "MAIL_LINK_BASE_URL"


def test_gabarit_un_seul_formulaire():
    tpl = _read("templates/admin/app_mail.html")
    assert 'id="mail-settings-form"' in tpl
    assert 'hx-post="/admin/app/mail/test"' in tpl and 'hx-post="/admin/app/mail/save"' in tpl
    assert tpl.count('hx-include="#mail-settings-form"') == 2
    assert 'name="security"' in tpl and 'mail_use_ssl' not in tpl  # plus de double interrupteur
    assert 'macros.switch("mail_password_reset"' in tpl
    assert "admin_mail.js" in _read("templates/admin/app.html")


# --- Mode Brevo (API web) : appel simulé, aucun envoi réel ------------------------

_BREVO_KEY = "xkeysib-" + "b" * 64
_BREVO_FORM = {"backend": "brevo", "sender": "file@pharma.fr", "api_key": _BREVO_KEY,
               "link_base_url": "", "mail_adress": "moi@exemple.fr"}


class _FakeResponse:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("pas de JSON")
        return self._data


@pytest.fixture()
def brevo_calls(monkeypatch):
    """Capture les appels à l'API Brevo ; ``reply`` règle la réponse simulée."""
    calls = []
    calls_reply = {"response": _FakeResponse(201, {"messageId": "<id@brevo>"}), "error": None}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if calls_reply["error"]:
            raise calls_reply["error"]
        return calls_reply["response"]

    monkeypatch.setattr(mail_service.requests, "post", fake_post)
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    return calls, calls_reply


def test_brevo_envoi_par_l_api(app, brevo_calls, connections):
    calls, _ = brevo_calls
    with app.app_context():
        app.config.update(MAIL_BACKEND="brevo", MAIL_BREVO_API_KEY=_BREVO_KEY,
                          MAIL_DEFAULT_SENDER="file@pharma.fr")
        ok, error = mail_service.send_email("dest@x.fr", "Sujet", "Corps")
    assert (ok, error) == (True, None)
    (call,) = calls
    assert call["url"] == "https://api.brevo.com/v3/smtp/email"
    assert call["headers"]["api-key"] == _BREVO_KEY
    assert call["timeout"] == 10
    assert call["json"]["sender"] == {"email": "file@pharma.fr", "name": "Pharmacie Test"}
    assert call["json"]["to"] == [{"email": "dest@x.fr"}]
    assert connections == []  # aucun SMTP


def test_brevo_cle_fournie_par_l_environnement(app, brevo_calls, monkeypatch):
    calls, _ = brevo_calls
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-env")
    with app.app_context():
        app.config.update(MAIL_BACKEND="brevo", MAIL_BREVO_API_KEY="",
                          MAIL_DEFAULT_SENDER="file@pharma.fr")
        assert mail_service.mail_configured()
        ok, _ = mail_service.send_email("dest@x.fr", "S", "C")
    assert ok and calls[0]["headers"]["api-key"] == "xkeysib-env"


def test_brevo_refus_explique_sans_divulguer_la_cle(app, brevo_calls):
    _, reply = brevo_calls
    reply["response"] = _FakeResponse(401, {"code": "unauthorized", "message": "Key not found"})
    with app.app_context():
        app.config.update(MAIL_BACKEND="brevo", MAIL_BREVO_API_KEY=_BREVO_KEY,
                          MAIL_DEFAULT_SENDER="file@pharma.fr")
        ok, error = mail_service.send_email("dest@x.fr", "S", "C")
    assert ok is False
    assert "Clé API refusée" in error
    assert _BREVO_KEY not in error


def test_brevo_injoignable(app, brevo_calls):
    _, reply = brevo_calls
    reply["error"] = mail_service.requests.ConnectTimeout("timeout")
    with app.app_context():
        app.config.update(MAIL_BACKEND="brevo", MAIL_BREVO_API_KEY=_BREVO_KEY,
                          MAIL_DEFAULT_SENDER="file@pharma.fr")
        ok, error = mail_service.send_email("dest@x.fr", "S", "C")
    assert ok is False and "ne répond pas" in error


def test_brevo_test_avant_enregistrement(client, brevo_calls, app):
    calls, _ = brevo_calls
    html = client.post("/admin/app/mail/test", data=_BREVO_FORM).get_data(as_text=True)
    assert "E-mail envoyé à moi@exemple.fr" in html
    assert calls[0]["headers"]["api-key"] == _BREVO_KEY
    assert "Brevo (API)" in calls[0]["json"]["textContent"]
    assert "MAIL_BACKEND" not in app.config or app.config["MAIL_BACKEND"] != "brevo"


def test_brevo_cle_smtp_refusee_avant_tout_appel(client, brevo_calls):
    calls, _ = brevo_calls
    html = client.post("/admin/app/mail/test",
                       data={**_BREVO_FORM, "api_key": "xsmtpsib-zzz"}).get_data(as_text=True)
    assert "clé SMTP" in html
    assert calls == []


def test_brevo_enregistrement(client, app):
    client.post("/admin/app/mail/save", data=_BREVO_FORM)
    opts = _options(app)
    assert opts["mail_backend"].value_str == "brevo"
    assert opts["mail_brevo_api_key"].value_str == _BREVO_KEY
    assert opts["mail_default_sender"].value_str == "file@pharma.fr"
    # Les réglages SMTP ne sont pas touchés (revenir en arrière ne perd rien).
    assert "mail_server" not in opts and "mail_password" not in opts
    assert app.config["MAIL_BACKEND"] == "brevo"
    assert app.config["MAIL_SERVER"] == "smtp.enregistre.fr"


def test_brevo_enregistrement_conserve_la_cle_si_vide(client, app):
    client.post("/admin/app/mail/save", data=_BREVO_FORM)
    client.post("/admin/app/mail/save", data={**_BREVO_FORM, "api_key": "",
                                              "sender": "autre@pharma.fr"})
    opts = _options(app)
    assert opts["mail_brevo_api_key"].value_str == _BREVO_KEY
    assert opts["mail_default_sender"].value_str == "autre@pharma.fr"


def test_cle_brevo_secrete():
    from params_registry import is_secret_key
    assert is_secret_key("mail_brevo_api_key")
    assert PARAM_REGISTRY["mail_backend"].allowed_values == {"", "brevo", "smtp"}


def test_gabarit_propose_brevo_en_premier():
    tpl = _read("templates/admin/app_mail.html")
    assert tpl.index('id="mail_backend_brevo"') < tpl.index('id="mail_backend_smtp"')
    assert 'name="api_key" value=""' in tpl  # jamais réaffichée
    assert "xkeysib-" in tpl and 'rel="noopener noreferrer"' in tpl
    css = _read("static/css/admin.css")
    assert ".app-mail-settings:has(#mail_backend_brevo:checked) .mail-smtp-only" in css


def test_compose_transmet_la_cle_au_service_web():
    compose = _read("docker-compose.coolify.yaml")
    import re as _re
    web = _re.search(r"\n  web:\n(.*?)\n  \w+:\n", compose, _re.DOTALL).group(1)
    assert "BREVO_API_KEY: ${BREVO_API_KEY:-}" in web
