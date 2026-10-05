"""In-process background job execution and thread-safe job state.

The worker function is a plain ``Callable[[Job], None]``. Moving to Celery/RQ
means replacing ``JobManager.submit`` with an enqueue call and backing ``Job``
state with Redis; the extraction services do not change.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.exceptions import ExtractorError
from app.models.domain import SourceKind
from app.models.schemas import (
    FileInfo,
    ItemStatus,
    JobItem,
    JobStatus,
    JobStatusResponse,
    JobSummary,
)
from app.services.progress import Stage

logger = logging.getLogger(__name__)

_TERMINAL_ITEM = {
    ItemStatus.SUCCESS, ItemStatus.EXISTING, ItemStatus.UNAVAILABLE,
    ItemStatus.NO_TRANSCRIPT, ItemStatus.FAILED,
}

_STAGE_MESSAGES = {
    Stage.QUEUED: "Waiting to start...",
    Stage.DETECTING: "Detecting source...",
    Stage.FETCHING_INFO: "Fetching video information...",
    Stage.EXTRACTING: "Extracting transcript...",
    Stage.WHISPER_DOWNLOAD: "YouTube captions unavailable. Downloading audio...",
    Stage.WHISPER_TRANSCRIBE: "YouTube captions unavailable. Generating transcript with Whisper...",
    Stage.SAVING: "Saving transcript...",
    Stage.COMPLETED: "Done.",
    Stage.PARTIAL: "Done, with some videos skipped.",
    Stage.FAILED: "Failed.",
}


@dataclass(slots=True)
class _ItemState:
    index: int
    video_id: str
    title: str
    status: ItemStatus = ItemStatus.PENDING
    fraction: float = 0.0
    stage_message: str | None = None
    transcript_source: str | None = None
    duration: str | None = None
    file: FileInfo | None = None
    message: str | None = None
    error_code: str | None = None
    warnings: list[str] = field(default_factory=list)


class Job:
    """Mutable job state, safe to update from worker threads."""

    def __init__(self, kind: SourceKind, source_id: str) -> None:
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.source_id = source_id
        self.created_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self._lock = threading.RLock()
        self._status = JobStatus.QUEUED
        self._stage = Stage.QUEUED
        self._stage_message = _STAGE_MESSAGES[Stage.QUEUED]
        self._items: list[_ItemState] = []
        self._current: int | None = None
        self._playlist_title: str | None = None
        self._error: str | None = None
        self._error_code: str | None = None
        self._warnings: list[str] = []
        self._pre_fraction = 0.0

    # ------------------------------------------------------------ mutation

    @property
    def status(self) -> JobStatus:
        with self._lock:
            return self._status

    def set_stage(self, stage: Stage, message: str | None = None, fraction: float | None = None) -> None:
        """Job-level stage before any item exists (e.g. listing a playlist)."""
        with self._lock:
            self._status = JobStatus.PROCESSING
            self._stage = stage
            self._stage_message = message or _STAGE_MESSAGES[stage]
            if fraction is not None:
                self._pre_fraction = fraction

    def set_items(self, items: list[tuple[int, str, str]], playlist_title: str | None = None) -> None:
        with self._lock:
            self._items = [_ItemState(i, vid, title) for i, vid, title in items]
            self._playlist_title = playlist_title

    def add_warning(self, warning: str) -> None:
        with self._lock:
            self._warnings.append(warning)

    def item_progress(self, pos: int, stage: Stage, message: str | None, fraction: float | None) -> None:
        """Progress callback for the item at list position ``pos``."""
        with self._lock:
            item = self._items[pos]
            if item.status in _TERMINAL_ITEM:
                return
            item.status = ItemStatus.PROCESSING
            item.stage_message = message or _STAGE_MESSAGES[stage]
            if fraction is not None:
                item.fraction = max(item.fraction, min(1.0, fraction))
            self._status = JobStatus.PROCESSING
            self._stage = stage
            self._stage_message = item.stage_message
            self._current = pos

    def update_item(self, pos: int, **fields) -> None:
        with self._lock:
            item = self._items[pos]
            for key, value in fields.items():
                setattr(item, key, value)
            if item.status in _TERMINAL_ITEM:
                item.fraction = 1.0
                item.stage_message = None

    def finish(self) -> None:
        """Derive the final status from item outcomes."""
        with self._lock:
            ok = sum(i.status in (ItemStatus.SUCCESS, ItemStatus.EXISTING) for i in self._items)
            total = len(self._items)
            if total and ok == total:
                self._status, self._stage = JobStatus.COMPLETED, Stage.COMPLETED
            elif ok:
                self._status, self._stage = JobStatus.PARTIAL, Stage.PARTIAL
            else:
                self._status, self._stage = JobStatus.FAILED, Stage.FAILED
                if self._error is None:
                    first = next((i.message for i in self._items if i.message), None)
                    self._error = first or "No transcripts could be extracted."
            self._stage_message = _STAGE_MESSAGES[self._stage]
            self._current = None
            self.finished_at = datetime.now(UTC)

    def fail(self, error: ExtractorError) -> None:
        with self._lock:
            self._status, self._stage = JobStatus.FAILED, Stage.FAILED
            self._stage_message = _STAGE_MESSAGES[Stage.FAILED]
            self._error = error.user_message
            self._error_code = error.code
            self.finished_at = datetime.now(UTC)

    # ------------------------------------------------------------- reading

    def file_ids(self) -> list[str]:
        with self._lock:
            return [i.file.file_id for i in self._items if i.file]

    def snapshot(self) -> JobStatusResponse:
        with self._lock:
            items = [
                JobItem(
                    index=i.index, video_id=i.video_id, title=i.title, status=i.status,
                    stage_message=i.stage_message, transcript_source=i.transcript_source,
                    duration=i.duration, file=i.file, message=i.message, error_code=i.error_code,
                    warnings=list(i.warnings),
                )
                for i in self._items
            ]
            summary = JobSummary(
                total=len(items),
                succeeded=sum(i.status == ItemStatus.SUCCESS for i in items),
                existing=sum(i.status == ItemStatus.EXISTING for i in items),
                unavailable=sum(i.status == ItemStatus.UNAVAILABLE for i in items),
                no_transcript=sum(i.status == ItemStatus.NO_TRANSCRIPT for i in items),
                failed=sum(i.status == ItemStatus.FAILED for i in items),
                pending=sum(i.status in (ItemStatus.PENDING, ItemStatus.PROCESSING) for i in items),
            )
            files = [i.file for i in items if i.file]
            current = self._items[self._current] if self._current is not None else None
            return JobStatusResponse(
                job_id=self.id,
                type=self.kind,
                status=self._status,
                stage=self._stage,
                stage_message=self._stage_message,
                progress=self._progress(),
                current_video=current.title if current else None,
                current_index=current.index if current else None,
                total=len(items),
                playlist_title=self._playlist_title,
                items=items,
                files=files,
                summary=summary,
                error=self._error,
                error_code=self._error_code,
                warnings=list(self._warnings),
                zip_available=self.kind == SourceKind.PLAYLIST and bool(files)
                and self._status in (JobStatus.COMPLETED, JobStatus.PARTIAL),
                created_at=self.created_at,
                finished_at=self.finished_at,
            )

    def _progress(self) -> int:
        if self._status in (JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED):
            return 100
        if not self._items:
            return int(self._pre_fraction * 100)
        # Listing the playlist counts as the first 5%; items share the rest.
        base = 0.05 if self.kind == SourceKind.PLAYLIST else 0.0
        done = sum(i.fraction for i in self._items) / len(self._items)
        return min(99, int((base + (1 - base) * done) * 100))


class JobManager:
    """Runs jobs on a bounded thread pool and keeps recent job state in memory."""

    def __init__(self, max_workers: int, retention: timedelta) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._retention = retention

    def create(self, kind: SourceKind, source_id: str) -> Job:
        job = Job(kind, source_id)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job
        return job

    def find_active(self, kind: SourceKind, source_id: str) -> Job | None:
        """A queued or running job for the same video/playlist, if any."""
        with self._lock:
            for job in self._jobs.values():
                if (job.kind == kind and job.source_id == source_id
                        and job.status in (JobStatus.QUEUED, JobStatus.PROCESSING)):
                    return job
        return None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, job: Job, worker: Callable[[Job], None]) -> None:
        def run() -> None:
            logger.info("Job %s started (%s %s)", job.id, job.kind, job.source_id)
            job.set_stage(Stage.DETECTING)
            try:
                worker(job)
            except ExtractorError as exc:
                logger.warning("Job %s failed: %s (%s)", job.id, exc.code, exc.summary)
                job.fail(exc)
            except Exception:
                logger.exception("Job %s crashed", job.id)
                job.fail(ExtractorError("An unexpected error occurred. Check the server logs."))
            else:
                logger.info("Job %s completed with status %s", job.id, job.status)

        self._executor.submit(run)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - self._retention
        stale = [jid for jid, job in self._jobs.items()
                 if job.finished_at is not None and job.finished_at < cutoff]
        for jid in stale:
            del self._jobs[jid]
