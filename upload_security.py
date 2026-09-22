"""Validation bornée des téléversements non-image (audio, clé JSON).

Pendant de :pymod:`image_storage` pour les sons et les fichiers JSON sensibles.

Point audit — ``routes/admin_announce.py`` :

* l'upload de sons n'acceptait les fichiers que sur leur extension, puis les
  enregistrait directement — un ``.mp3`` contenant un script ou un HTML passait
  (le fichier est ensuite servi par ``/sounds/<filename>``). Pire :
  ``app.config["ALLOWED_AUDIO_EXTENSIONS"]`` n'existait pas, donc l'upload
  levait un ``KeyError`` — la fonctionnalité était cassée ;
* la clé Google était lue entièrement en mémoire (``file.read()`` sans borne)
  puis chiffrée telle quelle : ni taille limite, ni validation que le contenu
  est bien un JSON de compte de service — un gros blob arbitraire pouvait être
  stocké en base et casser ``get_google_credentials`` au premier usage.

API :

* :func:`accept_audio_upload` — extension en liste blanche + lecture bornée +
  reniffrage des octets magiques (WAV ``RIFF…WAVE``, MP3 ``ID3``/sync frame) ;
* :func:`validate_service_account_json` — JSON décodable, schéma « compte de
  service » (``type`` + clés requises non vides) et validation structurelle par
  ``google.oauth2.service_account`` (parse réel de la clé privée PEM).
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from werkzeug.datastructures import FileStorage

# Taille maximale d'un son d'annonce (16 Mo), alignée sur
# ``backup_service.MAX_DECODED_FILE_BYTES`` pour qu'un son restauré depuis une
# sauvegarde puisse être re-téléversé de façon cohérente.
MAX_AUDIO_BYTES = 16 * 1024 * 1024

# Taille maximale d'une clé JSON de compte de service Google (~64 Ko). Une clé
# réelle fait ~3 Ko ; la marge couvre les variantes à champs additionnels.
MAX_SERVICE_ACCOUNT_JSON_BYTES = 64 * 1024

# Extensions audio acceptées à l'upload. Alignées sur ce que la galerie sait
# servir (``gallery_audio_list`` filtre .wav/.mp3) et sur l'attribut ``accept``
# du formulaire. Remplace ``ALLOWED_AUDIO_EXTENSIONS`` qui n'existait pas en
# configuration (KeyError à chaque upload/suppression).
ALLOWED_AUDIO_EXTENSIONS: frozenset[str] = frozenset({"wav", "mp3"})

# Clés qu'un JSON de compte de service Google doit contenir, non vides.
# ``private_key``/``client_email``/``token_uri`` sont en outre exigées par
# google-auth ; ``type`` doit valoir exactement "service_account".
SERVICE_ACCOUNT_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "token_uri",
    }
)


def read_upload_bounded(
    file: FileStorage, *, max_bytes: int
) -> Tuple[bool, Optional[str], Optional[bytes]]:
    """Lit un fichier téléversé en bornant la quantité lue (anti-bombe).

    Lit au plus ``max_bytes + 1`` octets depuis le flux sous-jacent : au-delà,
    le fichier est refusé sans avoir été chargé entièrement en mémoire.
    Retourne ``(ok, error_message, data)`` — ``data`` prêt à écrire si ``ok``.
    """
    data = file.stream.read(max_bytes + 1)
    if not data:
        return False, "Fichier vide", None
    if len(data) > max_bytes:
        return (
            False,
            f"Fichier trop volumineux (>{max_bytes // 1024 // 1024} Mo)",
            None,
        )
    return True, None, data


def _declared_extension(filename: str) -> Optional[str]:
    """Extrait l'extension déclarée (lowercase, sans point) ou ``None``."""
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext else None


def sniff_audio_extension(data: bytes) -> Optional[str]:
    """Détecte le format réel d'un son à partir de ses premiers octets.

    Retourne ``"wav"``, ``"mp3"`` ou ``None``. WAV : en-tête conteneur RIFF
    marqué WAVE. MP3 : tag ID3v2 (``ID3``) ou sync de frame MPEG audio
    (``0xFF`` suivi de 11 bits à 1, octet 2 & 0xE0 == 0xE0).
    """
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    return None


def accept_audio_upload(
    file: FileStorage,
    *,
    max_bytes: int = MAX_AUDIO_BYTES,
) -> Tuple[bool, Optional[str], Optional[dict]]:
    """Valide et accepte un son téléversé (galerie des signaux d'annonce).

    Retourne ``(ok, error_message, result)`` où ``result`` (si ``ok``) est ::
        {"data": bytes, "ext": str}
    ``data`` est le contenu validé prêt à écrire ; ``ext`` est le format réel
    détecté (qui doit coïncider avec l'extension déclarée).
    """
    filename = file.filename or ""
    declared = _declared_extension(filename)
    if declared is None:
        return False, "Nom de fichier sans extension", None
    if declared not in ALLOWED_AUDIO_EXTENSIONS:
        return False, "Format de fichier non autorisé (wav, mp3)", None

    ok, error, data = read_upload_bounded(file, max_bytes=max_bytes)
    if not ok:
        return False, error, None

    detected = sniff_audio_extension(data)
    if detected is None:
        return False, "Format audio non reconnu", None
    if detected != declared:
        return (
            False,
            f"Le contenu ne correspond pas à l'extension .{declared}",
            None,
        )
    return True, None, {"data": data, "ext": detected}


def validate_service_account_json(
    data: bytes,
) -> Tuple[bool, Optional[str], Optional[bytes]]:
    """Valide un JSON de clé de compte de service Google Cloud.

    Contrôles successifs : JSON décodable → objet → ``type`` ==
    ``"service_account"`` → clés requises présentes et non vides → validation
    structurelle par google-auth (qui parse réellement la clé privée PEM).

    Retourne ``(ok, error_message, normalized_json_bytes)`` : le JSON
    normalisé est prêt à être chiffré puis stocké — ce qui garantit que la
    valeur en base sera toujours exploitable par ``get_google_credentials``.
    """
    try:
        text = data.decode("utf-8")
        info = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "Le fichier n'est pas un JSON valide", None

    if not isinstance(info, dict):
        return False, "Le fichier n'est pas un objet JSON", None

    if info.get("type") != "service_account":
        return False, "Le JSON n'est pas une clé de compte de service", None

    missing = [
        key
        for key in SERVICE_ACCOUNT_REQUIRED_KEYS
        if not isinstance(info.get(key), str) or not info.get(key).strip()
    ]
    if missing:
        return (
            False,
            "Clé de compte de service incomplète (champs manquants)",
            None,
        )

    # Validation structurelle : google-auth exige client_email/token_uri et
    # construit un signataire RSA depuis private_key — un PEM corrompu lève
    # ici, sans aucun appel réseau.
    try:
        from google.oauth2 import service_account

        service_account.Credentials.from_service_account_info(info)
    except Exception:
        return False, "Clé de compte de service invalide (clé privée illisible)", None

    return True, None, json.dumps(info).encode("utf-8")
