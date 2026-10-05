"""Cross-platform, traversal-safe filename handling."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

DEFAULT_MAX_BYTES = 180
"""UTF-8 byte budget for the stem. Leaves room for "001_" prefixes, " (2)"
suffixes and the extension under the 255-byte limit of common filesystems."""

FALLBACK_NAME = "Untitled"

_SEPARATOR_CHARS = re.compile(r"\s*[:|]\s*")
_SLASHES = re.compile(r"\s*[/\\]\s*")
_DROP_CHARS = re.compile(r'[?*"<>]')
_WHITESPACE = re.compile(r"\s+")
_DASH_RUNS = re.compile(r"(?:\s*-\s*){2,}")

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(title: str | None, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Turn a video title into a safe, readable filename stem (no extension).

    >>> sanitize_filename("What is Claude Code? | Complete Guide")
    'What is Claude Code - Complete Guide'
    >>> sanitize_filename("How to Build an AI Agent: Complete Guide?")
    'How to Build an AI Agent - Complete Guide'
    """
    if not title:
        return FALLBACK_NAME

    name = unicodedata.normalize("NFC", str(title))
    # Drop control, format (zero-width / bidi override) and other invisible chars.
    name = "".join(
        ch for ch in name if unicodedata.category(ch)[0] != "C" or ch in "\t\n"
    )
    name = _SEPARATOR_CHARS.sub(" - ", name)
    name = _SLASHES.sub("-", name)
    name = _DROP_CHARS.sub("", name)
    name = _WHITESPACE.sub(" ", name)
    name = _DASH_RUNS.sub(" - ", name)
    # Leading dots would create hidden files ("..", ".env"); trailing dots and
    # spaces are silently stripped by Windows. Remove both, plus stray dashes.
    name = name.strip(" .-")

    name = _truncate_utf8(name, max_bytes).rstrip(" .-")

    if not name:
        return FALLBACK_NAME
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = f"_{name}"
    return name


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def unique_path(
    directory: Path, stem: str, suffix: str = ".txt", taken: set[Path] | None = None
) -> Path:
    """Return ``directory/stem.suffix``, or ``stem (2).suffix`` etc. if that
    exists on disk or is in ``taken``."""
    taken = taken or set()
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists() or candidate in taken:
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def safe_join(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and refuse anything that escapes it."""
    base_resolved = base.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError("Resolved path escapes the base directory")
    return target
