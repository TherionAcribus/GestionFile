"""Cache immutable des annonces vocales.

L'appel patient ne doit jamais dependre d'une synthese distante deja realisee.
Ce module est l'unique proprietaire du stockage des fichiers TTS : il construit
une cle a partir de *tout* ce qui influence le rendu, protege les generations
concurrentes et ne publie un fichier qu'une fois ecrit completement.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from flask import current_app


CACHE_FORMAT_VERSION = 1
MIN_AUDIO_SIZE_BYTES = 128

_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def _cache_directory() -> Path:
    directory = Path(current_app.static_folder) / "audio" / "annonces"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(*, text: str, provider: str, voice: str, language: str,
               voice_region: str | None = None) -> str:
    """Retourne une empreinte stable de tous les parametres audibles."""
    payload = {
        "version": CACHE_FORMAT_VERSION,
        "text": text,
        "provider": provider,
        "voice": voice,
        "language": language,
        "voice_region": voice_region or "",
        "encoding": "mp3",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_complete_audio(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_AUDIO_SIZE_BYTES
    except OSError:
        return False


def _lock_for(cache_key: str) -> threading.Lock:
    with _locks_guard:
        return _key_locks.setdefault(cache_key, threading.Lock())


def _public_url(filename: str) -> str:
    """URL relative, exploitable sans contexte de requete Flask."""
    static_root = (current_app.static_url_path or "/static").rstrip("/")
    return f"{static_root}/audio/annonces/{filename}"


def cached_announcement_url(*, text: str, provider: str, voice: str,
                            language: str, writer: Callable[[str], None],
                            voice_region: str | None = None) -> str:
    """Retourne l'URL d'une annonce complete, en la synthetisant si necessaire.

    ``writer`` recoit un chemin temporaire et doit y ecrire un MP3 complet. Le
    remplacement final est atomique, de sorte qu'un navigateur ne peut jamais
    telecharger un fichier en cours de creation.
    """
    key = _cache_key(text=text, provider=provider, voice=voice,
                     language=language, voice_region=voice_region)
    filename = f"{key}.mp3"
    final_path = _cache_directory() / filename

    if _is_complete_audio(final_path):
        # Le nettoyage conserve les annonces encore utilisees. Un echec de
        # touch n'affecte jamais la lecture d'un cache valide.
        try:
            os.utime(final_path, None)
        except OSError:
            current_app.logger.warning("Impossible de mettre a jour l'acces audio %s", final_path)
        current_app.logger.info("Audio announcement cache hit: %s", key)
        return _public_url(filename)

    with _lock_for(key):
        if _is_complete_audio(final_path):
            try:
                os.utime(final_path, None)
            except OSError:
                current_app.logger.warning("Impossible de mettre a jour l'acces audio %s", final_path)
            current_app.logger.info("Audio announcement cache hit after wait: %s", key)
            return _public_url(filename)

        temporary_path = final_path.with_name(
            f".{key}.{uuid.uuid4().hex}.tmp.mp3"
        )
        try:
            writer(os.fspath(temporary_path))
            if not _is_complete_audio(temporary_path):
                raise RuntimeError("Le fournisseur TTS a produit un fichier audio vide ou incomplet.")
            os.replace(temporary_path, final_path)
            current_app.logger.info("Audio announcement cache miss generated: %s", key)
        finally:
            # Si le fournisseur echoue, aucun residu ne doit etre servi au
            # prochain appel ni remplir le volume persistant.
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning("Impossible de supprimer le temporaire audio %s", temporary_path)

    return _public_url(filename)
