"""Tests du validateur d'images centralisé (``image_storage``).

Ces tests sont volontairement « purs » : ils ne nécessitent ni Flask, ni base,
ni ``flask_security`` — uniquement ``werkzeug.datastructures.FileStorage`` qui
est une dépendance déjà présente. Ils couvrent :

- acceptation des formats légitimes (png, jpg, jpeg, gif, webp) ;
- refus des extensions non autorisées (.txt, .py, .svg, .exe) ;
- refus des fichiers vides ;
- refus des contenus dont les octets magiques ne correspondent pas à
  l'extension déclarée (anti-polyglot : un ``.png`` qui est du texte) ;
- refus des fichiers trop volumineux ;
- génération d'un nom unique dérivé du contenu (deux contenus identiques ->
  même nom ; deux contenus différents -> noms différents) ;
- borne sur le nombre de fichiers par lot (constante exportée).
"""

from __future__ import annotations

import io

import pytest

from image_storage import (
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_IMAGES_PER_UPLOAD,
    THUMBNAIL_MAX_SIZE,
    THUMBNAIL_PREFIX,
    ImageValidationError,
    accept_image_upload,
    generate_thumbnail,
    thumbnail_filename,
    unique_image_filename,
)


# --- Helpers : construire un FileStorage à partir de bytes ---

def _file(name: str, data: bytes):
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=io.BytesIO(data), filename=name)


# Petites images valides (en-têtes minimales suffisent pour le reniffrage).

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_GIF = b"GIF89a" + b"\x00" * 32
_WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32
_BMP = b"BM" + b"\x00" * 32


# --- Acceptation des formats légitimes ---

@pytest.mark.parametrize("name,data", [
    ("photo.png", _PNG),
    ("photo.PNG", _PNG),          # insensible à la casse
    ("photo.jpg", _JPG),
    ("photo.jpeg", _JPG),         # jpeg accepté comme jpg
    ("photo.JPEG", _JPG),
    ("anim.gif", _GIF),
    ("modern.webp", _WEBP),
])
def test_accepte_formats_legitimes(name, data):
    ok, err, result = accept_image_upload(_file(name, data))
    assert ok, f"{name} devrait être accepté : {err}"
    assert result is not None
    assert result["data"] == data
    assert result["ext"] in {"png", "jpg", "gif", "webp"}
    # Le nom stocké est dérivé du contenu, pas du nom d'origine.
    assert result["filename"].endswith("." + result["ext"])
    assert "/" not in result["filename"]
    assert "\\" not in result["filename"]


# --- Refus des extensions non autorisées ---

@pytest.mark.parametrize("name", [
    "script.py",
    "notes.txt",
    "icon.svg",        # SVG exclu de l'upload (risque XSS)
    "payload.exe",
    "noext",
    "double.",
])
def test_refuse_extensions_interdites(name):
    ok, err, result = accept_image_upload(_file(name, b"\x00" * 16))
    assert not ok
    assert result is None
    assert err  # message présent


# --- Refus des fichiers vides ---

def test_refuse_fichier_vide():
    ok, err, result = accept_image_upload(_file("vide.png", b""))
    assert not ok
    assert "vide" in err.lower() or "vide" in err


# --- Anti-polyglot : contenu ne correspond pas à l'extension ---

def test_refuse_png_qui_est_du_texte():
    ok, err, result = accept_image_upload(_file("fake.png", b"<html>not an image</html>"))
    assert not ok
    assert "correspond" in err.lower() or "format" in err.lower()


def test_refuse_jpg_qui_est_du_png():
    ok, err, result = accept_image_upload(_file("fake.jpg", _PNG))
    assert not ok
    assert "correspond" in err.lower() or "format" in err.lower()


# --- Limite de taille ---

def test_refuse_fichier_trop_volumineux():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    ok, err, result = accept_image_upload(_file("big.png", big), max_bytes=100)
    assert not ok
    assert "volumineux" in err.lower() or "mo" in err.lower()


# --- Nom unique dérivé du contenu ---

def test_deux_contenus_identiques_meme_nom():
    n1 = unique_image_filename(_PNG, "png")
    n2 = unique_image_filename(_PNG, "png")
    assert n1 == n2


def test_deux_contenus_differents_noms_differents():
    n1 = unique_image_filename(_PNG, "png")
    n2 = unique_image_filename(_JPG, "jpg")
    assert n1 != n2


def test_jpeg_normalise_vers_jpg():
    name = unique_image_filename(_JPG, "jpeg")
    assert name.endswith(".jpg")
    name2 = unique_image_filename(_JPG, "jpg")
    assert name == name2  # même contenu -> même nom canonique


# --- Constantes exportées ---

def test_extensions_autorisees_coherentes():
    # webp inclus (sûr, supporté navigateurs) ; svg exclu (XSS).
    assert "webp" in ALLOWED_IMAGE_EXTENSIONS
    assert "svg" not in ALLOWED_IMAGE_EXTENSIONS
    assert "png" in ALLOWED_IMAGE_EXTENSIONS
    assert "jpg" in ALLOWED_IMAGE_EXTENSIONS
    assert "jpeg" in ALLOWED_IMAGE_EXTENSIONS
    assert "gif" in ALLOWED_IMAGE_EXTENSIONS


def test_borne_nombre_fichiers_par_lot():
    assert isinstance(MAX_IMAGES_PER_UPLOAD, int)
    assert MAX_IMAGES_PER_UPLOAD > 0


# --- Le FileStorage est rembobiné après lecture ---

def test_file_rembobine_apres_validation():
    fs = _file("photo.png", _PNG)
    ok, err, result = accept_image_upload(fs)
    assert ok
    # Le curseur du stream est remis à 0 (seek(0) dans accept_image_upload).
    assert fs.stream.tell() == 0


# --- Miniatures ---

def test_thumbnail_filename_avec_prefixe():
    assert thumbnail_filename("abc123.jpg") == THUMBNAIL_PREFIX + "abc123.jpg"
    assert thumbnail_filename("photo.png") == THUMBNAIL_PREFIX + "photo.png"


def test_generate_thumbnail_produit_jpeg_redimensionne():
    """Une image PNG de 800x800 doit produire une miniature JPEG <= 300x300."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow non installé")
    # Créer une image avec du contenu varié.
    img = Image.new("RGB", (800, 800))
    pixels = img.load()
    for x in range(800):
        for y in range(800):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 5) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    original = buf.getvalue()
    thumb = generate_thumbnail(original)
    assert thumb is not None
    # La miniature est en JPEG (commence par \xff\xd8\xff).
    assert thumb[:3] == b"\xff\xd8\xff"
    # Les dimensions sont réduites à <= 300x300.
    thumb_img = Image.open(io.BytesIO(thumb))
    assert thumb_img.width <= 300
    assert thumb_img.height <= 300


def test_generate_thumbnail_respecte_max_size():
    """Une image 500x500 doit être redimensionnée à <= 300x300."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow non installé")
    buf = io.BytesIO()
    Image.new("RGB", (500, 500), (0, 128, 255)).save(buf, format="PNG")
    thumb = generate_thumbnail(buf.getvalue(), max_size=(300, 300))
    assert thumb is not None
    # Vérifier les dimensions de la miniature.
    thumb_img = Image.open(io.BytesIO(thumb))
    assert thumb_img.width <= 300
    assert thumb_img.height <= 300


def test_generate_thumbnail_replie_sur_none_si_image_invalide():
    """Des octets non-image doivent retourner None (repli sur l'originale)."""
    thumb = generate_thumbnail(b"ce n'est pas une image")
    assert thumb is None


def test_generate_thumbnail_convertit_rgba_vers_jpeg():
    """Une image avec transparence (RGBA) doit être convertie en RGB."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow non installé")
    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (255, 0, 0, 128)).save(buf, format="PNG")
    thumb = generate_thumbnail(buf.getvalue())
    assert thumb is not None
    # JPEG valide.
    assert thumb[:3] == b"\xff\xd8\xff"
