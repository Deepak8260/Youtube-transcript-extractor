"""Shared fixtures and fakes. No test performs a real network request."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.config import Settings
from app.exceptions import ExtractorError, TranscriptUnavailableError
from app.models.domain import (
    ParsedURL,
    PlaylistEntry,
    PlaylistInfo,
    TranscriptResult,
    TranscriptSegment,
    VideoInfo,
)
from app.services.progress import ProgressCallback
from app.services.transcript_service import TranscriptProvider
from app.utils.url_parser import parse_youtube_url


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        output_dir=tmp_path / "transcripts",
        temp_dir=tmp_path / "temp",
        request_delay_seconds=0,
        retry_base_delay_seconds=0,
        max_retries=1,
        enable_whisper_fallback=False,
    )


def make_segments(count: int, step: float = 4.0, prefix: str = "line") -> list[TranscriptSegment]:
    return [TranscriptSegment(i * step, step, f"{prefix} {i}") for i in range(count)]


class FakeYouTube:
    """Stands in for YouTubeService."""

    ffmpeg_available = False

    def __init__(
        self,
        videos: dict[str, VideoInfo] | None = None,
        playlist: PlaylistInfo | None = None,
        errors: dict[str, ExtractorError] | None = None,
    ) -> None:
        self.videos = videos or {}
        self.playlist = playlist
        self.errors = errors or {}
        self.info_calls: list[str] = []

    def parse_url(self, url: str) -> ParsedURL:
        return parse_youtube_url(url)

    def get_video_info(self, video_id: str) -> VideoInfo:
        self.info_calls.append(video_id)
        if video_id in self.errors:
            raise self.errors[video_id]
        return self.videos.get(video_id) or VideoInfo(video_id, f"Video {video_id}", 40.0)

    def get_playlist(self, playlist_id: str) -> PlaylistInfo:
        assert self.playlist is not None
        return self.playlist


class FakeProvider(TranscriptProvider):
    """Returns canned transcripts per video id, or raises a canned error."""

    def __init__(
        self,
        name: str = "youtube",
        transcripts: dict[str, list[TranscriptSegment]] | None = None,
        errors: dict[str, Exception] | None = None,
        default: list[TranscriptSegment] | None = None,
        label: str = "YouTube Captions",
        available: bool = True,
    ) -> None:
        self.name = name
        self.kind = "asr" if name == "whisper" else "captions"
        self.transcripts = transcripts or {}
        self.errors = errors or {}
        self.default = default
        self.label = label
        self.available = available
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def get_transcript(
        self, video: VideoInfo, languages: Sequence[str], progress: ProgressCallback
    ) -> TranscriptResult:
        self.calls.append(video.video_id)
        if video.video_id in self.errors:
            raise self.errors[video.video_id]
        segments = self.transcripts.get(video.video_id, self.default)
        if segments is None:
            raise TranscriptUnavailableError()
        return TranscriptResult(
            segments=list(segments), source_label=self.label, provider=self.name,
            language_code="en", language_name="English",
        )


def playlist_of(*titles: str, unavailable: Sequence[int] = ()) -> PlaylistInfo:
    entries = []
    for i, title in enumerate(titles, start=1):
        vid = f"vid{i:08d}"[:11].ljust(11, "x")
        entries.append(PlaylistEntry(
            index=i, video_id=vid, title=title, duration=40.0,
            available=i not in unavailable,
            unavailable_reason="This video is private." if i in unavailable else None,
        ))
    return PlaylistInfo(playlist_id="PLtest1234567", title="Test Course", entries=entries)
