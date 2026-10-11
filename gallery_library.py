"""Noyau pur de la page « Galerie média » (aucune dépendance à Flask).

Une galerie est un dossier d'images sous ``static/galleries/<nom>``. Les
galeries cochées défilent en diaporama sur l'écran d'annonce. Ce module
regroupe les règles partagées par l'administration et par l'écran, pour que
les deux listent exactement les mêmes images, dans le même ordre :

- nom de galerie acceptable (``gallery_name_error``) ;
- nom de stockage d'une image, qui garde le nom d'origine lisible
  (``stored_image_name`` / ``image_label``) ;
- ordre des images, choisi par l'administrateur et conservé dans un petit
  fichier ``_order.json`` du dossier (``list_images`` / ``move_image``) ;
- phrase d'état du diaporama (``slideshow_summary``).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from image_storage import ALLOWED_IMAGE_EXTENSIONS, THUMBNAIL_DIR, thumbnail_filename

GALLERY_NAME_MAX = 40

# Fichier d'ordre, dans le dossier de la galerie. Absent ou illisible : les
# images sont simplement listées de la plus ancienne à la plus récente.
ORDER_FILENAME = "_order.json"

# Lettres (accents compris), chiffres, espace, tiret, underscore. Le nom sert
# de dossier ET de segment d'URL : pas de ponctuation à risque (# ? % / . …).
_NAME_RE = re.compile(r"^[^\W_]([\w \-]*[^\W_])?$", re.UNICODE)

# Noms réservés : par Windows (dossier inutilisable) et par l'adresse
# /admin/gallery/list, qui masquerait une galerie nommée « list ».
_RESERVED = {"list", "con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

_HASH_SUFFIX_RE = re.compile(r"^(?P<slug>.+)-(?P<digest>[0-9a-f]{16})$")
_HASH_ONLY_RE = re.compile(r"^[0-9a-f]{16,64}$")
_SLUG_MAX = 40


# ---------------------------------------------------------------------------
# Galeries
# ---------------------------------------------------------------------------

def normalize_gallery_name(name: Optional[str]) -> str:
    """Nom tel qu'il sera enregistré : espaces de bord retirés, espaces
    multiples réduits."""
    return " ".join((name or "").split())


def gallery_name_error(name: Optional[str], existing: Iterable[str] = ()) -> Optional[str]:
    """Message d'erreur (affichable) si ``name`` ne convient pas, sinon ``None``.

    ``existing`` : noms déjà pris. La comparaison ignore la casse — sous
    Windows, « Promo » et « promo » désignent le même dossier.
    """
    name = normalize_gallery_name(name)
    if not name:
        return "Le nom de la galerie doit être renseigné."
    if len(name) > GALLERY_NAME_MAX:
        return f"Le nom de la galerie ne peut pas dépasser {GALLERY_NAME_MAX} caractères."
    if not _NAME_RE.match(name):
        return ("Nom de galerie invalide : utilisez des lettres, des chiffres, "
                "des espaces ou des tirets.")
    if name.casefold() in _RESERVED or name.casefold() == THUMBNAIL_DIR:
        return "Ce nom est réservé : choisissez-en un autre."
    if name.casefold() in {other.casefold() for other in existing}:
        return "Une galerie porte déjà ce nom."
    return None


def list_galleries(base_dir: Path) -> list[str]:
    """Noms des galeries (sous-dossiers), triés sans tenir compte de la casse."""
    try:
        names = [p.name for p in Path(base_dir).iterdir() if p.is_dir()]
    except OSError:
        return []
    return sorted(names, key=str.casefold)


def parse_selection(raw: Optional[str]) -> list[str]:
    """Galeries cochées, depuis la valeur JSON enregistrée en configuration.

    Tolère une valeur absente ou abîmée (liste vide) : l'écran d'annonce ne
    doit jamais planter sur un réglage corrompu.
    """
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def is_image_name(name: str) -> bool:
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def stored_image_name(original_name: str, hashed_name: str) -> str:
    """Nom de stockage qui garde le nom d'origine lisible.

    ``hashed_name`` est le nom unique produit par ``accept_image_upload``
    (``<empreinte>.<ext>``). On le préfixe d'une version assainie du nom
    d'origine : ``Promo Été.JPG`` → ``promo-ete-<empreinte>.jpg``. Sans cela
    l'administrateur ne voyait que des empreintes, impossibles à distinguer.
    """
    stem = os.path.splitext(os.path.basename(original_name or ""))[0]
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_stem.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return f"{slug}-{hashed_name}" if slug else hashed_name


def content_digest(filename: str) -> Optional[str]:
    """Empreinte de contenu portée par un nom de stockage, ou ``None``."""
    stem = filename.rsplit(".", 1)[0]
    match = _HASH_SUFFIX_RE.match(stem)
    if match:
        return match.group("digest")
    if _HASH_ONLY_RE.match(stem):
        return stem[:16]
    return None


def image_label(filename: str) -> str:
    """Libellé affiché pour une image : son nom d'origine quand il est connu."""
    stem = filename.rsplit(".", 1)[0]
    match = _HASH_SUFFIX_RE.match(stem)
    if match and not _HASH_ONLY_RE.match(match.group("slug")):
        return match.group("slug").replace("-", " ")
    if match or _HASH_ONLY_RE.match(stem):
        return "Image sans nom"
    return stem


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")
    return f"{max(1, round(size / 1024))} Ko"


def read_order(gallery_dir: Path) -> list[str]:
    try:
        value = json.loads((Path(gallery_dir) / ORDER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def write_order(gallery_dir: Path, filenames: list[str]) -> None:
    """Enregistre l'ordre (écriture atomique : jamais de fichier à moitié écrit)."""
    target = Path(gallery_dir) / ORDER_FILENAME
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(filenames, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def list_images(gallery_dir: Path) -> list[dict]:
    """Images d'une galerie, dans l'ordre d'affichage.

    D'abord celles rangées par l'administrateur (``_order.json``), puis les
    autres de la plus ancienne à la plus récente : une image ajoutée arrive
    donc en fin de diaporama. Dossier absent ou illisible → liste vide.
    """
    gallery_dir = Path(gallery_dir)
    try:
        entries = [p for p in gallery_dir.iterdir() if p.is_file() and is_image_name(p.name)]
    except OSError:
        return []

    thumb_dir = gallery_dir / THUMBNAIL_DIR
    images = {}
    for path in entries:
        try:
            stat = path.stat()
        except OSError:
            continue
        thumb = thumb_dir / thumbnail_filename(path.name)
        images[path.name] = {
            "filename": path.name,
            "label": image_label(path.name),
            "size": stat.st_size,
            "size_label": human_size(stat.st_size),
            "mtime": stat.st_mtime,
            "thumbnail": thumb.name if thumb.is_file() else None,
        }

    ordered = [images.pop(name) for name in read_order(gallery_dir) if name in images]
    ordered.extend(sorted(images.values(), key=lambda img: (img["mtime"], img["filename"])))
    return ordered


def move_image(filenames: list[str], filename: str, direction: str) -> list[str]:
    """Nouvel ordre après déplacement d'une image d'un cran, au début ou à la
    fin. Renvoie la liste inchangée si le déplacement est impossible."""
    names = list(filenames)
    if filename not in names:
        return names
    index = names.index(filename)
    target = {
        "up": index - 1,
        "down": index + 1,
        "first": 0,
        "last": len(names) - 1,
    }.get(direction, index)
    target = max(0, min(len(names) - 1, target))
    names.insert(target, names.pop(index))
    return names


# ---------------------------------------------------------------------------
# Diaporama (écran d'annonce)
# ---------------------------------------------------------------------------

def slideshow_images(base_dir: Path, selected: Iterable[str]) -> list[tuple[str, str]]:
    """Couples ``(galerie, fichier)`` du diaporama : galeries cochées par ordre
    alphabétique, images dans l'ordre de chaque galerie."""
    result = []
    for gallery in sorted(set(selected), key=str.casefold):
        result.extend((gallery, img["filename"])
                      for img in list_images(Path(base_dir) / gallery))
    return result


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count <= 1 else (plural or singular + 's')}"


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min" + (f" {rest} s" if rest else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h" + (f" {minutes:02d}" if minutes else "")


def slideshow_summary(*, display_enabled: bool, selected_count: int,
                      image_count: int, display_time: int, shuffled: bool) -> dict:
    """État du diaporama, en clair : ``{level, icon, title, detail}``.

    ``level`` est une couleur Bootstrap (success / warning / secondary).
    """
    ready = (f"{_plural(image_count, 'image')} dans "
             f"{_plural(selected_count, 'galerie affichée', 'galeries affichées')}")
    if not display_enabled:
        return {
            "level": "secondary", "icon": "bi-pause-circle",
            "title": "Le diaporama est désactivé sur l'écran d'annonce.",
            "detail": (f"{ready} : elles défileront dès qu'il sera activé."
                       if image_count else
                       "Cochez une galerie ci-dessous, puis activez le diaporama."),
        }
    if selected_count == 0:
        return {
            "level": "warning", "icon": "bi-exclamation-triangle",
            "title": "Aucune galerie n'est affichée.",
            "detail": "L'écran consacre toute sa largeur aux appels. Cochez « Afficher à "
                      "l'écran » sur au moins une galerie pour lancer le diaporama.",
        }
    if image_count == 0:
        return {
            "level": "warning", "icon": "bi-exclamation-triangle",
            "title": "Les galeries affichées ne contiennent aucune image.",
            "detail": "L'écran consacre toute sa largeur aux appels. Ajoutez des images "
                      "pour lancer le diaporama.",
        }
    if image_count == 1:
        return {
            "level": "success", "icon": "bi-play-circle",
            "title": "1 image est affichée en continu à l'écran.",
            "detail": "Ajoutez d'autres images pour les faire défiler.",
        }
    display_time = max(1, int(display_time or 1))
    order = "dans un ordre mélangé" if shuffled else "dans l'ordre des galeries"
    return {
        "level": "success", "icon": "bi-play-circle",
        "title": f"{ready} : le diaporama est en cours à l'écran.",
        "detail": (f"Une image toutes les {_duration(display_time)}, {order} — "
                   f"un tour complet dure {_duration(display_time * image_count)}."),
    }
