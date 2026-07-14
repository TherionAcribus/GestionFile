"""Point 4 — Sécuriser les fichiers de sauvegarde importés.

Exécutable **sans MySQL ni serveur**. Couvre :

1. Validation structurelle stricte (``backup_service.load_and_validate_backup``) :
   JSON mal formé, mauvaise ``app``, ``format_version`` non supportée, structure
   invalide, trop de sections — avec messages **sûrs** (pas de détail interne).
2. Décodage base64 borné (``_safe_b64decode``) : contenu invalide / trop gros.
3. Limites des sections d'images (nombre de fichiers, taille par fichier, volume
   total) sur ``current_app`` factice.
4. Anti-XSS de la prévisualisation : le gabarit Jinja échappe toute métadonnée du
   fichier importé (clé/label/date/version malveillants).
5. Route ``_read_uploaded_backup`` : extension, taille max, fichier trop
   volumineux, messages génériques.
"""

import base64
import io

import pytest
from flask import Flask, render_template_string

import backup_service as bs
from backup_service import (
    BackupValidationError,
    load_and_validate_backup,
    _safe_b64decode,
    MAX_BACKUP_FILE_BYTES,
)


# ---------------------------------------------------------------------------
# 1. Validation structurelle
# ---------------------------------------------------------------------------

def _valid_backup_dict():
    return {
        "app": "GestionFile",
        "format_version": "2.0",
        "timestamp": "2026-07-13 10:00:00",
        "sections": ["config"],
        "data": {"config": {"foo": "bar"}},
    }


def test_load_valid_backup():
    import json
    data = load_and_validate_backup(json.dumps(_valid_backup_dict()))
    assert data["app"] == "GestionFile"


def test_load_valid_backup_from_bytes():
    import json
    raw = json.dumps(_valid_backup_dict()).encode("utf-8")
    assert load_and_validate_backup(raw)["format_version"] == "2.0"


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "",
        "null",
        "[]",
        "123",
        '"just a string"',
    ],
)
def test_load_rejects_malformed_json(raw):
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(raw)


def test_load_rejects_wrong_app():
    import json
    d = _valid_backup_dict()
    d["app"] = "EvilApp"
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


@pytest.mark.parametrize("version", ["1.0", "4.0", None, "", "2", 2.0])
def test_load_rejects_unsupported_format_version(version):
    import json
    d = _valid_backup_dict()
    d["format_version"] = version
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


def test_load_rejects_bad_sections_type():
    import json
    d = _valid_backup_dict()
    d["sections"] = {"config": 1}  # pas une liste
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


def test_load_rejects_non_string_section():
    import json
    d = _valid_backup_dict()
    d["sections"] = ["config", 42]
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


def test_load_rejects_too_many_sections():
    import json
    d = _valid_backup_dict()
    d["sections"] = [f"s{i}" for i in range(bs.MAX_SECTIONS + 1)]
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


def test_load_rejects_bad_data_type():
    import json
    d = _valid_backup_dict()
    d["data"] = ["not", "a", "dict"]
    with pytest.raises(BackupValidationError):
        load_and_validate_backup(json.dumps(d))


def test_validation_error_messages_are_safe():
    """Les messages ne divulguent aucun détail technique interne."""
    import json
    with pytest.raises(BackupValidationError) as ei:
        load_and_validate_backup("{bad")
    msg = str(ei.value)
    for leak in ("Traceback", "Expecting", "line ", "char ", "0x"):
        assert leak not in msg


# ---------------------------------------------------------------------------
# 2. Décodage base64 borné
# ---------------------------------------------------------------------------

def test_safe_b64decode_ok():
    raw = b"hello world"
    enc = base64.b64encode(raw).decode("ascii")
    assert _safe_b64decode(enc, max_bytes=1024) == raw


def test_safe_b64decode_rejects_non_string():
    with pytest.raises(ValueError):
        _safe_b64decode(b"bytes", max_bytes=1024)


def test_safe_b64decode_rejects_invalid_base64():
    with pytest.raises(ValueError):
        _safe_b64decode("not*valid*base64*!!", max_bytes=1024)


def test_safe_b64decode_rejects_oversized_by_encoded_length():
    # Chaîne encodée bien plus grande que la borne : rejet avant décodage.
    big = base64.b64encode(b"x" * 5000).decode("ascii")
    with pytest.raises(ValueError):
        _safe_b64decode(big, max_bytes=100)


def test_safe_b64decode_rejects_oversized_decoded():
    # Encoded length juste sous la borne mais décodé au-dessus.
    payload = b"x" * 200
    enc = base64.b64encode(payload).decode("ascii")
    with pytest.raises(ValueError):
        _safe_b64decode(enc, max_bytes=100)


# ---------------------------------------------------------------------------
# 3. Limites des sections d'images (current_app factice)
# ---------------------------------------------------------------------------

class _FakeLogger:
    def __init__(self):
        self.msgs = []

    def warning(self, m, *a, **k):
        self.msgs.append(m)

    def error(self, m, *a, **k):
        self.msgs.append(m)


class _FakeApp:
    def __init__(self, static_folder):
        self.static_folder = static_folder
        self.logger = _FakeLogger()


@pytest.fixture
def fake_app(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    app = _FakeApp(str(static))
    monkeypatch.setattr(bs, "current_app", app)
    return app, tmp_path


def _png_b64():
    return base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def test_buttons_rejects_too_many_files(fake_app, monkeypatch):
    monkeypatch.setattr(bs, "MAX_IMAGE_FILES", 3)
    section = bs.ImagesButtonsSection()
    data = {f"f{i}.png": _png_b64() for i in range(4)}
    with pytest.raises(BackupValidationError):
        section.restore_data(data)


def test_buttons_skips_oversized_file(fake_app, monkeypatch):
    monkeypatch.setattr(bs, "MAX_DECODED_FILE_BYTES", 8)
    app, _ = fake_app
    section = bs.ImagesButtonsSection()
    big = base64.b64encode(b"x" * 100).decode("ascii")
    section.restore_data({"ok.png": _png_b64(), "toobig.png": big})
    base_dir = section._get_dir()
    import os
    assert os.path.exists(os.path.join(base_dir, "ok.png"))
    assert not os.path.exists(os.path.join(base_dir, "toobig.png"))
    assert app.logger.msgs  # refus journalisé


def test_buttons_rejects_total_volume(fake_app, monkeypatch):
    monkeypatch.setattr(bs, "MAX_DECODED_FILE_BYTES", 1024)
    monkeypatch.setattr(bs, "MAX_TOTAL_DECODED_BYTES", 100)
    section = bs.ImagesButtonsSection()
    chunk = base64.b64encode(b"y" * 60).decode("ascii")
    data = {f"f{i}.png": chunk for i in range(5)}
    with pytest.raises(BackupValidationError):
        section.restore_data(data)


def test_gallery_rejects_too_many_files(fake_app, monkeypatch):
    monkeypatch.setattr(bs, "MAX_IMAGE_FILES", 2)
    section = bs.ImagesGallerySection()
    data = {f"galleries/g/f{i}.png": _png_b64() for i in range(3)}
    with pytest.raises(BackupValidationError):
        section.restore_data(data)


# ---------------------------------------------------------------------------
# 4. Anti-XSS de la prévisualisation
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app():
    return Flask(__name__)


def test_preview_template_escapes_metadata(flask_app):
    from routes.admin_backup import _PREVIEW_TEMPLATE
    info = {
        "app": "GestionFile",
        "format_version": "<script>alert(1)</script>",
        "timestamp": "\"><img src=x onerror=alert(2)>",
        "sections": [
            {"key": "<b>k</b>", "label": "<script>evil()</script>", "count": 3},
        ],
    }
    with flask_app.app_context():
        html = render_template_string(_PREVIEW_TEMPLATE, info=info)
    # Aucune balise HTML brute injectée (toutes neutralisées par l'échappement).
    assert "<script>" not in html
    assert "<img" not in html
    # La valeur du timestamp est en contenu texte : ses < > " sont échappés,
    # donc aucune balise <img ...> n'est créée.
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
    # Les autres valeurs apparaissent bien, mais échappées.
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;k&lt;/b&gt;" in html


def test_preview_backup_builds_expected_structure():
    backup = {
        "app": "GestionFile",
        "format_version": "2.0",
        "timestamp": "2026-07-13 10:00:00",
        "sections": ["config", "unknown_section"],
        "data": {"config": {"a": 1, "b": 2}, "unknown_section": [1, 2, 3]},
    }
    info = bs.preview_backup(backup)
    keys = {s["key"] for s in info["sections"]}
    assert keys == {"config", "unknown_section"}


# ---------------------------------------------------------------------------
# 5. Route _read_uploaded_backup : extension / taille / messages
# ---------------------------------------------------------------------------

def _upload_ctx(flask_app, *, filename="backup.json", content=b"", content_length=None):
    data = {"file": (io.BytesIO(content), filename)}
    headers = {}
    ctx_kwargs = dict(
        path="/admin/backup/preview",
        method="POST",
        data=data,
        content_type="multipart/form-data",
    )
    return flask_app.test_request_context(**ctx_kwargs)


def test_read_rejects_non_json_extension(flask_app):
    from routes.admin_backup import _read_uploaded_backup
    with _upload_ctx(flask_app, filename="evil.exe", content=b"x"):
        data, err = _read_uploaded_backup()
    assert data is None
    assert ".json" in err


def test_read_rejects_missing_file(flask_app):
    from routes.admin_backup import _read_uploaded_backup
    with flask_app.test_request_context(
        path="/admin/backup/preview", method="POST",
        data={}, content_type="multipart/form-data",
    ):
        data, err = _read_uploaded_backup()
    assert data is None
    assert err


def test_read_rejects_oversized_file(flask_app, monkeypatch):
    import routes.admin_backup as rb
    monkeypatch.setattr(rb, "MAX_BACKUP_FILE_BYTES", 32)
    from routes.admin_backup import _read_uploaded_backup
    with _upload_ctx(flask_app, content=b"a" * 100):
        data, err = _read_uploaded_backup()
    assert data is None
    assert "volumineux" in err.lower()


def test_read_rejects_malformed_json(flask_app):
    from routes.admin_backup import _read_uploaded_backup
    with _upload_ctx(flask_app, content=b"{not valid json"):
        data, err = _read_uploaded_backup()
    assert data is None
    # message générique et sûr
    assert "Traceback" not in err
    assert err


def test_read_accepts_valid_backup(flask_app):
    import json
    from routes.admin_backup import _read_uploaded_backup
    content = json.dumps(_valid_backup_dict()).encode("utf-8")
    with _upload_ctx(flask_app, content=content):
        upload, err = _read_uploaded_backup()
    assert err is None
    assert upload.manifest["app"] == "GestionFile"
    assert upload.archive is None
    upload.close()
