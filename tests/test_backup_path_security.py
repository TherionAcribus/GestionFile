"""Point 3 — Blocage des traversées de répertoires pendant une restauration.

Deux niveaux, exécutables **sans MySQL ni serveur** :

1. Primitive pure ``path_security.safe_relative_path`` / ``split_relative_path`` :
   refus des chemins absolus, ``..``, lecteurs Windows, UNC, extensions non
   autorisées ; acceptation des chemins relatifs légitimes (Linux et Windows).
2. Restauration réelle des sections images (``ImagesButtonsSection`` /
   ``ImagesGallerySection``) sur un ``current_app`` factice pointant vers un
   dossier temporaire : un chemin malveillant n'écrit jamais hors du dossier
   cible, un chemin légitime est bien restauré.
"""

import base64
import os

import pytest

import path_security as ps
from path_security import UnsafePathError, safe_relative_path, split_relative_path


# ---------------------------------------------------------------------------
# 1. Primitive pure
# ---------------------------------------------------------------------------

MALICIOUS_PATHS = [
    "../evil.png",
    "../../etc/passwd",
    "a/../../evil.png",
    "sub/../../evil.png",
    "/etc/passwd",            # POSIX absolu
    "/absolute.png",
    "\\\\server\\share\\x.png",  # UNC Windows
    "C:\\Windows\\system32\\evil.png",  # lecteur Windows
    "C:/Windows/evil.png",
    "..\\..\\evil.png",       # traversée Windows
    "sub\\..\\..\\evil.png",
    "a//b.png",               # segment vide
    "a/.png/../x.png",
    "foo/./bar.png",          # segment '.'
    "with\x00null.png",       # octet NUL
    "",                       # vide
]


@pytest.mark.parametrize("bad", MALICIOUS_PATHS)
def test_safe_relative_path_rejects_malicious(bad, tmp_path):
    with pytest.raises(UnsafePathError):
        safe_relative_path(tmp_path, bad, allowed_extensions={"png"})


@pytest.mark.parametrize("bad", MALICIOUS_PATHS)
def test_split_relative_path_rejects_malicious(bad):
    with pytest.raises(UnsafePathError):
        split_relative_path(bad)


def test_safe_relative_path_rejects_non_string(tmp_path):
    for bad in (None, 123, b"bytes.png", ["a"]):
        with pytest.raises(UnsafePathError):
            safe_relative_path(tmp_path, bad)


@pytest.mark.parametrize(
    "good",
    [
        "a.png",
        "sub/a.png",
        "sub/deeper/a.png",
        "sub\\a.png",          # séparateur Windows accepté (normalisé)
        "galleries/g1/img.jpg",
    ],
)
def test_safe_relative_path_accepts_legit(good, tmp_path):
    result = safe_relative_path(tmp_path, good, allowed_extensions={"png", "jpg"})
    resolved = os.path.realpath(str(result))
    base = os.path.realpath(str(tmp_path))
    assert resolved == base or resolved.startswith(base + os.sep)


def test_safe_relative_path_extension_whitelist(tmp_path):
    # Refuse une extension non-image
    for bad in ("evil.py", "sub/evil.exe", "a.PNG.py", "noext", "a."):
        with pytest.raises(UnsafePathError):
            safe_relative_path(tmp_path, bad, allowed_extensions={"png", "jpg"})
    # Accepte, insensible à la casse
    assert safe_relative_path(tmp_path, "IMG.PNG", allowed_extensions={"png"})


def test_safe_relative_path_no_extension_check_when_none(tmp_path):
    # Sans whitelist, l'extension n'est pas contrôlée (mais la traversée l'est)
    assert safe_relative_path(tmp_path, "sub/file.py")
    with pytest.raises(UnsafePathError):
        safe_relative_path(tmp_path, "../file.py")


# ---------------------------------------------------------------------------
# 2. Restauration réelle des sections images (current_app factice)
# ---------------------------------------------------------------------------

import backup_service as bs


class _FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(msg)

    def error(self, msg, *a, **k):
        self.warnings.append(msg)


class _FakeApp:
    def __init__(self, static_folder):
        self.static_folder = static_folder
        self.logger = _FakeLogger()


def _png_b64():
    # PNG minimal (signature) — le contenu importe peu pour ce test.
    return base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


@pytest.fixture
def fake_app(tmp_path, monkeypatch):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    app = _FakeApp(str(static_folder))
    monkeypatch.setattr(bs, "current_app", app)
    return app, tmp_path


def test_buttons_restore_blocks_traversal(fake_app):
    app, tmp_path = fake_app
    section = bs.ImagesButtonsSection()
    base_dir = os.path.realpath(section._get_dir())

    data = {
        "legit.png": _png_b64(),
        "sub/ok.png": _png_b64(),
        "../escaped.png": _png_b64(),
        "../../escaped.png": _png_b64(),
        "..\\escaped.png": _png_b64(),
        "C:\\Windows\\evil.png": _png_b64(),
        "evil.py": _png_b64(),
    }
    section.restore_data(data)

    # Fichiers légitimes écrits sous le dossier cible.
    assert os.path.exists(os.path.join(base_dir, "legit.png"))
    assert os.path.exists(os.path.join(base_dir, "sub", "ok.png"))

    # Rien n'a été écrit hors du dossier cible.
    for bad in ("escaped.png",):
        assert not os.path.exists(os.path.join(os.path.dirname(base_dir), bad))
        assert not os.path.exists(
            os.path.join(os.path.dirname(os.path.dirname(base_dir)), bad)
        )
    # L'extension non-image est refusée.
    assert not os.path.exists(os.path.join(base_dir, "evil.py"))
    # Des refus ont bien été journalisés.
    assert app.logger.warnings


def test_gallery_restore_blocks_traversal_and_confines(fake_app):
    app, tmp_path = fake_app
    static_folder = os.path.realpath(app.static_folder)
    section = bs.ImagesGallerySection()

    data = {
        "galleries/g1/ok.png": _png_b64(),
        "images/annonces/ok.jpg": _png_b64(),
        # Sous static/ mais hors périmètre autorisé -> refusé.
        "images/buttons/inject.png": _png_b64(),
        "otherdir/inject.png": _png_b64(),
        # Traversée pure -> refusé.
        "../escaped.png": _png_b64(),
        "galleries/../../escaped.png": _png_b64(),
    }
    section.restore_data(data)

    assert os.path.exists(os.path.join(static_folder, "galleries", "g1", "ok.png"))
    assert os.path.exists(
        os.path.join(static_folder, "images", "annonces", "ok.jpg")
    )
    # Confinement : pas d'écriture dans un autre sous-dossier de static/.
    assert not os.path.exists(
        os.path.join(static_folder, "images", "buttons", "inject.png")
    )
    assert not os.path.exists(os.path.join(static_folder, "otherdir", "inject.png"))
    # Pas d'évasion hors de static/.
    assert not os.path.exists(
        os.path.join(os.path.dirname(static_folder), "escaped.png")
    )
    assert app.logger.warnings
