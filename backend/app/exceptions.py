"""Domain exceptions.

Every exception carries a ``user_message`` that is safe to show in the UI and a
stable ``code`` the frontend can switch on. Technical detail goes to the logs,
never to the client.
"""

from __future__ import annotations


class ExtractorError(Exception):
    code: str = "error"
    user_message: str = "Something went wrong while processing this request."
    retryable: bool = False

    def __init__(self, user_message: str | None = None, *, detail: str | None = None) -> None:
        if user_message:
            self.user_message = user_message
        self.detail = detail
        super().__init__(detail or self.user_message)

    @property
    def summary(self) -> str:
        """One log-friendly line (library errors can be 15-line essays)."""
        text = str(self.detail or self.user_message)
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), self.code)
        return line if len(line) <= 200 else line[:199] + "…"


# --- Input -----------------------------------------------------------------


class InvalidURLError(ExtractorError):
    code = "invalid_url"
    user_message = "That doesn't look like a valid URL. Paste a YouTube video or playlist link."


class UnsupportedURLError(ExtractorError):
    code = "unsupported_url"
    user_message = "Only YouTube video and playlist URLs are supported."


# --- Video availability ------------------------------------------------------


class VideoUnavailableError(ExtractorError):
    code = "video_unavailable"
    user_message = "This video is unavailable. It may have been removed or blocked in your region."


class PrivateVideoError(VideoUnavailableError):
    code = "private_video"
    user_message = "This video is private."


class AgeRestrictedError(VideoUnavailableError):
    code = "age_restricted"
    user_message = "This video is age-restricted and cannot be accessed without signing in."


class PlaylistUnavailableError(ExtractorError):
    code = "playlist_unavailable"
    user_message = "This playlist could not be found. It may be private or deleted."


# --- Transcript availability -------------------------------------------------


class TranscriptUnavailableError(ExtractorError):
    """No usable caption track. Triggers the Whisper fallback."""

    code = "transcript_unavailable"
    user_message = "No transcript is available for this video."


class TranscriptsDisabledError(TranscriptUnavailableError):
    code = "transcripts_disabled"
    user_message = "Captions are disabled for this video."


class TranscriptNotFoundError(TranscriptUnavailableError):
    code = "transcript_not_found"
    user_message = "No caption track was found for the requested language."


class IncompleteTranscriptError(ExtractorError):
    code = "incomplete_transcript"
    user_message = "The transcript appears to be incomplete, so it was not saved."


# --- Infrastructure ------------------------------------------------------------


class NetworkError(ExtractorError):
    code = "network_error"
    user_message = "A network error occurred while contacting YouTube. Please try again."
    retryable = True


class RateLimitedError(ExtractorError):
    code = "rate_limited"
    user_message = (
        "YouTube is rate-limiting requests from this machine. Wait a while and "
        "run it again; transcripts that already succeeded will be skipped."
    )
    retryable = True


class IpBlockedError(RateLimitedError):
    """YouTube refuses caption downloads from this IP. Not retried: hammering a
    blocked endpoint only extends the block."""

    retryable = False
    user_message = (
        "YouTube is temporarily blocking caption downloads from this machine "
        "(too many requests). This usually clears within a few hours. To continue "
        "now, enable \"Use Whisper if YouTube blocks captions\" or set PROXY_URL."
    )


class ExtractionToolError(ExtractorError):
    code = "ytdlp_error"
    user_message = "YouTube metadata could not be retrieved."


class WhisperError(ExtractorError):
    code = "whisper_error"
    user_message = "Automatic transcription with Whisper failed."


class WhisperNotInstalledError(WhisperError):
    code = "whisper_not_installed"
    user_message = "The Whisper fallback is not installed on this server (faster-whisper missing)."


class FFmpegMissingError(WhisperError):
    code = "ffmpeg_missing"
    user_message = "FFmpeg is required to process this audio but was not found on the server."


class StorageError(ExtractorError):
    code = "storage_error"
    user_message = "The transcript could not be saved to disk."


class OperationTimeoutError(ExtractorError):
    code = "timeout"
    user_message = "The operation took too long and was stopped."
