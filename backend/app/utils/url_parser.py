"""Strict YouTube URL parsing and validation.

Only well-known YouTube hosts are accepted and every extracted ID is validated
against a whitelist regex, so nothing user-supplied ever reaches yt-dlp or the
filesystem unchecked.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.exceptions import InvalidURLError, UnsupportedURLError
from app.models.domain import ParsedURL, SourceKind

MAX_URL_LENGTH = 2048

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Real playlist ids are prefixed (PL, UU, OL, RD, ...) and well over 10 chars.
# Short personal lists such as "WL" (Watch Later) or "LL" (Liked) need a login.
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_ID_PREFIXES = ("shorts", "embed", "live", "v", "e")


def is_valid_video_id(value: str | None) -> bool:
    return bool(value) and bool(VIDEO_ID_RE.match(value))


def is_valid_playlist_id(value: str | None) -> bool:
    return bool(value) and bool(PLAYLIST_ID_RE.match(value))


def parse_youtube_url(raw_url: str) -> ParsedURL:
    """Classify a URL as a single video or a playlist and extract its id.

    A ``watch?v=...&list=...`` URL is treated as the single video the user was
    watching; paste the ``/playlist?list=...`` URL to process a whole playlist.

    Raises:
        InvalidURLError: the input is not a parseable http(s) URL.
        UnsupportedURLError: the URL is valid but not a supported YouTube link.
    """
    if not isinstance(raw_url, str):
        raise InvalidURLError()
    url = raw_url.strip()
    if not url or len(url) > MAX_URL_LENGTH or any(c.isspace() for c in url):
        raise InvalidURLError()

    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidURLError(detail=str(exc)) from exc

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InvalidURLError()

    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    path_parts = [p for p in parsed.path.split("/") if p]

    if host in _SHORT_HOSTS:
        if path_parts and is_valid_video_id(path_parts[0]):
            return ParsedURL(SourceKind.VIDEO, video_id=path_parts[0])
        raise UnsupportedURLError("This youtu.be link does not contain a valid video ID.")

    if host not in _YOUTUBE_HOSTS:
        raise UnsupportedURLError()

    video_id = _first(query.get("v"))
    playlist_id = _first(query.get("list"))

    if path_parts[:1] == ["playlist"]:
        if is_valid_playlist_id(playlist_id):
            return ParsedURL(SourceKind.PLAYLIST, playlist_id=playlist_id)
        if playlist_id in ("WL", "LL", "LM"):
            raise UnsupportedURLError(
                "Personal playlists such as Watch Later or Liked Videos require "
                "signing in and are not supported."
            )
        raise UnsupportedURLError("This playlist URL does not contain a valid playlist ID.")

    if path_parts[:1] == ["watch"]:
        if is_valid_video_id(video_id):
            return ParsedURL(SourceKind.VIDEO, video_id=video_id)
        raise UnsupportedURLError("This video URL does not contain a valid video ID.")

    if len(path_parts) >= 2 and path_parts[0] in _PATH_ID_PREFIXES:
        if is_valid_video_id(path_parts[1]):
            return ParsedURL(SourceKind.VIDEO, video_id=path_parts[1])
        raise UnsupportedURLError("This URL does not contain a valid video ID.")

    raise UnsupportedURLError(
        "Only YouTube video and playlist URLs are supported "
        "(channel, search and home page links are not)."
    )


def _first(values: list[str] | None) -> str | None:
    return values[0].strip() if values else None
