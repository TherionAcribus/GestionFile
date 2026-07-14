"""Point 13 — Optimiser les sauvegardes (archive ZIP + estimation de taille).

Exécutable **sans MySQL ni serveur**. Couvre :

1. Écriture d'une archive 3.0 (``write_backup_archive``) : manifeste JSON pour
   les sections non binaires + images en **fichiers binaires** ``files/<key>/…``
   (jamais de base64 ni de gros JSON).
2. Aller-retour complet : archive → ``load_and_validate_archive`` →
   ``restore_sections`` (données + images écrites au bon endroit).
3. Gardes anti-bombe à l'ouverture d'une archive (manifeste absent/illisible,
   trop d'entrées, volume décompressé, fichier trop volumineux ignoré).
4. Choix du format (``selection_has_binary``) et estimation de taille
   (``estimate_export_size``, drapeau « lourd »).
5. ``preview_backup`` sur un manifeste 3.0 (compte des images via
   ``binary_counts``).
6. Route ``_read_uploaded_backup`` : téléversement d'un vrai .zip → restauration.
"""

import io
import json
import os
import zipfile

import pytest
from flask import Flask

import backup_service as bs
from backup_service import (
    BackupValidationError,
    write_backup_archive,
    load_and_validate_archive,
    restore_sections,
    selection_has_binary,
    estimate_export_size,
    preview_backup,
    human_size,
    MANIFEST_NAME,
    ARCHIVE_FORMAT_VERSION,
)


# ---------------------------------------------------------------------------
# Faux current_app
# ---------------------------------------------------------------------------

class _FakeLogger:
    def __init__(self):
        self.msgs = []

    def warning(self, m, *a, **k):
        self.msgs.append(("warning", m))

    def error(self, m, *a, **k):
        self.msgs.append(("error", m))


class _FakeApp:
    def __init__(self, static_folder):
        self.static_folder = static_folder
        self.logger = _FakeLogger()


class _DummyDataSection(bs.BackupSection):
    """Section non binaire minimale (pas d'accès base de données)."""

    key = "dummy"
    label = "Dummy"
    restored = None

    def export_data(self):
        return {"hello": "world", "n": 3}

    def restore_data(self, data):
        _DummyDataSection.restored = data


PNG = b"\x89PNG\r\n\x1a\nFAKEDATA"


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Prépare une arborescence static avec des images boutons + registre réduit."""
    static = tmp_path / "static"
    buttons = static / "images" / "buttons"
    (buttons / "sub").mkdir(parents=True)
    (buttons / "a.png").write_bytes(PNG)
    (buttons / "sub" / "b.png").write_bytes(PNG * 2)

    app = _FakeApp(str(static))
    monkeypatch.setattr(bs, "current_app", app)
    # Registre réduit : une section de données + une section d'images.
    monkeypatch.setattr(
        bs, "BACKUP_SECTIONS",
        {"dummy": _DummyDataSection, "images_buttons": bs.ImagesButtonsSection},
    )
    monkeypatch.setattr(bs, "BINARY_SECTION_KEYS", {"images_buttons"})
    _DummyDataSection.restored = None
    return app, tmp_path


# ---------------------------------------------------------------------------
# 1. Écriture d'archive 3.0
# ---------------------------------------------------------------------------

def test_write_archive_layout(fake_env):
    buf = io.BytesIO()
    manifest = write_backup_archive(["dummy", "images_buttons"], buf)

    assert manifest["format_version"] == ARCHIVE_FORMAT_VERSION
    assert manifest["binary_sections"] == ["images_buttons"]
    assert manifest["binary_counts"]["images_buttons"] == 2
    # Les données non binaires figurent dans le manifeste ; pas les images.
    assert manifest["data"]["dummy"] == {"hello": "world", "n": 3}
    assert "images_buttons" not in manifest["data"]

    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        names = set(zf.namelist())
        assert MANIFEST_NAME in names
        assert "files/images_buttons/a.png" in names
        assert "files/images_buttons/sub/b.png" in names
        # Les images sont stockées telles quelles (pas de base64).
        assert zf.read("files/images_buttons/a.png") == PNG
        # Le manifeste ne contient aucune donnée base64 d'image.
        man = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        assert man["data"]["dummy"]["hello"] == "world"


def test_write_archive_no_images_still_valid(fake_env):
    buf = io.BytesIO()
    manifest = write_backup_archive(["dummy"], buf)
    assert manifest["binary_sections"] == []
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        assert zf.namelist() == [MANIFEST_NAME]


# ---------------------------------------------------------------------------
# 2. Aller-retour complet (export → restore)
# ---------------------------------------------------------------------------

def test_archive_round_trip_restore(fake_env, tmp_path):
    app, _ = fake_env
    buf = io.BytesIO()
    write_backup_archive(["dummy", "images_buttons"], buf)

    # Restaurer dans un dossier static neuf.
    dst = tmp_path / "static_dst"
    dst.mkdir()
    app.static_folder = str(dst)

    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        archive = load_and_validate_archive(zf)
        report = restore_sections(archive.manifest, archive=archive)

    assert report["success"] is True
    assert set(report["restored"]) == {"dummy", "images_buttons"}
    assert _DummyDataSection.restored == {"hello": "world", "n": 3}
    # Images réécrites au bon endroit, contenu identique.
    assert (dst / "images" / "buttons" / "a.png").read_bytes() == PNG
    assert (dst / "images" / "buttons" / "sub" / "b.png").read_bytes() == PNG * 2


def test_restore_binary_without_archive_reports_missing(fake_env):
    # Un manifeste 3.0 sans archive fournie ne peut pas restaurer les images.
    manifest = {
        "app": "GestionFile",
        "format_version": "3.0",
        "sections": ["images_buttons"],
        "binary_sections": ["images_buttons"],
        "binary_counts": {"images_buttons": 2},
        "data": {},
    }
    report = restore_sections(manifest, archive=None)
    assert report["success"] is False
    assert any("images_buttons" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# 3. Gardes anti-bombe / validation d'archive
# ---------------------------------------------------------------------------

def _zip_bytes(entries: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def _valid_manifest_json(**over):
    d = {
        "app": "GestionFile",
        "format_version": "3.0",
        "sections": ["dummy"],
        "binary_sections": [],
        "data": {"dummy": {"a": 1}},
    }
    d.update(over)
    return json.dumps(d)


def test_load_archive_rejects_missing_manifest(fake_env):
    buf = _zip_bytes({"files/images_buttons/a.png": PNG})
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(BackupValidationError):
            load_and_validate_archive(zf)


def test_load_archive_rejects_bad_manifest_json(fake_env):
    buf = _zip_bytes({MANIFEST_NAME: "{not json"})
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(BackupValidationError):
            load_and_validate_archive(zf)


def test_load_archive_rejects_unsupported_version(fake_env):
    buf = _zip_bytes({MANIFEST_NAME: _valid_manifest_json(format_version="9.9")})
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(BackupValidationError):
            load_and_validate_archive(zf)


def test_load_archive_rejects_too_many_entries(fake_env, monkeypatch):
    monkeypatch.setattr(bs, "MAX_ARCHIVE_ENTRIES", 2)
    entries = {MANIFEST_NAME: _valid_manifest_json()}
    for i in range(3):
        entries[f"files/images_buttons/f{i}.png"] = PNG
    buf = _zip_bytes(entries)
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(BackupValidationError):
            load_and_validate_archive(zf)


def test_load_archive_rejects_uncompressed_bomb(fake_env, monkeypatch):
    monkeypatch.setattr(bs, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 10)
    buf = _zip_bytes({
        MANIFEST_NAME: _valid_manifest_json(),
        "files/images_buttons/a.png": b"x" * 100,
    })
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(BackupValidationError):
            load_and_validate_archive(zf)


def test_archive_iter_skips_oversized_file(fake_env, monkeypatch):
    monkeypatch.setattr(bs, "MAX_DECODED_FILE_BYTES", 4)
    buf = _zip_bytes({
        MANIFEST_NAME: _valid_manifest_json(
            sections=["images_buttons"], binary_sections=["images_buttons"], data={},
        ),
        "files/images_buttons/big.png": b"x" * 100,
    })
    with zipfile.ZipFile(buf, "r") as zf:
        archive = load_and_validate_archive(zf)
        items = list(archive.iter_binary_files("images_buttons"))
    assert items == [("big.png", None)]  # refusé sans être lu


# ---------------------------------------------------------------------------
# 4. Choix du format + estimation
# ---------------------------------------------------------------------------

def test_selection_has_binary(fake_env):
    assert selection_has_binary(["dummy", "images_buttons"]) is True
    assert selection_has_binary(["dummy"]) is False
    assert selection_has_binary([]) is False


def test_estimate_counts_only_images(fake_env):
    est = estimate_export_size(["dummy", "images_buttons"])
    assert est["images_bytes"] == len(PNG) + len(PNG) * 2
    assert est["sections"][0]["key"] == "images_buttons"
    assert est["heavy"] is False  # sous le seuil par défaut


def test_estimate_heavy_flag(fake_env, monkeypatch):
    monkeypatch.setattr(bs, "EXPORT_IMAGE_WARNING_BYTES", 1)
    est = estimate_export_size(["images_buttons"])
    assert est["heavy"] is True
    assert "images_human" in est


def test_estimate_no_images_empty(fake_env):
    est = estimate_export_size(["dummy"])
    assert est["images_bytes"] == 0
    assert est["sections"] == []
    assert est["heavy"] is False


def test_human_size():
    assert human_size(0) == "0 o"
    assert human_size(512) == "512 o"
    assert human_size(1536).endswith("Ko")
    assert human_size(5 * 1024 * 1024).endswith("Mo")


# ---------------------------------------------------------------------------
# 5. Prévisualisation d'un manifeste 3.0
# ---------------------------------------------------------------------------

def test_preview_uses_binary_counts():
    manifest = {
        "app": "GestionFile",
        "format_version": "3.0",
        "timestamp": "2026-07-14 10:00:00",
        "sections": ["images_buttons"],
        "binary_sections": ["images_buttons"],
        "binary_counts": {"images_buttons": 7},
        "data": {},
    }
    info = preview_backup(manifest)
    section = next(s for s in info["sections"] if s["key"] == "images_buttons")
    assert section["count"] == 7


# ---------------------------------------------------------------------------
# 6. Route _read_uploaded_backup : téléversement d'un vrai .zip
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app():
    return Flask(__name__)


def test_read_uploaded_zip_round_trip(fake_env, tmp_path, flask_app):
    from routes.admin_backup import _read_uploaded_backup

    # 1) Produire une archive réelle.
    buf = io.BytesIO()
    write_backup_archive(["dummy", "images_buttons"], buf)
    content = buf.getvalue()

    # 2) La téléverser via la route (fichier .zip).
    app, _ = fake_env
    dst = tmp_path / "static_dst"
    dst.mkdir()
    app.static_folder = str(dst)

    data = {"file": (io.BytesIO(content), "backup.zip")}
    with flask_app.test_request_context(
        path="/admin/backup/import", method="POST",
        data=data, content_type="multipart/form-data",
    ):
        upload, err = _read_uploaded_backup()
        assert err is None
        assert upload.archive is not None
        assert upload.manifest["format_version"] == "3.0"
        try:
            report = restore_sections(
                upload.manifest, archive=upload.archive
            )
        finally:
            upload.close()

    assert report["success"] is True
    assert (dst / "images" / "buttons" / "a.png").read_bytes() == PNG
    # Le fichier temporaire est supprimé après close().
    assert upload.tmp_path is not None
    assert not os.path.exists(upload.tmp_path)


def test_read_uploaded_rejects_bad_zip(fake_env, flask_app):
    from routes.admin_backup import _read_uploaded_backup
    data = {"file": (io.BytesIO(b"not a zip at all"), "backup.zip")}
    with flask_app.test_request_context(
        path="/admin/backup/import", method="POST",
        data=data, content_type="multipart/form-data",
    ):
        upload, err = _read_uploaded_backup()
    assert upload is None
    assert err  # message sûr


def test_read_uploaded_rejects_unknown_extension(fake_env, flask_app):
    from routes.admin_backup import _read_uploaded_backup
    data = {"file": (io.BytesIO(b"x"), "evil.exe")}
    with flask_app.test_request_context(
        path="/admin/backup/import", method="POST",
        data=data, content_type="multipart/form-data",
    ):
        upload, err = _read_uploaded_backup()
    assert upload is None
    assert ".zip" in err or ".json" in err
