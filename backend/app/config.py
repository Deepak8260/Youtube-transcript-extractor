"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All tunables for the extractor. Every field maps to an upper-case env var."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage
    output_dir: Path = Field(default=BACKEND_ROOT / "transcripts")
    temp_dir: Path = Field(default=BACKEND_ROOT / "temp")

    # Transcript behaviour
    default_language: str = "en"
    allow_other_languages: bool = True
    """If no caption track exists in the requested language, use a track in the
    video's own language before falling back to Whisper."""
    remove_non_speech: bool = True
    segment_group_seconds: float = 0.0
    """0 = one timestamp per caption segment. >0 = losslessly join consecutive
    segments into blocks of at most this many seconds."""

    # Whisper fallback
    enable_whisper_fallback: bool = True
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3", "turbo"] = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "default"
    whisper_on_rate_limit: bool = False
    """Also fall back to Whisper when YouTube rate-limits caption requests.
    Off by default: rate limits are temporary and resume retries those videos."""
    enable_transcript_panel: bool = True
    """Fall back to the transcript shown in YouTube's "Show transcript" panel
    when the caption server fails or blocks this IP."""
    caption_block_cooldown_seconds: int = 900
    """After YouTube blocks a caption download, skip caption requests for this
    long (remaining videos go straight to Whisper or fail fast)."""
    whisper_language: str = ""
    """Force a Whisper language (e.g. "en"). Empty = auto-detect."""
    max_audio_duration_seconds: int = 4 * 60 * 60
    max_audio_filesize_mb: int = 500
    whisper_timeout_seconds: int = 3 * 60 * 60

    # Playlists / throughput
    max_playlist_videos: int = 500
    playlist_concurrency: int = Field(default=2, ge=1, le=8)
    request_delay_seconds: float = 0.5
    max_retries: int = 3
    retry_base_delay_seconds: float = 2.0
    network_timeout_seconds: int = 30
    proxy_url: str = ""
    """Optional HTTP(S) proxy for all YouTube traffic, e.g. http://user:pass@host:port.
    Useful when YouTube blocks this machine's IP."""
    max_concurrent_jobs: int = Field(default=2, ge=1, le=16)
    job_retention_hours: int = 24

    # HTTP
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    log_level: str = "INFO"

    @field_validator("output_dir", "temp_dir", mode="after")
    @classmethod
    def _resolve_dir(cls, value: Path) -> Path:
        path = value if value.is_absolute() else (BACKEND_ROOT / value)
        return path.resolve()

    @field_validator("default_language", "whisper_language", mode="after")
    @classmethod
    def _normalize_lang(cls, value: str) -> str:
        return value.strip().lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
