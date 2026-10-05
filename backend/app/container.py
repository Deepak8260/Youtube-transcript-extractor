"""Wires services together. Routes receive this via FastAPI dependency injection,
and tests swap in fakes by building their own Container."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.config import Settings
from app.services.extraction_service import ExtractionService
from app.services.job_manager import JobManager
from app.services.playlist_service import PlaylistService
from app.services.storage_service import StorageService
from app.services.transcript_panel_provider import YouTubeTranscriptPanelProvider
from app.services.transcript_service import (
    TranscriptProvider,
    TranscriptService,
    YouTubeTranscriptProvider,
)
from app.services.whisper_service import WhisperService, WhisperTranscriptProvider
from app.services.youtube_service import YouTubeService


@dataclass
class Container:
    settings: Settings
    youtube: YouTubeService
    whisper: WhisperService
    transcripts: TranscriptService
    storage: StorageService
    extraction: ExtractionService
    playlists: PlaylistService
    jobs: JobManager


def build_container(
    settings: Settings,
    *,
    youtube: YouTubeService | None = None,
    providers: list[TranscriptProvider] | None = None,
) -> Container:
    youtube = youtube or YouTubeService(settings)
    whisper = WhisperService(settings)
    if providers is None:
        providers = [YouTubeTranscriptProvider(settings)]
        if settings.enable_transcript_panel:
            providers.append(YouTubeTranscriptPanelProvider(settings))
        if settings.enable_whisper_fallback:
            providers.append(WhisperTranscriptProvider(settings, youtube, whisper))
    transcripts = TranscriptService(settings, providers)
    storage = StorageService(settings)
    extraction = ExtractionService(settings, youtube, transcripts, storage)
    playlists = PlaylistService(settings, youtube, extraction, storage)
    jobs = JobManager(
        max_workers=settings.max_concurrent_jobs,
        retention=timedelta(hours=settings.job_retention_hours),
    )
    return Container(settings, youtube, whisper, transcripts, storage, extraction, playlists, jobs)
