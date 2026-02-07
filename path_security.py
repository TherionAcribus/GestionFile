from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union


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

