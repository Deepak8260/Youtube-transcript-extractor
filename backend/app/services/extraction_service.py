"""Turns one video into one saved transcript file. Shared by single-video and
playlist jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.exceptions import (
    ExtractorError,
    TranscriptUnavailableError,
    VideoUnavailableError,
)
from app.models.domain import VideoInfo
from app.models.schemas import FileInfo, ItemStatus
from app.services.job_manager import Job
from app.services.progress import Stage
from app.services.storage_service import StorageService
from app.services.transcript_service import TranscriptService
from app.services.youtube_service import YouTubeService
from app.utils.formatter import format_transcript, parse_header
from app.utils.timestamps import format_duration, format_timestamp

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExtractionOptions:
    language: str
    overwrite: bool = False
    remove_non_speech: bool | None = None
    whisper_on_rate_limit: bool | None = None


@dataclass(slots=True)
class VideoOutcome:
    status: ItemStatus
    file: FileInfo | None = None
    transcript_source: str | None = None
    duration: str | None = None
    message: str | None = None
    error_code: str | None = None
    warnings: list[str] = field(default_factory=list)


def classify_error(exc: Exception) -> tuple[ItemStatus, str]:
    if isinstance(exc, VideoUnavailableError):
        return ItemStatus.UNAVAILABLE, exc.user_message
    if isinstance(exc, TranscriptUnavailableError):
        return ItemStatus.NO_TRANSCRIPT, exc.user_message
    if isinstance(exc, ExtractorError):
        return ItemStatus.FAILED, exc.user_message
    return ItemStatus.FAILED, "An unexpected error occurred while processing this video."


class ExtractionService:
    def __init__(
        self,
        settings: Settings,
        youtube: YouTubeService,
        transcripts: TranscriptService,
        storage: StorageService,
    ) -> None:
        self._settings = settings
        self._youtube = youtube
        self._transcripts = transcripts
        self._storage = storage

    # --------------------------------------------------------- single video

    def begin_job(self) -> None:
        """A new user request always gets a real attempt at YouTube captions."""
        self._transcripts.reset_pauses()

    def run_video_job(self, job: Job, video_id: str, options: ExtractionOptions) -> None:
        self.begin_job()
        job.set_items([(1, video_id, video_id)])
        job.item_progress(0, Stage.FETCHING_INFO, None, 0.05)
        try:
            video = self._youtube.get_video_info(video_id)
            logger.info("Video detected: %s (%s)", video.title, video_id)
            job.update_item(0, title=video.title, duration=format_duration(video.duration))
            target, existing = self._storage.reserve_single(video.title, video_id)
            try:
                if existing is not None and not options.overwrite:
                    outcome = self.existing_outcome(existing, video)
                else:
                    outcome = self.process_video(
                        video, target, options,
                        lambda stage, msg, frac=None: job.item_progress(0, stage, msg, frac),
                    )
            finally:
                self._storage.release(target)
        except Exception as exc:  # converted into an item result below
            outcome = self.failure_outcome(video_id, exc)
        self.apply_outcome(job, 0, outcome)
        job.finish()

    # -------------------------------------------------------------- shared

    def process_video(self, video: VideoInfo, target: Path, options: ExtractionOptions, progress) -> VideoOutcome:
        """Extract, verify, format and atomically save one transcript."""
        result = self._transcripts.get_transcript(
            video, progress,
            languages=[options.language],
            remove_non_speech=options.remove_non_speech,
            whisper_on_rate_limit=options.whisper_on_rate_limit,
        )
        progress(Stage.SAVING, "Saving transcript...", 0.97)
        duration = format_duration(video.duration)
        content = format_transcript(
            video, result,
            group_seconds=self._settings.segment_group_seconds,
            duration_display=format_timestamp(video.duration) if video.duration else None,
        )
        path = self._storage.write_atomic(target, content)
        return VideoOutcome(
            status=ItemStatus.SUCCESS,
            file=self.file_info(path, video.title, result.source_label, duration),
            transcript_source=result.source_label,
            duration=duration,
            warnings=list(result.warnings),
        )

    def existing_outcome(self, path: Path, video: VideoInfo) -> VideoOutcome:
        logger.info("Transcript for %s already exists at %s; skipping", video.video_id, path.name)
        source = None
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                source = parse_header(fh.read(4096)).get("Transcript Source")
        except OSError:
            pass
        duration = format_duration(video.duration)
        return VideoOutcome(
            status=ItemStatus.EXISTING,
            file=self.file_info(path, video.title, source, duration),
            transcript_source=source,
            duration=duration,
            message="Already extracted. Enable overwrite to extract it again.",
        )

    def file_info(self, path: Path, title: str, source: str | None, duration: str | None) -> FileInfo:
        stored = self._storage.register(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return FileInfo(file_id=stored.file_id, filename=stored.filename, title=title,
                        transcript_source=source, duration=duration, size_bytes=size)

    @staticmethod
    def failure_outcome(video_id: str, exc: Exception) -> VideoOutcome:
        status, message = classify_error(exc)
        if isinstance(exc, ExtractorError):
            logger.warning("Video %s failed: %s (%s)", video_id, exc.code, exc.summary)
        else:
            logger.exception("Video %s crashed", video_id)
        code = exc.code if isinstance(exc, ExtractorError) else "error"
        return VideoOutcome(status=status, message=message, error_code=code)

    @staticmethod
    def apply_outcome(job: Job, pos: int, outcome: VideoOutcome) -> None:
        fields = dict(status=outcome.status, file=outcome.file, message=outcome.message,
                      error_code=outcome.error_code,
                      transcript_source=outcome.transcript_source, warnings=outcome.warnings)
        if outcome.duration:
            fields["duration"] = outcome.duration
        job.update_item(pos, **fields)

