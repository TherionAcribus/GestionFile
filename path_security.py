from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union


class UnsafePathError(ValueError):
    pass


def validate_path_segment(segment: str, *, what: str = "path segment") -> str:
    if segment is None:
        raise UnsafePathError(f"Invalid {what}: missing value")

    if not isinstance(segment, str):
        raise UnsafePathError(f"Invalid {what}: not a string")

    if segment == "":
        raise UnsafePathError(f"Invalid {what}: empty")

    if "\x00" in segment:
        raise UnsafePathError(f"Invalid {what}: NUL byte")

    if "/" in segment or "\\" in segment:
        raise UnsafePathError(f"Invalid {what}: must not contain path separators")

    if ":" in segment:
        raise UnsafePathError(f"Invalid {what}: must not contain ':'")

    if segment in {".", ".."}:
        raise UnsafePathError(f"Invalid {what}: {segment!r} is not allowed")

    return segment


def to_abs_base_dir(base_dir: Union[str, Path], *, root_dir: Union[str, Path, None] = None) -> Path:
    base_path = Path(base_dir)
    if not base_path.is_absolute():
        if root_dir is None:
            base_path = base_path.resolve(strict=False)
        else:
            base_path = (Path(root_dir) / base_path).resolve(strict=False)
    else:
        base_path = base_path.resolve(strict=False)
    return base_path


def safe_path_under(base_dir: Union[str, Path], *parts: str, root_dir: Union[str, Path, None] = None) -> Path:
    base_path = to_abs_base_dir(base_dir, root_dir=root_dir)
    safe_parts: list[str] = [validate_path_segment(p) for p in parts]
    candidate = (base_path.joinpath(*safe_parts)).resolve(strict=False)
    if not candidate.is_relative_to(base_path):
        raise UnsafePathError("Resolved path escapes base directory")
    return candidate


def split_relative_path(rel_path: str, *, what: str = "path") -> list[str]:
    """Split a *relative* path string (which may embed subdirectories) into
    validated segments.

    Unlike :func:`safe_path_under`, which receives already-split segments, this
    accepts a single relative path as produced by ``os.path.relpath`` /
    ``os.path.join`` on either platform (so separators may be ``/`` or ``\\``).

    It rejects absolute paths, Windows drive letters and UNC paths, ``..``
    traversal, NUL bytes and any empty segment (``a//b``, ``a/``, ``/a``), and
    returns the list of clean segments. It does **not** touch the filesystem.
    """
    if rel_path is None:
        raise UnsafePathError(f"Invalid {what}: missing value")
    if not isinstance(rel_path, str):
        raise UnsafePathError(f"Invalid {what}: not a string")
    if rel_path == "":
        raise UnsafePathError(f"Invalid {what}: empty")
    if "\x00" in rel_path:
        raise UnsafePathError(f"Invalid {what}: NUL byte")

    # Normalise Windows separators so a path captured on either OS validates the
    # same way. A leading separator (POSIX-absolute or UNC ``\\server``) then
    # surfaces as an empty first segment and is rejected below.
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("/"):
        raise UnsafePathError(f"Invalid {what}: absolute paths are not allowed")

    segments = normalized.split("/")
    # validate_path_segment rejects '', '.', '..', ':' (drive letters) and stray
    # separators, so every dangerous form above is caught here.
    return [validate_path_segment(seg, what=what) for seg in segments]


def check_extension(
    name: str,
    allowed_extensions: Iterable[str],
    *,
    what: str = "path",
) -> None:
    """Raise :class:`UnsafePathError` unless ``name`` ends with one of
    ``allowed_extensions`` (compared lower-case, without leading dot)."""
    allowed = {ext.lower().lstrip(".") for ext in allowed_extensions}
    parts = name.rsplit(".", 1)
    if len(parts) != 2 or not parts[1] or parts[1].lower() not in allowed:
        raise UnsafePathError(f"Invalid {what}: file extension not allowed")


def safe_relative_path(
    base_dir: Union[str, Path],
    rel_path: str,
    *,
    root_dir: Union[str, Path, None] = None,
    allowed_extensions: Optional[Iterable[str]] = None,
    what: str = "path",
) -> Path:
    """Resolve a relative path (possibly containing subdirectories) strictly
    under ``base_dir``.

    Rejects absolute paths, drive letters, UNC paths, ``..`` traversal and any
    forbidden character (see :func:`split_relative_path`); optionally enforces an
    allowed file-extension whitelist. Guarantees the resolved path stays under
    ``base_dir`` even after symlink/normalisation.
    """
    segments = split_relative_path(rel_path, what=what)

    if allowed_extensions is not None:
        check_extension(segments[-1], allowed_extensions, what=what)

    base_path = to_abs_base_dir(base_dir, root_dir=root_dir)
    candidate = base_path.joinpath(*segments).resolve(strict=False)
    if not candidate.is_relative_to(base_path):
        raise UnsafePathError(f"Invalid {what}: resolved path escapes base directory")
    return candidate

