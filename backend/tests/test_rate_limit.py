"""YouTube IP blocks: no hammering, optional Whisper, readable logs."""

import logging

import pytest
from youtube_transcript_api import IpBlocked

from app.exceptions import IpBlockedError
from app.models.domain import VideoInfo
from app.services.progress import null_progress
from app.services.transcript_service import TranscriptService, YouTubeTranscriptProvider

from .conftest import FakeProvider, make_segments

VIDEO = VideoInfo("abcdefghijk", "Some Video", 40.0)


class CountingApi:
    def __init__(self):
        self.calls = 0

    def list(self, video_id):
        self.calls += 1
        raise IpBlocked(video_id)


def test_ip_block_is_not_retried_and_pauses_caption_requests(settings, monkeypatch):
    settings.max_retries = 3
    provider = YouTubeTranscriptProvider(settings)
    api = CountingApi()
    monkeypatch.setattr(provider, "_api", lambda: api)

    with pytest.raises(IpBlockedError):
        provider.get_transcript(VIDEO, ["en"], null_progress)
    assert api.calls == 1  # no rapid retries against a blocked endpoint

    with pytest.raises(IpBlockedError):
        provider.get_transcript(VideoInfo("bbbbbbbbbbb", "Next", 40.0), ["en"], null_progress)
    assert api.calls == 1  # breaker open: YouTube was not contacted again


def test_ip_block_message_explains_the_way_out(settings):
    yt = FakeProvider(errors={VIDEO.video_id: IpBlockedError()})
    service = TranscriptService(settings, [yt])
    with pytest.raises(IpBlockedError) as info:
        service.get_transcript(VIDEO, null_progress)
    assert "Use Whisper if YouTube blocks captions" in info.value.user_message


def test_per_request_whisper_on_block(settings):
    yt = FakeProvider(errors={VIDEO.video_id: IpBlockedError()})
    whisper = FakeProvider(name="whisper", default=make_segments(5))
    service = TranscriptService(settings, [yt, whisper])
    assert settings.whisper_on_rate_limit is False
    result = service.get_transcript(VIDEO, null_progress, whisper_on_rate_limit=True)
    assert result.provider == "whisper"
    with pytest.raises(IpBlockedError):
        service.get_transcript(VIDEO, null_progress, whisper_on_rate_limit=False)


def test_error_summary_trims_library_essays():
    essay = "\nCould not retrieve a transcript for the video X!\n\nYouTube is blocking...\n" + "x" * 500
    assert IpBlockedError(detail=essay).summary == "Could not retrieve a transcript for the video X!"
    assert len(IpBlockedError(detail="y" * 500).summary) == 200
    assert IpBlockedError().summary.startswith("YouTube is temporarily blocking")


def test_polling_requests_are_not_access_logged():
    from app.main import _QuietPolling

    def record(path, status=200):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                 ("1.2.3.4:5", "GET", path, "1.1", status), None)

    f = _QuietPolling()
    assert not f.filter(record("/api/transcripts/jobs/abc123"))
    assert not f.filter(record("/api/health"))
    assert f.filter(record("/api/transcripts/jobs/abc123/download-all"))
    assert f.filter(record("/api/transcripts/extract"))
    assert f.filter(record("/api/transcripts/jobs/abc123", 404))


def test_new_job_gets_a_real_attempt_after_a_pause(settings, monkeypatch):
    provider = YouTubeTranscriptProvider(settings)
    api = CountingApi()
    monkeypatch.setattr(provider, "_api", lambda: api)
    service = TranscriptService(settings, [provider])

    with pytest.raises(IpBlockedError):
        service.get_transcript(VIDEO, null_progress)
    with pytest.raises(IpBlockedError):
        service.get_transcript(VIDEO, null_progress)
    assert api.calls == 1  # paused within the same job

    service.reset_pauses()  # what every new job does first
    with pytest.raises(IpBlockedError):
        service.get_transcript(VIDEO, null_progress)
    assert api.calls == 2  # the user's retry really reached YouTube
