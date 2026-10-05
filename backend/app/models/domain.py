"""Internal domain objects shared between services (not part of the HTTP API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceKind(StrEnum):
    VIDEO = "video"
    PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class ParsedURL:
    kind: SourceKind
    video_id: str | None = None
    playlist_id: str | None = None


@dataclass(frozen=True, slots=True)
class VideoInfo:
    video_id: str
    title: str
    duration: float | None = None
    """Seconds. None when the source did not report it."""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    index: int
    """1-based position in the playlist."""
    video_id: str
    title: str
    duration: float | None = None
    available: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlaylistInfo:
    playlist_id: str
    title: str
    entries: list[PlaylistEntry]
    reported_count: int | None = None
    """Total videos YouTube reports, which may exceed len(entries) when capped."""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass(slots=True)
class TranscriptResult:
    segments: list[TranscriptSegment]
    source_label: str
    """Human-readable source, e.g. "YouTube Captions" or "Whisper (small)"."""
    provider: str
    """Machine id: "youtube" or "whisper"."""
    language_code: str | None = None
    language_name: str | None = None
    is_generated: bool = False
    warnings: list[str] = field(default_factory=list)
