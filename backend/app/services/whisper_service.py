"""Speech-to-text fallback using faster-whisper.

Used only when YouTube provides no usable caption track. Audio is downloaded
to a per-run temp directory which is always deleted afterwards.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.exceptions import (
    ExtractorError,
    FFmpegMissingError,
    IncompleteTranscriptError,
    OperationTimeoutError,
    WhisperError,
    WhisperNotInstalledError,
)
from app.models.domain import TranscriptResult, TranscriptSegment, VideoInfo
from app.services.progress import ProgressCallback, Stage
from app.services.transcript_service import TranscriptProvider
from app.services.youtube_service import YouTubeService
from app.utils.timestamps import format_duration

logger = logging.getLogger(__name__)

# Downloaded audio may legitimately be a few seconds shorter than the reported
# video length; anything beyond this means the download was cut short.
AUDIO_LENGTH_TOLERANCE_SECONDS = 30.0
AUDIO_LENGTH_TOLERANCE_RATIO = 0.05


@dataclass(slots=True)
class WhisperOutput:
    segments: list[TranscriptSegment]
    audio_duration: float
    language: str | None


class WhisperService:
    """Owns the (lazily loaded, shared) faster-whisper model."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any = None
        self._load_lock = threading.Lock()
        # One transcription at a time: Whisper saturates the CPU/GPU on its own.
        self._run_lock = threading.Semaphore(1)

    @staticmethod
    def is_installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    @property
    def model_name(self) -> str:
        return self._settings.whisper_model

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                if not self.is_installed():
                    raise WhisperNotInstalledError()
                from faster_whisper import WhisperModel  # heavy import, keep lazy

                logger.info(
                    "Loading Whisper model %s (device=%s, compute=%s)",
                    self._settings.whisper_model,
                    self._settings.whisper_device,
                    self._settings.whisper_compute_type,
                )
                try:
                    self._model = WhisperModel(
                        self._settings.whisper_model,
                        device=self._settings.whisper_device,
                        compute_type=self._settings.whisper_compute_type,
                    )
                except Exception as exc:  # model download / CTranslate2 init
                    raise WhisperError(
                        "The Whisper model could not be loaded.", detail=repr(exc)
                    ) from exc
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        *,
        on_progress: Callable[[float], None] | None = None,
        language: str | None = None,
    ) -> WhisperOutput:
        """Transcribe a local audio file into timestamped segments."""
        with self._run_lock:
            model = self._get_model()
            try:
                return self._run(model, audio_path, on_progress, language)
            except _DecodeError as exc:
                wav = self._convert_with_ffmpeg(audio_path)
                logger.info("Retrying Whisper on FFmpeg-converted audio (%s)", exc)
                try:
                    return self._run(model, wav, on_progress, language)
                except _DecodeError as exc2:
                    raise WhisperError(
                        "The downloaded audio could not be decoded.", detail=repr(exc2)
                    ) from exc2

    def _run(
        self,
        model: Any,
        audio_path: Path,
        on_progress: Callable[[float], None] | None,
        language: str | None,
    ) -> WhisperOutput:
        deadline = time.monotonic() + self._settings.whisper_timeout_seconds
        try:
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=language or None,
                beam_size=5,
                vad_filter=True,
                # Avoids the repetition loops Whisper can fall into on long audio.
                condition_on_previous_text=False,
            )
        except Exception as exc:
            raise _DecodeError(repr(exc)) from exc

        audio_duration = float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[TranscriptSegment] = []
        try:
            # The generator does the actual work; it must be fully consumed.
            for seg in segments_iter:
                if time.monotonic() > deadline:
                    raise OperationTimeoutError(
                        "Automatic transcription took too long and was stopped."
                    )
                text = (seg.text or "").strip()
                if text:
                    start = float(seg.start)
                    segments.append(TranscriptSegment(start, max(0.0, float(seg.end) - start), text))
                if on_progress and audio_duration > 0:
                    on_progress(min(1.0, float(seg.end) / audio_duration))
        except ExtractorError:
            raise
        except Exception as exc:
            raise WhisperError(detail=repr(exc)) from exc

        return WhisperOutput(segments, audio_duration, getattr(info, "language", None))

    def _convert_with_ffmpeg(self, audio_path: Path) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FFmpegMissingError()
        out = audio_path.with_suffix(".16k.wav")
        cmd = [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(audio_path),
               "-ac", "1", "-ar", "16000", "-f", "wav", str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True,
                           timeout=self._settings.whisper_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise OperationTimeoutError("Audio conversion took too long.") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            raise WhisperError(
                "FFmpeg could not convert the audio.",
                detail=stderr.decode("utf-8", "replace")[-500:] or repr(exc),
            ) from exc
        return out


class _DecodeError(Exception):
    """Internal: the audio container could not be opened by PyAV."""


class WhisperTranscriptProvider(TranscriptProvider):
    """Download audio with yt-dlp, transcribe it with faster-whisper."""

    name = "whisper"
    kind = "asr"

    def __init__(self, settings: Settings, youtube: YouTubeService, whisper: WhisperService) -> None:
        self._settings = settings
        self._youtube = youtube
        self._whisper = whisper

    def is_available(self) -> bool:
        return self._settings.enable_whisper_fallback and self._whisper.is_installed()

    def get_transcript(
        self,
        video: VideoInfo,
        languages: Sequence[str],
        progress: ProgressCallback,
    ) -> TranscriptResult:
        limit = self._settings.max_audio_duration_seconds
        if video.duration and video.duration > limit:
            raise WhisperError(
                f"This video is longer than the {format_duration(limit)} limit for "
                "automatic transcription."
            )

        work_dir = self._settings.temp_dir / f"whisper-{uuid.uuid4().hex}"
        try:
            progress(Stage.WHISPER_DOWNLOAD,
                     "YouTube captions unavailable. Downloading audio...", 0.05)
            audio = self._youtube.download_audio(
                video.video_id, work_dir,
                on_progress=lambda f: progress(
                    Stage.WHISPER_DOWNLOAD,
                    "YouTube captions unavailable. Downloading audio...",
                    0.05 + 0.25 * f,
                ),
            )
            message = (
                "YouTube captions unavailable. Generating transcript with "
                f"Whisper ({self._whisper.model_name})..."
            )
            progress(Stage.WHISPER_TRANSCRIBE, message, 0.3)
            output = self._whisper.transcribe(
                audio,
                language=self._settings.whisper_language or None,
                on_progress=lambda f: progress(Stage.WHISPER_TRANSCRIBE, message, 0.3 + 0.65 * f),
            )
        except ExtractorError:
            raise
        except OSError as exc:
            raise WhisperError(detail=repr(exc)) from exc
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        self._verify_audio_length(video, output.audio_duration)
        if not output.segments:
            raise WhisperError("Whisper did not detect any speech in this video.")

        return TranscriptResult(
            segments=output.segments,
            source_label=f"Whisper ({self._whisper.model_name})",
            provider=self.name,
            language_code=output.language,
            language_name=None,
            is_generated=True,
        )

    @staticmethod
    def _verify_audio_length(video: VideoInfo, audio_duration: float) -> None:
        if not video.duration or audio_duration <= 0:
            return
        allowed = max(AUDIO_LENGTH_TOLERANCE_SECONDS, video.duration * AUDIO_LENGTH_TOLERANCE_RATIO)
        if video.duration - audio_duration > allowed:
            raise IncompleteTranscriptError(
                "The downloaded audio is shorter than the video, so the transcript "
                "would be incomplete. It was not saved.",
                detail=f"audio={audio_duration:.1f}s video={video.duration:.1f}s",
            )
