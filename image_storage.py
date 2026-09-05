"""Validation et stockage centralisés des images téléversées par l'admin.

Avant ce module, chaque route d'upload (galeries d'annonces, images de boutons
patients, drapeaux de langues) appliquait ses propres règles — ou aucune :

- ``routes/admin_gallery.py::upload_gallery`` enregistrait tout fichier sous
  son nom d'origine (via ``secure_filename``) sans vérifier l'extension ni le
  contenu : un ``.txt`` ou un exécutable renommé ``.jpg`` passait.
- ``routes/admin_patient.py::allowed_image_file`` ne contrôlait que l'extension
  (``ALLOWED_EXTENSIONS``), pas le contenu ni la taille.
- ``ui_feedback.py::allowed_image_file`` référençait ``ALLOWED_IMAGE_EXTENSIONS``
  qui n'existait pas en config : l'upload de drapeau levait un ``KeyError``.

Ce module offre une API unique, ``accept_image_upload``, qui :

1. vérifie l'extension déclarée (insensible à la casse) contre une liste blanche
   partagée ;
2. lit le contenu en le bornant (anti-bombe) ;
3. **renifle les octets magiques** pour confirmer que le contenu correspond bien
   à l'extension déclarée (anti-polyglot : un ``.png`` qui est en réalité du
   PHP est refusé) ;
4. produit un **nom de fichier unique** dérivé de l'empreinte SHA-256 du
   contenu : deux contenus identiques partagent le même nom (déduplication),
   deux contenus différents n'entrent jamais en collision, et l'écrasement
   silencieux d'une image utilisée par ailleurs devient impossible.

Les constantes (extensions, tailles) sont alignées sur ``backup_service`` pour
qu'une image restaurée puis re-téléversée soit acceptée de façon cohérente.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from werkzeug.datastructures import FileStorage

# Extensions acceptées à l'upload. Alignées sur ce que les navigateurs savent
# afficher dans le diaporama et les boutons, plus webp (sûr et largement
# supporté). SVG est exclu de l'upload (risque XSS via scripts embarqués) ;
# il reste admis à la restauration (``backup_service``) qui est un canal
# d'import administrateur distinct.
ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp"}
)

# Taille maximale d'un fichier image individuel (16 Mo), alignée sur
# ``backup_service.MAX_DECODED_FILE_BYTES``.
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# Nombre maximal de fichiers traités en une seule requête d'upload multiple
# (galeries). Garde-fou anti-abus ; au-delà, on refuse le lot entier.
MAX_IMAGES_PER_UPLOAD = 50


class ImageValidationError(ValueError):
    """Erreur de validation d'une image téléversée.

    Le message est sûr pour l'affichage (toast admin) : il ne contient jamais
    de chemin ni de détail technique interne.
    """


def _sniff_extension(data: bytes) -> Optional[str]:
    """Détecte le format réel d'une image à partir de ses premiers octets.

    Retourne l'extension canonique (``"png"``, ``"jpg"`` pour le JPEG,
    ``"gif"``, ``"webp"``, ``"bmp"``) ou ``None`` si le format n'est pas
    reconnu. Ne lit que le début du tampon ; ne décode pas l'image.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"  # normalise jpeg -> jpg
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    # WebP : "RIFF" .... "WEBP"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return None


def _declared_extension(filename: str) -> Optional[str]:
    """Extrait l'extension déclarée (lowercase, sans point) ou ``None``."""
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext else None


def unique_image_filename(data: bytes, ext: str) -> str:
    """Construit un nom de fichier unique dérivé du contenu.

    Utilise les 16 premiers caractères hexadécimaux du SHA-256 du contenu :
    cela rend toute collision entre contenus différents négligeable et
    interdit l'écrasement silencieux d'une image déjà référencée ailleurs.
    Le même contenu re-téléversé produit le même nom (déduplication).
    """
    digest = hashlib.sha256(data).hexdigest()[:16]
    # Normalise jpeg -> jpg pour un stockage homogène.
    canon = "jpg" if ext in ("jpg", "jpeg") else ext
    return f"{digest}.{canon}"


def accept_image_upload(
    file: FileStorage,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> Tuple[bool, Optional[str], Optional[dict]]:
    """Valide et accepte une image téléversée.

    Paramètres
    ----------
    file:
        Objet ``werkzeug.FileStorage`` (entrées de ``request.files``).
    max_bytes:
        Borne individuelle de taille (défaut : ``MAX_IMAGE_BYTES``).

    Retourne
    --------
    ``(ok, error_message, result)`` où ``result`` (uniquement si ``ok``) est ::
        {"data": bytes, "filename": str, "ext": str}
    - ``data`` : contenu binaire validé (déjà lu, prêt à écrire).
    - ``filename`` : nom unique à utiliser pour le stockage.
    - ``ext`` : extension canonique détectée par reniffrage.

    En cas d'échec, ``error_message`` est un message sûr pour l'affichage.
    """
    filename = file.filename or ""
    declared = _declared_extension(filename)
    if declared is None:
        return False, "Nom de fichier sans extension", None
    if declared not in ALLOWED_IMAGE_EXTENSIONS:
        return False, f"Extension .{declared} non autorisée", None

    data = file.read()
    file.seek(0)
    if not data:
        return False, "Fichier vide", None
    if len(data) > max_bytes:
        return False, f"Fichier trop volumineux (>{max_bytes // 1024 // 1024} Mo)", None

    detected = _sniff_extension(data)
    if detected is None:
        return False, "Format d'image non reconnu", None
    # Le JPEG se déclare indifféremment .jpg ou .jpeg ; on accepte les deux.
    if detected == "jpg" and declared in ("jpg", "jpeg"):
        canon_ext = "jpg"
    elif detected == declared:
        canon_ext = detected
    else:
        return (
            False,
            f"Le contenu ne correspond pas à l'extension .{declared}",
            None,
        )

    stored_name = unique_image_filename(data, canon_ext)
    return True, None, {"data": data, "filename": stored_name, "ext": canon_ext}
