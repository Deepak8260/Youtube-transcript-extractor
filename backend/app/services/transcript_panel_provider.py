"""Second caption source: YouTube's own "Show transcript" panel.

The watch page fills its transcript panel from ``youtubei/v1/get_panel``. That
is a different endpoint from the caption file server (``/api/timedtext``) that
youtube-transcript-api uses, and it often keeps working when the caption
server rate-limits or blocks an IP. The text is the same transcript a viewer
sees under "Show transcript", with each line's start time.

Limits compared with the caption track: start times are whole seconds (all the
``[HH:MM:SS]`` output needs), the track is whichever one YouTube shows by
default (language selection is not available here), and manual vs
auto-generated is not reported.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import requests

from app.config import Settings
from app.exceptions import (
    IncompleteTranscriptError,
    IpBlockedError,
    NetworkError,
    TranscriptUnavailableError,
    VideoUnavailableError,
)
from app.models.domain import TranscriptResult, TranscriptSegment, VideoInfo
from app.services.progress import ProgressCallback, Stage
from app.services.transcript_service import CircuitBreaker, TranscriptProvider
from app.utils.retry import with_retries
from app.utils.url_parser import is_valid_video_id

logger = logging.getLogger(__name__)

PANEL_ID = "PAmodern_transcript_view"
FALLBACK_CLIENT_VERSION = "2.20261002.01.00"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
_TIMESTAMP_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def panel_params(video_id: str) -> str:
    """The panel request's ``params``: the video id in a tiny protobuf
    (field 165 { field 1: video_id, field 3: 1 }), base64-encoded."""
    inner = b"\x0a" + bytes([len(video_id)]) + video_id.encode("ascii") + b"\x18\x01"
    return base64.b64encode(b"\xaa\x09" + bytes([len(inner)]) + inner).decode("ascii")


def parse_timestamp(value: str) -> int | None:
    """"8:05" -> 485, "1:02:03" -> 3723."""
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_panel(data: dict[str, Any]) -> tuple[list[tuple[int, str]], bool]:
    """Return ``([(start_seconds, text), ...], has_more_pages)`` from a
    ``get_panel`` response, in the order YouTube lists them."""
    lines: list[tuple[int, str]] = []
    has_more = False

    def walk(node: Any) -> None:
        nonlocal has_more
        if isinstance(node, dict):
            if "continuationItemRenderer" in node:
                has_more = True
            item = node.get("macroMarkersPanelItemViewModel")
            if isinstance(item, dict):
                line = _parse_item(item)
                if line is not None:
                    lines.append(line)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return lines, has_more


def _parse_item(item: dict[str, Any]) -> tuple[int, str] | None:
    timeline = (item.get("item") or {}).get("timelineItemViewModel") or {}
    texts = [
        (content.get("transcriptSegmentViewModel") or {}).get("simpleText", "")
        for content in timeline.get("contentItems") or []
    ]
    text = " ".join(t.strip() for t in texts if t and t.strip())
    if not text:
        return None
    endpoint = (((item.get("onTap") or {}).get("innertubeCommand") or {}).get("watchEndpoint") or {})
    start = endpoint.get("startTimeSeconds")
    if not isinstance(start, int):
        start = parse_timestamp(str(timeline.get("timestamp", "")))
    if start is None:
        return None
    return start, text


def to_segments(lines: Sequence[tuple[int, str]]) -> list[TranscriptSegment]:
    """Each line lasts until the next one starts. The last line's length is
    unknown and is left at 0 rather than invented."""
    ordered = sorted(lines, key=lambda line: line[0])
    segments = []
    for i, (start, text) in enumerate(ordered):
        end = ordered[i + 1][0] if i + 1 < len(ordered) else start
        segments.append(TranscriptSegment(float(start), float(max(0, end - start)), text))
    return segments


class YouTubeTranscriptPanelProvider(TranscriptProvider):
    name = "youtube_panel"
    kind = "captions"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._breaker = CircuitBreaker(settings.caption_block_cooldown_seconds, "transcript panel")

    def reset_pause(self) -> None:
        self._breaker.reset()

    def get_transcript(
        self,
        video: VideoInfo,
        languages: Sequence[str],
        progress: ProgressCallback,
    ) -> TranscriptResult:
        if not is_valid_video_id(video.video_id):
            raise VideoUnavailableError("Invalid video ID.")
        self._breaker.check()
        progress(Stage.EXTRACTING, "Reading YouTube's transcript panel...", 0.3)
        try:
            data = with_retries(
                lambda: self._fetch(video.video_id),
                attempts=self._settings.max_retries,
                base_delay=self._settings.retry_base_delay_seconds,
                description=f"transcript panel {video.video_id}",
            )
        except IpBlockedError:
            self._breaker.trip()
            raise

        lines, has_more = parse_panel(data)
        if has_more:
            # Never save part of a transcript as if it were the whole thing.
            raise IncompleteTranscriptError(
                "YouTube returned this transcript in pages, which is not supported yet, "
                "so it was not saved.",
                detail=f"continuation present for {video.video_id}",
            )
        if not lines:
            raise TranscriptUnavailableError("YouTube shows no transcript for this video.")

        logger.info("Video %s: transcript panel returned %d lines", video.video_id, len(lines))
        return TranscriptResult(
            segments=to_segments(lines),
            source_label="YouTube Transcript",
            provider=self.name,
        )

    # ---------------------------------------------------------------- HTTP

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        # Skip the EU cookie-consent interstitial, which has no page config.
        session.cookies.set("SOCS", "CAI", domain=".youtube.com")
        if self._settings.proxy_url:
            session.proxies = {"http": self._settings.proxy_url, "https": self._settings.proxy_url}
        return session

    def _fetch(self, video_id: str) -> dict[str, Any]:
        timeout = self._settings.network_timeout_seconds
        with self._session() as session:
            try:
                page = session.get(f"https://www.youtube.com/watch?v={video_id}", timeout=timeout)
                self._check(page)
                version, visitor = _page_config(page.text)
                client: dict[str, Any] = {"clientName": "WEB", "clientVersion": version, "hl": "en"}
                if visitor:
                    client["visitorData"] = visitor
                response = session.post(
                    "https://www.youtube.com/youtubei/v1/get_panel",
                    params={"prettyPrint": "false"},
                    json={"context": {"client": client}, "panelId": PANEL_ID,
                          "params": panel_params(video_id)},
                    headers={"X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": version,
                             "Origin": "https://www.youtube.com"},
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise NetworkError(detail=repr(exc)) from exc
            self._check(response)
            try:
                return response.json()
            except ValueError as exc:
                raise TranscriptUnavailableError(
                    "YouTube's transcript panel returned an unexpected response.", detail=repr(exc)
                ) from exc

    @staticmethod
    def _check(response: requests.Response) -> None:
        if response.status_code == 429:
            raise IpBlockedError(detail=f"HTTP 429 from {response.url.split('?')[0]}")
        if response.status_code in (400, 403, 404):
            raise TranscriptUnavailableError(
                "YouTube's transcript panel is not available for this video.",
                detail=f"HTTP {response.status_code}: {response.text[:200]}",
            )
        if response.status_code >= 400:
            raise NetworkError(detail=f"HTTP {response.status_code} from {response.url.split('?')[0]}")


def _page_config(html: str) -> tuple[str, str | None]:
    version = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    visitor = re.search(r'"VISITOR_DATA":"([^"]+)"', html)
    return (
        version.group(1) if version else FALLBACK_CLIENT_VERSION,
        json.loads(f'"{visitor.group(1)}"') if visitor else None,
    )
