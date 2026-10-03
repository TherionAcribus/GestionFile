"""Page /admin/app refaite + correctif de numérotation par activité.

Couvre :
- l'onglet demandé par l'URL (/admin/app/<tab> était ignoré) ;
- les connexions en direct : libellés lisibles, filtre limité aux canaux
  connus, total ;
- les gabarits : alerte RabbitMQ déplacée dans « Avancé », alerte TLS+SSL,
  honnêteté sur l'usage des e-mails ;
- les endpoints ``network_adress`` (suggestion d'adresse, QR de test) ;
- ``call_numbering.next_category_call_number`` : plus grand numéro + 1
  (l'ancien « nombre + 1 » redonnait un numéro après un retrait).

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    assert 'id="stress_reference_patients"' in general
    assert 'Pic habituel de patients' in general
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

    def test_login(username="admin"):
        from flask_login import login_user
        login_user(User.query.filter_by(username=username).first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])
    app.add_url_rule("/_test/login/<username>", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_app=True))
        # Compte sans la permission 'app' (vérifie le 403 des endpoints API).
        noperm = User(username="noperm", email="n@n.n",
                      password=generate_password_hash("x"), active=True)
        noperm.roles.append(Role(name="comptoir", admin_queue=True))
        db.session.add_all([admin, noperm])
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


# --- Adresse réseau (network_adress) -------------------------------------------

def test_default_config_network_adress_vide():
    # Plus d'IP de dev préremplie : une nouvelle installation part d'un champ
    # vide (l'admin remplit via « Détecter » ou manuellement si besoin).
    cfg = json.loads(_read("static/json/default_config.json"))
    assert cfg["configurations"]["network_adress"] == ""


def test_gabarit_network_adress():
    general = _read("templates/admin/app_general.html")
    assert 'id="network-adress-detect"' in general
    assert 'id="network-adress-test"' in general
    assert 'id="network-adress-test-result"' in general
    # Alerte réservée à l'ancienne valeur préremplie héritée des installs
    # antérieures.
    assert "http://192.168.86.221:5000" in general
    assert "alert-warning" in general


def test_suggest_reprend_adresse_actuelle(app):
    # Host non-loopback : l'admin joint déjà le serveur par une adresse
    # joignable depuis le réseau — on la propose telle quelle. Le cookie de
    # session étant lié au domaine, la connexion doit utiliser le même Host.
    host = "192.168.10.5:5000"
    c = app.test_client()
    c.post("/_test/login", headers={"Host": host})
    resp = c.get("/admin/app/network_adress/suggest", headers={"Host": host})
    assert resp.status_code == 200
    assert resp.get_json() == {"url": "http://192.168.10.5:5000/",
                               "source": "adresse actuelle"}


def test_suggest_detecte_ipv4_depuis_localhost(client):
    fake_sock = MagicMock()
    fake_sock.getsockname.return_value = ("10.20.30.40", 0)
    with patch("routes.admin_app.socket.socket", return_value=fake_sock):
        resp = client.get("/admin/app/network_adress/suggest",
                          headers={"Host": "localhost:5000"})
    assert resp.status_code == 200
    assert resp.get_json() == {"url": "http://10.20.30.40:5000",
                               "source": "détectée"}
    fake_sock.connect.assert_called_once_with(("192.0.2.1", 80))
    fake_sock.close.assert_called_once()


def test_suggest_detection_impossible(app):
    # Interface de sortie loopback (ou absente) : rien de proposable.
    host = "127.0.0.1:5000"
    c = app.test_client()
    c.post("/_test/login", headers={"Host": host})
    fake_sock = MagicMock()
    fake_sock.getsockname.return_value = ("127.0.0.1", 0)
    with patch("routes.admin_app.socket.socket", return_value=fake_sock):
        resp = c.get("/admin/app/network_adress/suggest",
                     headers={"Host": host})
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_suggest_anonyme_refuse(app):
    resp = app.test_client().get("/admin/app/network_adress/suggest")
    assert resp.status_code == 401


def test_suggest_sans_permission_refuse(app):
    c = app.test_client()
    c.post("/_test/login/noperm")
    assert c.get("/admin/app/network_adress/suggest").status_code == 403


def test_qrcode_png_ok(client):
    resp = client.get("/admin/app/network_adress/qrcode.png",
                      query_string={"url": "http://192.168.1.10:5000"})
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data[:4] == b"\x89PNG"


@pytest.mark.parametrize("qs", [
    {},                                        # paramètre absent
    {"url": "ftp://192.168.1.10"},             # scheme non http(s)
    {"url": "nimporte quoi"},                  # pas une URL
    {"url": "https://"},                       # netloc vide
])
def test_qrcode_png_url_invalide(client, qs):
    resp = client.get("/admin/app/network_adress/qrcode.png", query_string=qs)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "URL invalide"
