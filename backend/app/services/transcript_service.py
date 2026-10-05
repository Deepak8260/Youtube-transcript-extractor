"""Transcript retrieval: provider abstraction, YouTube captions, cleaning, verification.

Flow::

    YouTube captions --available--> clean --> verify --> TranscriptResult
          | unavailable
          v
    Whisper (download audio -> faster-whisper) --> clean --> verify --> TranscriptResult
"""

from __future__ import annotations

import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence

import requests
from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    FetchedTranscript,
    InvalidVideoId,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    Transcript,
    TranscriptList,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from app.config import Settings
from app.exceptions import (
    AgeRestrictedError,
    ExtractorError,
    IpBlockedError,
    NetworkError,
    RateLimitedError,
    TranscriptNotFoundError,
    TranscriptsDisabledError,
    TranscriptUnavailableError,
    VideoUnavailableError,
    WhisperError,
    WhisperNotInstalledError,
)
from app.models.domain import TranscriptResult, TranscriptSegment, VideoInfo
from app.services.progress import ProgressCallback, Stage
from app.utils.retry import with_retries
from app.utils.timestamps import format_timestamp

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #


class TranscriptProvider(ABC):
    """A source of timestamped transcript segments for a single video."""

    name: str = "provider"
    kind: str = "captions"
    """"captions" = YouTube's own transcript text; "asr" = speech recognition."""

    def is_available(self) -> bool:
        return True

    @abstractmethod
    def get_transcript(
        self,
        video: VideoInfo,
        languages: Sequence[str],
        progress: ProgressCallback,
    ) -> TranscriptResult:
        """Return raw segments (cleaning happens in TranscriptService).

        Raises TranscriptUnavailableError when this provider has nothing usable,
        which lets the service try the next provider.
        """


# --------------------------------------------------------------------------- #
# YouTube captions
# --------------------------------------------------------------------------- #


class CircuitBreaker:
    """Once YouTube blocks an endpoint, stop calling it for a cooldown so the
    block is not extended. Shared by all jobs; each new job resets it so the
    user's retry always gets one real attempt."""

    def __init__(self, cooldown_seconds: float, label: str) -> None:
        self._cooldown = cooldown_seconds
        self._label = label
        self._open_until = 0.0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            paused = time.monotonic() < self._open_until
        if paused:
            raise IpBlockedError(detail=f"{self._label} requests paused: YouTube blocked this IP recently")

    def trip(self) -> None:
        with self._lock:
            self._open_until = time.monotonic() + self._cooldown
        logger.warning("YouTube is blocking %s requests; pausing them for %ds", self._label, self._cooldown)

    def reset(self) -> None:
        with self._lock:
            self._open_until = 0.0


class _TimeoutSession(requests.Session):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


class YouTubeTranscriptProvider(TranscriptProvider):
    """Reads YouTube-provided caption tracks via youtube-transcript-api.

    Selection order (first match wins):
      1. manually created track in a requested language
      2. auto-generated track in a requested language
      3. (if ALLOW_OTHER_LANGUAGES) manual track in any language
      4. (if ALLOW_OTHER_LANGUAGES) auto-generated track in any language
    """

    name = "youtube"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._breaker = CircuitBreaker(settings.caption_block_cooldown_seconds, "caption")

    def reset_pause(self) -> None:
        self._breaker.reset()

    def _api(self) -> YouTubeTranscriptApi:
        proxy = self._settings.proxy_url
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None,
            http_client=_TimeoutSession(self._settings.network_timeout_seconds),
        )

    def _retry(self, func, description: str):
        return with_retries(
            func,
            attempts=self._settings.max_retries,
            base_delay=self._settings.retry_base_delay_seconds,
            description=description,
        )

    def get_transcript(
        self,
        video: VideoInfo,
        languages: Sequence[str],
        progress: ProgressCallback,
    ) -> TranscriptResult:
        self._breaker.check()
        try:
            return self._fetch(video, languages, progress)
        except IpBlockedError:
            self._breaker.trip()
            raise

    def _fetch(
        self,
        video: VideoInfo,
        languages: Sequence[str],
        progress: ProgressCallback,
    ) -> TranscriptResult:
        progress(Stage.EXTRACTING, "Extracting YouTube captions...", 0.2)
        api = self._api()

        transcript_list: TranscriptList = self._retry(
            lambda: _call(lambda: api.list(video.video_id)),
            f"caption list {video.video_id}",
        )
        transcript = select_transcript(
            list(transcript_list), languages, allow_other=self._settings.allow_other_languages
        )
        if transcript is None:
            available = ", ".join(sorted({t.language_code for t in transcript_list})) or "none"
            raise TranscriptNotFoundError(
                f"No caption track in {', '.join(languages)} (available: {available}).",
            )

        logger.info(
            "Video %s: using %s caption track %s",
            video.video_id,
            "auto-generated" if transcript.is_generated else "manual",
            transcript.language_code,
        )
        progress(Stage.EXTRACTING, "Downloading caption track...", 0.4)
        fetched: FetchedTranscript = self._retry(
            lambda: _call(transcript.fetch),
            f"caption fetch {video.video_id}",
        )

        segments = [
            TranscriptSegment(float(s.start), float(s.duration), s.text)
            for s in fetched.snippets
        ]
        if not segments:
            raise TranscriptUnavailableError("The caption track for this video is empty.")

        return TranscriptResult(
            segments=segments,
            source_label=(
                "YouTube Captions (auto-generated)" if fetched.is_generated else "YouTube Captions"
            ),
            provider=self.name,
            language_code=fetched.language_code,
            language_name=_clean_language_name(fetched.language),
            is_generated=fetched.is_generated,
        )


def select_transcript(
    transcripts: Sequence[Transcript], languages: Sequence[str], *, allow_other: bool
) -> Transcript | None:
    """Pick the best caption track. ``"en"`` also matches ``"en-US"``, ``"en-GB"``..."""

    def matches(code: str, lang: str) -> bool:
        code, lang = code.lower(), lang.lower()
        return code == lang or code.startswith(lang + "-")

    manual = [t for t in transcripts if not t.is_generated]
    generated = [t for t in transcripts if t.is_generated]

    for pool in (manual, generated):
        for lang in languages:
            for t in pool:
                if matches(t.language_code, lang):
                    return t
    if allow_other:
        for pool in (manual, generated):
            if pool:
                return pool[0]
    return None


def _call(func):
    """Run a youtube-transcript-api call, translating its exceptions."""
    try:
        return func()
    except TranscriptsDisabled as exc:
        raise TranscriptsDisabledError(detail=str(exc)) from exc
    except NoTranscriptFound as exc:
        raise TranscriptNotFoundError(detail=str(exc)) from exc
    except AgeRestricted as exc:
        raise AgeRestrictedError(detail=str(exc)) from exc
    except (VideoUnavailable, InvalidVideoId) as exc:
        raise VideoUnavailableError(detail=str(exc)) from exc
    except VideoUnplayable as exc:
        raise VideoUnavailableError(detail=str(exc)) from exc
    except RequestBlocked as exc:  # includes IpBlocked
        raise IpBlockedError(detail=str(exc)) from exc
    except YouTubeRequestFailed as exc:
        if "429" in str(exc):
            raise RateLimitedError(detail=str(exc)) from exc
        raise NetworkError(detail=str(exc)) from exc
    except PoTokenRequired as exc:
        raise TranscriptUnavailableError(
            "YouTube requires extra verification to read captions for this video.",
            detail=str(exc),
        ) from exc
    except CouldNotRetrieveTranscript as exc:
        raise TranscriptUnavailableError(detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise NetworkError(detail=str(exc)) from exc


def _clean_language_name(name: str | None) -> str | None:
    if not name:
        return None
    return re.sub(r"\s*\(auto-generated\)\s*$", "", name, flags=re.IGNORECASE).strip() or None


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #

# Words that, when they make up the *entire* content of a bracketed cue, mark
# it as non-speech. Includes the localized tags YouTube uses in auto captions.
_NOISE_WORDS = {
    "music", "musical", "musique", "música", "musica", "musik", "संगीत",
    "applause", "applauding", "aplausos", "applaudissements", "applaus", "तालियाँ",
    "laughter", "laughs", "laughing", "laugh", "risas", "rires", "lachen", "हंसी",
    "cheering", "cheers", "cheer", "clapping", "claps", "whistling", "whistles",
    "silence", "noise", "noises", "sound", "sounds", "sfx", "foreign",
    "background", "upbeat", "soft", "gentle", "dramatic", "intro", "outro",
    "playing", "plays", "instrumental", "electronic", "ambient", "piano", "guitar",
    "sighs", "coughs", "coughing", "breathing", "and", "&", "continues",
    # Hindi auto-caption cues, e.g. [घंटी की आवाज़] (bell), [गाना गाने की आवाज़] (singing)
    "आवाज़", "आवाज", "घंटी", "गाना", "गाने", "तालियां", "हंसने", "हँसी", "की", "का", "के",
}
_BRACKETED = re.compile(r"[\[\(]\s*([^\[\]()]{0,60}?)\s*[\])]")
_MUSIC_SYMBOLS = re.compile(r"[♪♫♬♩]+")
_WHITESPACE = re.compile(r"\s+")


def _is_noise_cue(inner: str) -> bool:
    words = [w for w in re.split(r"[\s,]+", inner.lower().strip(" .!")) if w]
    # Empty brackets are what is left of "[♪♪♪]" once the symbols are removed.
    return all(w in _NOISE_WORDS for w in words)


def clean_text(text: str, *, remove_non_speech: bool) -> str:
    """Normalize whitespace; optionally strip *only* obvious non-speech cues.

    Spoken words are never removed: "[Music]" and "(applause)" go, but
    "[inaudible]", "(John speaking)" and lyrics between ♪ symbols stay.
    """
    if remove_non_speech:
        text = _MUSIC_SYMBOLS.sub(" ", text)
        text = _BRACKETED.sub(lambda m: " " if _is_noise_cue(m.group(1)) else m.group(0), text)
    return _WHITESPACE.sub(" ", text).strip()


def clean_segments(
    segments: Sequence[TranscriptSegment], *, remove_non_speech: bool
) -> list[TranscriptSegment]:
    """Return segments sorted by start time with cleaned text.

    A segment is dropped only when nothing remains after cleaning (it was empty
    or consisted solely of a non-speech cue).
    """
    cleaned: list[TranscriptSegment] = []
    for seg in sorted(segments, key=lambda s: s.start):
        text = clean_text(seg.text, remove_non_speech=remove_non_speech)
        if text:
            cleaned.append(TranscriptSegment(max(0.0, seg.start), max(0.0, seg.duration), text))
    return cleaned


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

COVERAGE_RATIO = 0.8
COVERAGE_GAP_SECONDS = 60.0


def coverage_warning(segments: Sequence[TranscriptSegment], duration: float | None) -> str | None:
    """Flag a transcript that stops well before the end of the video."""
    if not segments or not duration or duration <= 0:
        return None
    last_end = max(s.end for s in segments)
    gap = duration - last_end
    if last_end < duration * COVERAGE_RATIO and gap > COVERAGE_GAP_SECONDS:
        return (
            f"Transcript ends at {format_timestamp(last_end)} but the video is "
            f"{format_timestamp(duration)} long; the source may not cover the whole video."
        )
    return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class TranscriptService:
    """Try each provider in order and return a cleaned, verified transcript."""

    def __init__(self, settings: Settings, providers: Sequence[TranscriptProvider]) -> None:
        self._settings = settings
        self._providers = list(providers)

    def reset_pauses(self) -> None:
        for provider in self._providers:
            reset = getattr(provider, "reset_pause", None)
            if callable(reset):
                reset()

    @property
    def providers(self) -> list[TranscriptProvider]:
        return list(self._providers)

    def get_transcript(
        self,
        video: VideoInfo,
        progress: ProgressCallback,
        *,
        languages: Sequence[str] | None = None,
        remove_non_speech: bool | None = None,
        whisper_on_rate_limit: bool | None = None,
    ) -> TranscriptResult:
        langs = list(languages or [self._settings.default_language])
        remove = self._settings.remove_non_speech if remove_non_speech is None else remove_non_speech
        on_block = (self._settings.whisper_on_rate_limit
                    if whisper_on_rate_limit is None else whisper_on_rate_limit)

        errors: list[ExtractorError] = []

        for provider in self._providers:
            if errors and not self._should_try(provider, errors, on_block):
                logger.info("Video %s: skipping %s (YouTube is blocking requests and "
                            "Whisper-on-block is off)", video.video_id, provider.name)
                continue
            if not provider.is_available():
                logger.info("Provider %s not available, skipping", provider.name)
                if errors and provider.kind == "asr":
                    errors.append(WhisperNotInstalledError())
                continue
            if errors:
                logger.info(
                    "Video %s: %s fallback triggered (%s)",
                    video.video_id, provider.name, errors[-1].code,
                )
            try:
                logger.info("Video %s: trying provider %s", video.video_id, provider.name)
                raw = provider.get_transcript(video, langs, progress)
            except ExtractorError as exc:
                # "No transcript" and "YouTube is blocking us" move on to the
                # next source. Any failure inside a fallback is recorded, not
                # raised. Other errors from the first source (private or
                # removed video) are final: no provider can succeed.
                fallback_worthy = isinstance(exc, (TranscriptUnavailableError, RateLimitedError))
                if not fallback_worthy and not errors:
                    raise
                logger.info("Video %s: provider %s failed: %s (%s)",
                            video.video_id, provider.name, exc.code, exc.summary)
                errors.append(exc)
                continue

            segments = clean_segments(raw.segments, remove_non_speech=remove)
            if not segments:
                errors.append(TranscriptUnavailableError(
                    "The transcript contains no spoken content (only non-speech cues)."
                ))
                continue

            warning = coverage_warning(raw.segments, video.duration)
            if warning:
                logger.warning("Video %s: possibly incomplete transcript: %s", video.video_id, warning)
                raw.warnings.append(warning)
            raw.segments = segments
            logger.info(
                "Video %s: transcript ok via %s (%d segments)",
                video.video_id, provider.name, len(segments),
            )
            return raw

        raise self._final_error(errors)

    @staticmethod
    def _should_try(provider: TranscriptProvider, errors: list[ExtractorError], on_block: bool) -> bool:
        """Other caption sources are always worth a try. Whisper runs when there
        is genuinely no transcript, or, if the user opted in, when YouTube is
        only blocking requests (rate limits are temporary; real captions beat
        speech recognition)."""
        if provider.kind != "asr":
            return True
        if any(isinstance(e, TranscriptUnavailableError) for e in errors):
            return True
        return on_block

    def _asr_enabled(self) -> bool:
        return any(p.kind == "asr" for p in self._providers)

    def _final_error(self, errors: list[ExtractorError]) -> ExtractorError:
        if not errors:
            return TranscriptUnavailableError()
        missing = [e for e in errors if isinstance(e, TranscriptUnavailableError)]
        blocked = [e for e in errors if isinstance(e, RateLimitedError)]
        fallback = [e for e in errors if e not in missing and e not in blocked]

        if not missing and not fallback:
            return blocked[0]  # only blocking: the message explains the options
        primary = missing[0] if missing else blocked[0] if blocked else fallback[0]
        if fallback and fallback[-1] is not primary:
            how = ("Automatic transcription could not be completed:"
                   if isinstance(fallback[-1], WhisperError) else "The fallback also failed:")
            return TranscriptUnavailableError(
                f"{primary.user_message} {how} {fallback[-1].user_message}",
                detail=" | ".join(e.summary for e in errors),
            )
        if missing and not self._asr_enabled():
            return TranscriptUnavailableError(
                f"{primary.user_message} Automatic transcription (Whisper) is disabled.",
                detail=primary.detail,
            )
        return primary
