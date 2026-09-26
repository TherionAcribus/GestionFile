"""Page /admin/app refaite + correctif de numérotation par activité.

Couvre :
- l'onglet demandé par l'URL (/admin/app/<tab> était ignoré) ;
- les connexions en direct : libellés lisibles, filtre limité aux canaux
  connus, total ;
- les gabarits : alerte RabbitMQ déplacée dans « Avancé », alerte TLS+SSL,
  honnêteté sur l'usage des e-mails ;
- ``call_numbering.next_category_call_number`` : plus grand numéro + 1
  (l'ancien « nombre + 1 » redonnait un numéro après un retrait).

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_app as admin_app
from call_numbering import next_category_call_number
from models import Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


# --- Numérotation -----------------------------------------------------------------

def test_numerotation_par_activite_prend_le_plus_grand():
    assert next_category_call_number("A", []) == "A-1"
    assert next_category_call_number("A", ["A-1", "A-2", "A-3"]) == "A-4"
    # A-2 retiré : l'ancien calcul (2 patients + 1) redonnait « A-3 ».
    assert next_category_call_number("A", ["A-1", "A-3"]) == "A-4"
    # Autres lettres, numéros simples et valeurs parasites ignorés.
    assert next_category_call_number("A", ["B-9", "12", "A-x", None, "A-10"]) == "A-11"


# --- Gabarits --------------------------------------------------------------------

def test_gabarits_general_et_mail():
    general = _read("templates/admin/app_general.html")
    # L'avertissement « redémarrage requis » est dans la section RabbitMQ.
    avance = general.split("Avancé : relais entre processus", 1)[1]
    assert "Redémarrage requis" in avance and "start_rabbitmq" in avance
    assert "Redémarrage requis" not in general.split("Avancé : relais entre processus", 1)[0]
    mail = _read("templates/admin/app_mail.html")
    assert 'secret_full("mail_password"' in mail
    assert "mail-tls-ssl-conflict" in mail
    assert "e-mail de test" in mail
    css = _read("static/css/admin.css")
    assert ":has(#mail_use_tls:checked):has(#mail_use_ssl:checked)" in css


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        START_RABBITMQ=False, NETWORK_ADRESS="", NUMBERING_BY_ACTIVITY=True,
        ANNOUNCE_SOUND=True, PHARMACY_NAME="Test", MAIL_SERVER="", MAIL_PORT=587,
        MAIL_USERNAME="", MAIL_DEFAULT_SENDER="", MAIL_USE_TLS=True, MAIL_USE_SSL=False,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_app.admin_app_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_app=True))
        db.session.add(admin)
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


@pytest.mark.parametrize("url,expected", [
    ("/admin/app/backups", "backups"),
    ("/admin/app?tab=mail", "mail"),
    ("/admin/app/nimporte", "general"),
    ("/admin/app", "general"),
])
def test_onglet_demande(client, url, expected):
    captured = {}
    with patch.object(admin_app, "render_template",
                      lambda tpl, **kw: captured.update(kw) or ""):
        client.get(url)
    assert captured["active_tab"] == expected
    assert ("/socket_patient", "Bornes patient") in captured["namespaces"]


def test_connexions_lisibles(client):
    fake = {"/socket_patient": [{"sid": "abcdef123", "username": "Unknown"}],
            "/socket_admin": [{"sid": "zzz999", "username": "admin"}]}
    with patch.object(admin_app, "get_connected_clients", lambda ns: fake.get(ns, [])):
        html = client.post("/admin/app/get_connections",
                           data={"namespaces[]": ["/socket_patient", "/socket_admin", "/pirate"]}
                           ).get_data(as_text=True)
    assert "Bornes patient" in html and "Administration" in html
    assert "Sans compte" in html and "admin" in html
    assert "/pirate" not in html
    assert "<strong>2</strong> connexions actives" in html
