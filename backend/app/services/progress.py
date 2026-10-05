"""Processing stages and the progress callback signature shared by services."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class Stage(StrEnum):
    QUEUED = "queued"
    DETECTING = "detecting_source"
    FETCHING_INFO = "fetching_info"
    EXTRACTING = "extracting"
    WHISPER_DOWNLOAD = "whisper_download"
    WHISPER_TRANSCRIBE = "whisper_transcribe"
    SAVING = "saving"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ProgressCallback(Protocol):
    def __call__(self, stage: Stage, message: str, fraction: float | None = None) -> None:
        """``fraction`` is progress (0..1) through the *current video*, if known."""


def null_progress(stage: Stage, message: str, fraction: float | None = None) -> None:
    return None
