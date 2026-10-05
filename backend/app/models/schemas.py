"""Pydantic models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import SourceKind
from app.services.progress import Stage


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    EXISTING = "existing"
    """A complete transcript for this video was already on disk (resume)."""
    UNAVAILABLE = "unavailable"
    """Private, deleted, age-restricted or otherwise inaccessible video."""
    NO_TRANSCRIPT = "no_transcript"
    FAILED = "failed"


class ExtractRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    language: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$",
        description="Preferred caption language code. Defaults to DEFAULT_LANGUAGE.",
    )
    overwrite: bool = Field(
        default=False, description="Re-extract even if a transcript already exists."
    )
    remove_non_speech: bool | None = Field(
        default=None, description="Strip cues like [Music]. Defaults to REMOVE_NON_SPEECH."
    )
    whisper_on_rate_limit: bool | None = Field(
        default=None,
        description="Use Whisper when YouTube blocks caption downloads. Defaults to WHISPER_ON_RATE_LIMIT.",
    )


class ZipRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=200, description="ZIP name (sanitized).")


class ExtractResponse(BaseModel):
    job_id: str
    type: SourceKind
    status: JobStatus


class FileInfo(BaseModel):
    file_id: str
    filename: str
    title: str
    transcript_source: str | None = None
    duration: str | None = None
    size_bytes: int


class JobItem(BaseModel):
    index: int
    video_id: str
    title: str
    status: ItemStatus
    stage_message: str | None = None
    transcript_source: str | None = None
    duration: str | None = None
    file: FileInfo | None = None
    message: str | None = None
    error_code: str | None = None
    warnings: list[str] = []


class JobSummary(BaseModel):
    total: int = 0
    succeeded: int = 0
    existing: int = 0
    unavailable: int = 0
    no_transcript: int = 0
    failed: int = 0
    pending: int = 0


class JobStatusResponse(BaseModel):
    job_id: str
    type: SourceKind
    status: JobStatus
    stage: Stage
    stage_message: str
    progress: int = Field(ge=0, le=100)
    current_video: str | None = None
    current_index: int | None = None
    total: int = 0
    playlist_title: str | None = None
    items: list[JobItem] = []
    files: list[FileInfo] = []
    summary: JobSummary
    error: str | None = None
    error_code: str | None = None
    warnings: list[str] = []
    zip_available: bool = False
    created_at: datetime
    finished_at: datetime | None = None


class ErrorResponse(BaseModel):
    error: str
    code: str


class HealthResponse(BaseModel):
    status: str
    whisper_available: bool
    whisper_model: str
    ffmpeg_available: bool
    default_language: str
