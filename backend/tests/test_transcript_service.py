from dataclasses import dataclass

import pytest

from app.exceptions import (
    PrivateVideoError,
    RateLimitedError,
    TranscriptsDisabledError,
    TranscriptUnavailableError,
    WhisperError,
)
from app.models.domain import TranscriptSegment, VideoInfo
from app.services.progress import null_progress
from app.services.transcript_service import (
    TranscriptService,
    clean_segments,
    clean_text,
    coverage_warning,
    select_transcript,
)
from app.services.whisper_service import WhisperTranscriptProvider

from .conftest import FakeProvider, make_segments

VIDEO = VideoInfo("abcdefghijk", "Some Video", 40.0)


# ---------------------------------------------------------------- cleaning


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[Music]", ""),
        ("[music]", ""),
        ("[Applause]", ""),
        ("(laughter)", ""),
        ("[♪♪♪]", ""),
        ("♪", ""),
        ("[upbeat music]", ""),
        ("[Música]", ""),
        ("[संगीत]", ""),
        ("[घंटी की आवाज़] जय जय राम", "जय जय राम"),
        ("राम मेरे [गाना गाने की आवाज़] राम", "राम मेरे राम"),
        ("[Music] Welcome back everyone", "Welcome back everyone"),
        ("so that's it [Applause] thank you", "so that's it thank you"),
        ("♪ never gonna give you up ♪", "never gonna give you up"),
        ("[inaudible] said the thing", "[inaudible] said the thing"),
        ("(John) I disagree", "(John) I disagree"),
        ("I play [the guitar] daily", "I play [the guitar] daily"),
        ("line one\nline two", "line one line two"),
        ("  extra   spaces  ", "extra spaces"),
    ],
)
def test_clean_text_removes_only_noise(raw, expected):
    assert clean_text(raw, remove_non_speech=True) == expected


def test_clean_text_keep_mode_preserves_cues():
    assert clean_text("[Music] hello\nthere", remove_non_speech=False) == "[Music] hello there"


def test_clean_segments_drops_only_empty_and_sorts():
    segs = [
        TranscriptSegment(5, 1, "second"),
        TranscriptSegment(0, 1, "first"),
        TranscriptSegment(3, 1, "[Music]"),
        TranscriptSegment(4, 1, "   "),
    ]
    out = clean_segments(segs, remove_non_speech=True)
    assert [s.text for s in out] == ["first", "second"]
    assert [s.start for s in out] == [0, 5]


# ---------------------------------------------------------- track selection


@dataclass
class T:
    language_code: str
    is_generated: bool


def test_prefers_manual_over_generated():
    tracks = [T("en", True), T("en", False)]
    assert select_transcript(tracks, ["en"], allow_other=True) is tracks[1]


def test_language_matches_regional_variant():
    tracks = [T("de", False), T("en-GB", False)]
    assert select_transcript(tracks, ["en"], allow_other=False) is tracks[1]


def test_generated_in_language_beats_manual_in_other_language():
    tracks = [T("fr", False), T("en", True)]
    assert select_transcript(tracks, ["en"], allow_other=True) is tracks[1]


def test_falls_back_to_other_language_when_allowed():
    tracks = [T("hi", True), T("es", False)]
    assert select_transcript(tracks, ["en"], allow_other=True) is tracks[1]
    assert select_transcript(tracks, ["en"], allow_other=False) is None


# --------------------------------------------------------------- coverage


def test_coverage_ok():
    assert coverage_warning(make_segments(10, 4), 42.0) is None


def test_coverage_flags_early_end():
    warning = coverage_warning(make_segments(10, 4), 600.0)
    assert warning and "00:00:40" in warning and "00:10:00" in warning


def test_coverage_unknown_duration():
    assert coverage_warning(make_segments(3), None) is None


# ----------------------------------------------------------- orchestration


def service(settings, *providers):
    return TranscriptService(settings, list(providers))


def test_uses_first_provider_when_available(settings):
    yt = FakeProvider(default=make_segments(10))
    whisper = FakeProvider(name="whisper", default=make_segments(2))
    result = service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    assert result.provider == "youtube"
    assert len(result.segments) == 10
    assert whisper.calls == []


def test_falls_back_to_whisper(settings):
    yt = FakeProvider(errors={VIDEO.video_id: TranscriptsDisabledError()})
    whisper = FakeProvider(name="whisper", default=make_segments(10), label="Whisper (small)")
    result = service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    assert result.source_label == "Whisper (small)"
    assert whisper.calls == [VIDEO.video_id]


def test_rate_limit_is_reported_not_transcribed_by_default(settings):
    yt = FakeProvider(errors={VIDEO.video_id: RateLimitedError()})
    whisper = FakeProvider(name="whisper", default=make_segments(10))
    with pytest.raises(RateLimitedError):
        service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    assert whisper.calls == []


def test_rate_limit_fallback_when_enabled(settings):
    settings.whisper_on_rate_limit = True
    yt = FakeProvider(errors={VIDEO.video_id: RateLimitedError()})
    whisper = FakeProvider(name="whisper", default=make_segments(10))
    assert service(settings, yt, whisper).get_transcript(VIDEO, null_progress).provider == "whisper"


def test_falls_back_when_captions_are_only_noise(settings):
    yt = FakeProvider(default=[TranscriptSegment(0, 40, "[Music]")])
    whisper = FakeProvider(name="whisper", default=make_segments(10))
    assert service(settings, yt, whisper).get_transcript(VIDEO, null_progress).provider == "whisper"


def test_private_video_does_not_fall_back(settings):
    yt = FakeProvider(errors={VIDEO.video_id: PrivateVideoError()})
    whisper = FakeProvider(name="whisper", default=make_segments(10))
    with pytest.raises(PrivateVideoError):
        service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    assert whisper.calls == []


def test_both_fail_gives_combined_human_message(settings):
    yt = FakeProvider(errors={VIDEO.video_id: TranscriptsDisabledError()})
    whisper = FakeProvider(name="whisper", errors={VIDEO.video_id: WhisperError()})
    with pytest.raises(TranscriptUnavailableError) as info:
        service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    msg = info.value.user_message
    assert "Captions are disabled" in msg and "Automatic transcription could not be completed" in msg


def test_whisper_unavailable_is_reported(settings):
    yt = FakeProvider(errors={VIDEO.video_id: TranscriptsDisabledError()})
    whisper = FakeProvider(name="whisper", available=False)
    with pytest.raises(TranscriptUnavailableError) as info:
        service(settings, yt, whisper).get_transcript(VIDEO, null_progress)
    assert "not installed" in info.value.user_message


def test_whisper_disabled_message(settings):
    yt = FakeProvider(errors={VIDEO.video_id: TranscriptsDisabledError()})
    with pytest.raises(TranscriptUnavailableError) as info:
        service(settings, yt).get_transcript(VIDEO, null_progress)
    assert "disabled" in info.value.user_message


def test_incomplete_captions_get_warning(settings):
    long_video = VideoInfo("abcdefghijk", "Long", 3600.0)
    yt = FakeProvider(default=make_segments(10))
    result = service(settings, yt).get_transcript(long_video, null_progress)
    assert result.warnings and "may not cover" in result.warnings[0]


def test_whisper_rejects_truncated_audio():
    from app.exceptions import IncompleteTranscriptError

    with pytest.raises(IncompleteTranscriptError):
        WhisperTranscriptProvider._verify_audio_length(VideoInfo("abcdefghijk", "x", 3600.0), 1200.0)
    WhisperTranscriptProvider._verify_audio_length(VideoInfo("abcdefghijk", "x", 3600.0), 3590.0)


def test_whisper_provider_respects_disable_flag(settings):
    from app.services.whisper_service import WhisperService

    provider = WhisperTranscriptProvider(settings, None, WhisperService(settings))  # type: ignore[arg-type]
    assert settings.enable_whisper_fallback is False
    assert provider.is_available() is False
