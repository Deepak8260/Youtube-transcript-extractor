"""YouTube discovery: URL classification, metadata, playlists and audio download.

yt-dlp is used for everything here. No video files are downloaded; audio is
only fetched when the Whisper fallback explicitly asks for it.
"""

from __future__ import annotations

import json
import logging
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError
from yt_dlp.utils import ExtractorError as YtdlpExtractorError

from app.config import Settings
from app.exceptions import (
    AgeRestrictedError,
    ExtractionToolError,
    ExtractorError,
    NetworkError,
    PlaylistUnavailableError,
    PrivateVideoError,
    RateLimitedError,
    VideoUnavailableError,
    WhisperError,
)
from app.models.domain import ParsedURL, PlaylistEntry, PlaylistInfo, VideoInfo
from app.utils.retry import with_retries
from app.utils.url_parser import is_valid_video_id, parse_youtube_url

logger = logging.getLogger(__name__)

DownloadProgress = Callable[[float], None]
"""Receives a 0..1 fraction while audio downloads."""

_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]", "[unavailable video]"}


class _YtdlpLogger:
    """Route yt-dlp's chatter to our logger instead of stdout."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            logger.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        logger.info("yt-dlp error: %s", msg)


def map_ytdlp_error(message: str, *, playlist: bool = False) -> ExtractorError:
    """Translate a yt-dlp error message into a domain exception."""
    msg = message.lower()
    if "private video" in msg or "this video is private" in msg:
        return PrivateVideoError(detail=message)
    if "confirm your age" in msg or "age-restricted" in msg or "age restricted" in msg:
        return AgeRestrictedError(detail=message)
    if "429" in msg or "too many requests" in msg or "not a bot" in msg:
        return RateLimitedError(detail=message)
    if "members-only" in msg or "join this channel" in msg:
        return VideoUnavailableError("This video is for channel members only.", detail=message)
    if "premiere" in msg or "live event will begin" in msg or "is_upcoming" in msg:
        return VideoUnavailableError("This video has not been released yet.", detail=message)
    if (
        "video unavailable" in msg
        or "is not available" in msg
        or "has been removed" in msg
        or "has been terminated" in msg
        or "does not exist" in msg
        or "copyright" in msg
    ):
        if playlist:
            return PlaylistUnavailableError(detail=message)
        return VideoUnavailableError(detail=message)
    if (
        "unable to download" in msg
        or "timed out" in msg
        or "getaddrinfo" in msg
        or "connection" in msg
        or "network" in msg
        or "urlopen error" in msg
    ):
        return NetworkError(detail=message)
    if playlist:
        return PlaylistUnavailableError(detail=message)
    return ExtractionToolError(detail=message)


class YouTubeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ffmpeg = shutil.which("ffmpeg")

    @property
    def ffmpeg_available(self) -> bool:
        return self._ffmpeg is not None

    # ------------------------------------------------------------------ URLs

    def parse_url(self, url: str) -> ParsedURL:
        return parse_youtube_url(url)

    # -------------------------------------------------------------- metadata

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "socket_timeout": self._settings.network_timeout_seconds,
            "extractor_retries": 2,
            "logger": _YtdlpLogger(),
            "noplaylist": True,
            # YouTube's player challenges are solved with an external JS
            # runtime (via yt-dlp-ejs). yt-dlp enables only Deno by default;
            # Node is commonly installed locally, so allow both.
            "js_runtimes": {"deno": {}, "node": {}},
        }
        if self._ffmpeg:
            opts["ffmpeg_location"] = self._ffmpeg
        if self._settings.proxy_url:
            opts["proxy"] = self._settings.proxy_url
        return opts

    def _retry(self, func: Callable[[], Any], description: str) -> Any:
        return with_retries(
            func,
            attempts=self._settings.max_retries,
            base_delay=self._settings.retry_base_delay_seconds,
            description=description,
        )

    def get_video_info(self, video_id: str) -> VideoInfo:
        """Fetch the real title and duration of a video."""
        if not is_valid_video_id(video_id):
            raise VideoUnavailableError("Invalid video ID.")
        logger.info("Fetching metadata for video %s", video_id)
        try:
            return self._retry(lambda: self._ytdlp_video_info(video_id), f"metadata {video_id}")
        except (RateLimitedError, ExtractionToolError, NetworkError) as exc:
            # yt-dlp can be blocked ("confirm you're not a bot") while captions
            # still work. The public oEmbed endpoint gives us the real title.
            title = self._oembed_title(video_id)
            if title is None:
                raise
            logger.warning("yt-dlp metadata failed for %s (%s); using oEmbed title", video_id, exc.code)
            return VideoInfo(video_id=video_id, title=title, duration=None)

    def _ytdlp_video_info(self, video_id: str) -> VideoInfo:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(self._base_opts()) as ydl:
                # process=False skips format selection, which we do not need.
                info = ydl.extract_info(url, download=False, process=False)
        except (DownloadError, YtdlpExtractorError) as exc:
            raise map_ytdlp_error(str(exc)) from exc
        except OSError as exc:
            raise NetworkError(detail=str(exc)) from exc

        if not info:
            raise ExtractionToolError(detail="yt-dlp returned no info")
        availability = info.get("availability")
        if availability == "private":
            raise PrivateVideoError()
        if availability in ("premium_only", "subscriber_only"):
            raise VideoUnavailableError("This video requires a paid or members-only subscription.")
        if availability == "needs_auth" and info.get("age_limit"):
            raise AgeRestrictedError()
        if info.get("live_status") == "is_upcoming":
            raise VideoUnavailableError("This video has not been released yet.")

        title = (info.get("title") or "").strip() or video_id
        duration = info.get("duration")
        return VideoInfo(
            video_id=video_id,
            title=title,
            duration=float(duration) if isinstance(duration, (int, float)) else None,
        )

    def _oembed_title(self, video_id: str) -> str | None:
        query = urllib.parse.urlencode(
            {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
        )
        proxy = self._settings.proxy_url
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
        )
        try:
            with opener.open(
                f"https://www.youtube.com/oembed?{query}",
                timeout=self._settings.network_timeout_seconds,
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None
        title = data.get("title") if isinstance(data, dict) else None
        return title.strip() if isinstance(title, str) and title.strip() else None

    # -------------------------------------------------------------- playlists

    def get_playlist(self, playlist_id: str) -> PlaylistInfo:
        """List every video in a playlist, in playlist order, without downloading."""
        logger.info("Fetching playlist %s", playlist_id)
        return self._retry(lambda: self._ytdlp_playlist(playlist_id), f"playlist {playlist_id}")

    def _ytdlp_playlist(self, playlist_id: str) -> PlaylistInfo:
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        opts = self._base_opts() | {
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "playlistend": self._settings.max_playlist_videos,
            # Unavailable entries come back as flat placeholders anyway; real
            # failures (429, network) must raise so they are retried/reported.
            "ignoreerrors": "only_download",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except (DownloadError, YtdlpExtractorError) as exc:
            raise map_ytdlp_error(str(exc), playlist=True) from exc
        except OSError as exc:
            raise NetworkError(detail=str(exc)) from exc

        if not info:
            raise PlaylistUnavailableError()

        entries: list[PlaylistEntry] = []
        for position, raw in enumerate(info.get("entries") or [], start=1):
            entries.append(self._playlist_entry(position, raw))

        if not entries:
            raise PlaylistUnavailableError("This playlist is empty or could not be read.")

        count = info.get("playlist_count")
        return PlaylistInfo(
            playlist_id=playlist_id,
            title=(info.get("title") or "").strip() or playlist_id,
            entries=entries,
            reported_count=int(count) if isinstance(count, int) else None,
        )

    @staticmethod
    def _playlist_entry(position: int, raw: dict[str, Any] | None) -> PlaylistEntry:
        if not raw:
            return PlaylistEntry(position, "", f"Video {position}", available=False,
                                 unavailable_reason="This video is unavailable.")
        video_id = str(raw.get("id") or "")
        title = (raw.get("title") or "").strip()
        duration = raw.get("duration")
        duration_f = float(duration) if isinstance(duration, (int, float)) else None

        if not is_valid_video_id(video_id):
            return PlaylistEntry(position, video_id, title or f"Video {position}", duration_f,
                                 available=False, unavailable_reason="This video is unavailable.")
        lowered = title.lower()
        if lowered in _UNAVAILABLE_TITLES or raw.get("availability") == "private":
            reason = "This video is private." if "private" in lowered or raw.get("availability") == "private" \
                else "This video has been deleted or is unavailable."
            return PlaylistEntry(position, video_id, title or f"Video {position}", duration_f,
                                 available=False, unavailable_reason=reason)
        return PlaylistEntry(position, video_id, title or video_id, duration_f)

    # ----------------------------------------------------------------- audio

    def download_audio(
        self,
        video_id: str,
        dest_dir: Path,
        on_progress: DownloadProgress | None = None,
    ) -> Path:
        """Download the best audio-only stream (no video) into ``dest_dir``."""
        if not is_valid_video_id(video_id):
            raise VideoUnavailableError("Invalid video ID.")
        dest_dir.mkdir(parents=True, exist_ok=True)

        def hook(status: dict[str, Any]) -> None:
            if on_progress and status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                done = status.get("downloaded_bytes") or 0
                if total:
                    on_progress(min(1.0, done / total))

        opts = self._base_opts() | {
            "skip_download": False,
            "format": "bestaudio/best",
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
            "max_filesize": self._settings.max_audio_filesize_mb * 1024 * 1024,
            "progress_hooks": [hook],
            "retries": 3,
            "overwrites": True,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Downloading audio for %s", video_id)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except (DownloadError, YtdlpExtractorError) as exc:
            raise map_ytdlp_error(str(exc)) from exc
        except OSError as exc:
            raise NetworkError(detail=str(exc)) from exc

        path = self._downloaded_path(info, dest_dir, video_id)
        if path is None:
            raise WhisperError(
                "The audio for this video could not be downloaded (it may exceed the size limit).",
                detail=f"no audio file produced for {video_id}",
            )
        return path

    @staticmethod
    def _downloaded_path(info: dict[str, Any] | None, dest_dir: Path, video_id: str) -> Path | None:
        if info:
            for item in info.get("requested_downloads") or []:
                filepath = item.get("filepath")
                if filepath and Path(filepath).is_file():
                    return Path(filepath)
        candidates = [p for p in dest_dir.glob(f"{video_id}.*") if p.suffix != ".part"]
        return candidates[0] if candidates else None
