"""Resilient playlist processing with resume and bounded concurrency."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings
from app.models.domain import PlaylistEntry, PlaylistInfo, VideoInfo
from app.models.schemas import ItemStatus
from app.services.extraction_service import (
    ExtractionOptions,
    ExtractionService,
    VideoOutcome,
)
from app.services.job_manager import Job
from app.services.progress import Stage
from app.services.storage_service import StorageService
from app.services.youtube_service import YouTubeService
from app.utils.timestamps import format_duration

logger = logging.getLogger(__name__)


class PlaylistService:
    def __init__(
        self,
        settings: Settings,
        youtube: YouTubeService,
        extraction: ExtractionService,
        storage: StorageService,
    ) -> None:
        self._settings = settings
        self._youtube = youtube
        self._extraction = extraction
        self._storage = storage

    def run_playlist_job(self, job: Job, playlist_id: str, options: ExtractionOptions) -> None:
        self._extraction.begin_job()
        job.set_stage(Stage.FETCHING_INFO, "Fetching playlist videos...", 0.02)
        playlist = self._youtube.get_playlist(playlist_id)
        logger.info("Playlist detected: %s (%d videos)", playlist.title, len(playlist.entries))
        self._warn_if_truncated(job, playlist)

        job.set_items([(e.index, e.video_id, e.title) for e in playlist.entries], playlist.title)
        directory = self._storage.playlist_dir(playlist)
        existing = self._storage.index_directory(directory)
        width = max(3, len(str(playlist.entries[-1].index)))

        def work(pos: int, entry: PlaylistEntry) -> None:
            outcome = self._process_entry(job, pos, entry, directory, existing, width, options)
            self._extraction.apply_outcome(job, pos, outcome)

        # A small worker pool; each worker pauses between videos so we stay
        # well clear of YouTube rate limits. Every entry is attempted.
        with ThreadPoolExecutor(
            max_workers=self._settings.playlist_concurrency, thread_name_prefix="playlist"
        ) as pool:
            futures = [pool.submit(work, pos, entry) for pos, entry in enumerate(playlist.entries)]
            for future in futures:
                future.result()  # work() never raises; this just waits.

        job.finish()
        snap = job.snapshot().summary
        logger.info(
            "Playlist %s finished: %d ok, %d existing, %d unavailable, %d no transcript, %d failed",
            playlist_id, snap.succeeded, snap.existing, snap.unavailable, snap.no_transcript, snap.failed,
        )

    def _process_entry(
        self,
        job: Job,
        pos: int,
        entry: PlaylistEntry,
        directory: Path,
        existing: dict[str, Path],
        width: int,
        options: ExtractionOptions,
    ) -> VideoOutcome:
        if not entry.available:
            logger.info("Playlist item %d (%s) unavailable", entry.index, entry.video_id)
            return VideoOutcome(status=ItemStatus.UNAVAILABLE, message=entry.unavailable_reason)

        video = VideoInfo(entry.video_id, entry.title, entry.duration)
        previous = existing.get(entry.video_id)
        if previous is not None and not options.overwrite:
            return self._extraction.existing_outcome(previous, video)

        try:
            time.sleep(self._settings.request_delay_seconds)
            job.item_progress(pos, Stage.EXTRACTING, "Extracting YouTube captions...", 0.05)
            target = previous or self._storage.reserve_playlist_target(
                directory, entry.index, width, entry.title
            )
            try:
                return self._extraction.process_video(
                    video, target, options,
                    lambda stage, msg, frac=None: job.item_progress(pos, stage, msg, frac),
                )
            finally:
                self._storage.release(target)
        except Exception as exc:
            outcome = self._extraction.failure_outcome(entry.video_id, exc)
            if entry.duration:
                outcome.duration = format_duration(entry.duration)
            return outcome

    @staticmethod
    def _warn_if_truncated(job: Job, playlist: PlaylistInfo) -> None:
        if playlist.reported_count and playlist.reported_count > len(playlist.entries):
            message = (
                f"This playlist has {playlist.reported_count} videos; only the first "
                f"{len(playlist.entries)} were processed (MAX_PLAYLIST_VIDEOS)."
            )
            logger.warning(message)
            job.add_warning(message)
