"""Bornage des téléversements audio et de la clé Google (point audit).

Avant : les sons étaient acceptés sur leur seule extension (et la clé de
config ``ALLOWED_AUDIO_EXTENSIONS`` n'existait même pas — KeyError), et la
clé Google était lue entièrement sans limite ni validation JSON. Le module
``upload_security`` borne la lecture, renifle le contenu réel et valide le
schéma « compte de service ».
"""

import io
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

import routes.admin_announce as admin_announce
import upload_security
from models import ConfigOption, Role, User, db
from upload_security import (
    accept_audio_upload,
    read_upload_bounded,
    sniff_audio_extension,
    validate_service_account_json,
)


def _file_storage(data: bytes, filename: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename)


WAV_BYTES = b"RIFF" + (1000).to_bytes(4, "little") + b"WAVE" + b"fmt " + b"\x00" * 100
MP3_ID3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 100
MP3_FRAME_BYTES = b"\xff\xfb" + b"\x90\x64" + b"\x00" * 200


def _service_account_dict() -> dict:
    """Un dict de compte de service syntaxiquement complet, avec une vraie
    clé privée RSA (générée) pour passer la validation google-auth."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return {
        "type": "service_account",
        "project_id": "demo-project",
        "private_key_id": "abc123",
        "private_key": pem,
        "client_email": "tts@demo-project.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


# ---------------------------------------------------------------------------
# Validation unitaire — audio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "data,expected",
    [
        (WAV_BYTES, "wav"),
        (MP3_ID3_BYTES, "mp3"),
        (MP3_FRAME_BYTES, "mp3"),
        (b"<?php echo 'x'; ?>", None),
        (b"<html><body>fake</body></html>", None),
        (b"", None),
    ],
)
def test_sniff_audio_extension(data, expected):
    assert sniff_audio_extension(data) == expected


def test_accept_audio_upload_valid_wav():
    ok, error, result = accept_audio_upload(_file_storage(WAV_BYTES, "ding.wav"))
    assert ok and error is None
    assert result["ext"] == "wav"
    assert result["data"] == WAV_BYTES


@pytest.mark.parametrize("name", ["ding.mp3", "ding.MP3"])
def test_accept_audio_upload_valid_mp3(name):
    ok, error, _ = accept_audio_upload(_file_storage(MP3_ID3_BYTES, name))
    assert ok and error is None


def test_accept_audio_upload_rejects_polyglot():
    """Un script PHP renommé .mp3 est refusé : le contenu ne correspond pas."""
    ok, error, _ = accept_audio_upload(
        _file_storage(b"<?php system($_GET['c']); ?>", "evil.mp3")
    )
    assert not ok and "non reconnu" in error


def test_accept_audio_upload_rejects_extension_mismatch():
    """Un WAV renommé .mp3 est refusé (anti-polyglot, même principe qu'image)."""
    ok, error, _ = accept_audio_upload(_file_storage(WAV_BYTES, "song.mp3"))
    assert not ok and "ne correspond pas" in error


def test_accept_audio_upload_rejects_bad_extension_and_empty():
    ok, error, _ = accept_audio_upload(_file_storage(WAV_BYTES, "song.exe"))
    assert not ok
    ok, error, _ = accept_audio_upload(_file_storage(WAV_BYTES, "song"))
    assert not ok
    ok, error, _ = accept_audio_upload(_file_storage(b"", "song.wav"))
    assert not ok and "vide" in error


def test_read_upload_bounded_caps_size():
    """Au-delà de max_bytes, on lit au plus max_bytes+1 octets — jamais tout."""
    big = io.BytesIO(b"x" * (10 * 1024 * 1024))
    file = FileStorage(stream=big, filename="big.wav")
    ok, error, data = read_upload_bounded(file, max_bytes=1024)
    assert not ok and "volumineux" in error and data is None
    # Le flux n'a été lu que jusqu'à la borne, pas en entier.
    assert big.tell() == 1025


# ---------------------------------------------------------------------------
# Validation unitaire — clé de compte de service
# ---------------------------------------------------------------------------

def test_validate_service_account_json_valid():
    payload = json.dumps(_service_account_dict()).encode("utf-8")
    ok, error, normalized = validate_service_account_json(payload)
    assert ok and error is None
    # Le contenu retenu est le JSON normalisé, toujours décodable.
    assert json.loads(normalized)["type"] == "service_account"


@pytest.mark.parametrize(
    "payload",
    [
        b"pas du json {{{",
        b'"juste une chaine"',
        b"[1, 2, 3]",
        json.dumps({"type": "oauth2", "client_email": "a@b.c"}).encode(),
        # Champs requis manquants
        json.dumps(
            {"type": "service_account", "project_id": "p"}
        ).encode(),
        # private_key non-PEM : le signataire RSA refuse
        json.dumps(
            {**_service_account_dict(), "private_key": "pas-une-cle-pem"}
        ).encode(),
    ],
)
def test_validate_service_account_json_rejects(payload):
    ok, error, _ = validate_service_account_json(payload)
    assert not ok and error


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, template_folder="templates")
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        # db.metadatas est partagé entre fichiers de test : les fichiers
        # précédents ont déjà enregistré le bind 'users' — sans cette entrée,
        # db.create_all() lève UnboundExecutionError (convention de la suite).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        BASE32_KEY=Fernet.generate_key(),
        ANNOUNCE_ALERT_FILENAME="current.wav",
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_announce.admin_announce_bp)

    # Stub Flask-Security (non initialisée dans cette app de test minimale) :
    # require_permission redirige les anonymes vers security.login.
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user

        user = User.query.filter_by(username="admin").first()
        login_user(user)
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(
            username="admin",
            email="a@a.a",
            password=generate_password_hash("x"),
            active=True,
        )
        admin.roles.append(Role(name="admin", admin_announce=True))
        db.session.add(admin)
        db.session.commit()

    yield app, tmp_path


@pytest.fixture()
def client(app):
    return app[0].test_client()


def _login(client):
    client.post("/_test/login")


def test_route_upload_audio_writes_validated_file(app, client):
    flask_app, tmp_path = app
    _login(client)
    signals_dir = tmp_path / "signals"
    with patch.object(
        admin_announce, "_signals_dir", return_value=signals_dir
    ):
        response = client.post(
            "/admin/announce/audio/upload",
            data={"file": (io.BytesIO(WAV_BYTES), "ding.wav")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 302
    written = signals_dir / "ding.wav"
    assert written.exists() and written.read_bytes() == WAV_BYTES


def test_route_upload_audio_rejects_polyglot(app, client):
    flask_app, tmp_path = app
    _login(client)
    signals_dir = tmp_path / "signals"
    with patch.object(
        admin_announce, "_signals_dir", return_value=signals_dir
    ):
        response = client.post(
            "/admin/announce/audio/upload",
            data={
                "file": (
                    io.BytesIO(b"<?php system($_GET['c']); ?>"),
                    "evil.mp3",
                )
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 302
    assert not (signals_dir / "evil.mp3").exists()


def test_route_upload_audio_rejects_oversize(app, client):
    flask_app, tmp_path = app
    _login(client)
    signals_dir = tmp_path / "signals"
    oversize = MP3_FRAME_BYTES + b"\x00" * upload_security.MAX_AUDIO_BYTES
    with patch.object(
        admin_announce, "_signals_dir", return_value=signals_dir
    ):
        response = client.post(
            "/admin/announce/audio/upload",
            data={"file": (io.BytesIO(oversize), "big.mp3")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 302
    assert not (signals_dir / "big.mp3").exists()


def test_route_upload_google_key_stores_encrypted(app, client):
    flask_app, _ = app
    _login(client)
    payload = json.dumps(_service_account_dict()).encode("utf-8")
    # La route vérifie la clé auprès de Google après l'enregistrement : on
    # coupe l'appel réseau en simulant une liste de voix.
    with patch.object(
        admin_announce, "list_google_voices", return_value=[{"name": "fr-FR-Wavenet-A"}]
    ):
        response = client.post(
            "/admin/announce/google/add_key",
            data={"google_key_file": (io.BytesIO(payload), "key.json")},
            content_type="multipart/form-data",
        )
    assert b"alert-success" in response.data
    assert b"hx-swap-oob" in response.data
    with flask_app.app_context():
        option = ConfigOption.query.filter_by(config_key="voice_google_key").one()
        cipher = Fernet(flask_app.config["BASE32_KEY"])
        decrypted = json.loads(cipher.decrypt(option.value_json.encode()))
        assert decrypted["type"] == "service_account"
        assert decrypted["project_id"] == "demo-project"


def test_route_upload_google_key_refused_by_google(app, client):
    """Clé syntaxiquement valide mais refusée par Google : elle reste
    enregistrée, la réponse l'annonce comme un avertissement et non un succès
    (la bannière OOB ne prétend pas qu'elle fonctionne)."""
    flask_app, _ = app
    _login(client)
    payload = json.dumps(_service_account_dict()).encode("utf-8")
    from google.api_core import exceptions as gapi_exceptions

    with patch.object(
        admin_announce,
        "list_google_voices",
        side_effect=gapi_exceptions.PermissionDenied("API disabled"),
    ):
        response = client.post(
            "/admin/announce/google/add_key",
            data={"google_key_file": (io.BytesIO(payload), "key.json")},
            content_type="multipart/form-data",
        )
    assert b"alert-warning" in response.data
    assert b"hx-swap-oob" in response.data
    assert b"accept" not in response.data
    with flask_app.app_context():
        assert (
            ConfigOption.query.filter_by(config_key="voice_google_key").count()
            == 1
        )


@pytest.mark.parametrize(
    "filename,payload",
    [
        ("key.txt", json.dumps(_service_account_dict()).encode()),  # ext refusée
        ("key.json", b"pas du json"),  # contenu non-JSON
        ("key.json", json.dumps({"type": "oauth2"}).encode()),  # mauvais schéma
    ],
)
def test_route_upload_google_key_rejects(app, client, filename, payload):
    flask_app, _ = app
    _login(client)
    response = client.post(
        "/admin/announce/google/add_key",
        data={"google_key_file": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )
    assert b"alert-danger" in response.data
    with flask_app.app_context():
        assert (
            ConfigOption.query.filter_by(config_key="voice_google_key").count()
            == 0
        )


def test_route_upload_google_key_rejects_oversize(app, client):
    """Au-delà de MAX_SERVICE_ACCOUNT_JSON_BYTES, le fichier est refusé sans
    avoir été chargé entièrement en mémoire."""
    flask_app, _ = app
    _login(client)
    payload = b"x" * (upload_security.MAX_SERVICE_ACCOUNT_JSON_BYTES + 1)
    response = client.post(
        "/admin/announce/google/add_key",
        data={"google_key_file": (io.BytesIO(payload), "key.json")},
        content_type="multipart/form-data",
    )
    assert b"alert-danger" in response.data
    with flask_app.app_context():
        assert (
            ConfigOption.query.filter_by(config_key="voice_google_key").count()
            == 0
        )


def test_upload_routes_require_authentication(client):
    for url, field in (
        ("/admin/announce/audio/upload", "file"),
        ("/admin/announce/google/add_key", "google_key_file"),
    ):
        response = client.post(
            url,
            data={field: (io.BytesIO(b"x"), "f.bin")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Gardes statiques : le bornage ne doit pas régresser
# ---------------------------------------------------------------------------

def test_routes_do_not_read_or_save_unbounded():
    """Plus de lecture intégrale ni d'écriture directe du flux uploadé dans
    admin_announce : tout passe par read_upload_bounded / accept_audio_upload."""
    import inspect

    source = inspect.getsource(admin_announce.upload_google_key)
    assert "file.read(" not in source
    assert "read_upload_bounded" in source
    assert "validate_service_account_json" in source

    source = inspect.getsource(admin_announce.upload_signal_file)
    assert "file.save(" not in source
    assert "accept_audio_upload" in source


def test_audio_extension_whitelist_not_from_missing_config():
    """La liste blanche audio vient d'upload_security — la clé de config
    ALLOWED_AUDIO_EXTENSIONS n'ayant jamais existé, app.config ne doit plus
    être consultée pour elle."""
    import inspect

    source = inspect.getsource(admin_announce)
    assert 'app.config["ALLOWED_AUDIO_EXTENSIONS"]' not in source
    assert "ALLOWED_AUDIO_EXTENSIONS" in source  # importée depuis upload_security
