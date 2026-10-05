"""Timestamp helpers. Timestamps are always taken from the source, never invented."""

from __future__ import annotations

import math


def format_timestamp(seconds: float | int | None) -> str:
    """Format seconds as ``HH:MM:SS`` (floored, so a segment never appears to
    start later than it does). Negative/invalid input clamps to 00:00:00."""
    total = _whole_seconds(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds: float | int | None) -> str | None:
    """Human duration: ``42:18`` or ``1:02:03``. None if unknown."""
    if seconds is None:
        return None
    total = _whole_seconds(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _whole_seconds(seconds: float | int | None) -> int:
    if seconds is None:
        return 0
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0
    if math.isnan(value) or value < 0:
        return 0
    if math.isinf(value):
        return 0
    return int(math.floor(value))
