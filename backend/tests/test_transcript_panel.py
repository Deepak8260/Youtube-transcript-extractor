"""The "Show transcript" panel fallback (youtubei/v1/get_panel)."""

import pytest

from app.exceptions import (
    IncompleteTranscriptError,
    IpBlockedError,
    TranscriptsDisabledError,
    TranscriptUnavailableError,
)
from app.models.domain import VideoInfo
from app.services.progress import null_progress
from app.services.transcript_panel_provider import (
    YouTubeTranscriptPanelProvider,
    panel_params,
    parse_panel,
    parse_timestamp,
    to_segments,
)
from app.services.transcript_service import TranscriptService

from .conftest import FakeProvider, make_segments

VIDEO = VideoInfo("YKX66nkoMW8", "Song", 321.0)


def item(text, start=None, stamp=None):
    """One panel row, shaped like YouTube's macroMarkersPanelItemViewModel."""
    row = {"item": {"timelineItemViewModel": {
        "timestamp": stamp or "",
        "contentItems": [{"transcriptSegmentViewModel": {"simpleText": text, "timestamp": stamp}}],
    }}}
    if start is not None:
        row["onTap"] = {"innertubeCommand": {"watchEndpoint": {"videoId": VIDEO.video_id,
                                                               "startTimeSeconds": start}}}
    return {"macroMarkersPanelItemViewModel": row}


def response(*rows, more=False):
    contents = list(rows) + ([{"continuationItemRenderer": {"trigger": "x"}}] if more else [])
    return {"content": {"engagementPanelSectionListRenderer": {"content": {"sectionListRenderer": {
        # The search box's continuationCommand must NOT count as a next page.
        "header": {"searchInputViewModel": {"onInputAction": {"command": {"innertubeCommand": {
            "continuationCommand": {"token": "abc"}}}}}},
        "contents": [{"itemSectionRenderer": {"contents": contents}}],
    }}}}}


def test_panel_params_match_youtube():
    assert panel_params("YKX66nkoMW8") == "qgkPCgtZS1g2Nm5rb01XOBgB"


@pytest.mark.parametrize(("value", "seconds"), [("0:08", 8), ("8:05", 485), ("1:02:03", 3723), ("x", None)])
def test_parse_timestamp(value, seconds):
    assert parse_timestamp(value) == seconds


def test_parse_panel_reads_every_row_in_order():
    data = response(item("first line", 0), item("second line", 8), item("from label only", stamp="1:02:03"))
    lines, more = parse_panel(data)
    assert lines == [(0, "first line"), (8, "second line"), (3723, "from label only")]
    assert more is False


def test_parse_panel_detects_pagination():
    _, more = parse_panel(response(item("a", 0), more=True))
    assert more is True


def test_to_segments_uses_next_start_and_never_invents_the_last_length():
    segments = to_segments([(8, "b"), (0, "a"), (15, "c")])
    assert [(s.start, s.duration, s.text) for s in segments] == [(0, 8, "a"), (8, 7, "b"), (15, 0, "c")]


def provider_with(settings, monkeypatch, result):
    provider = YouTubeTranscriptPanelProvider(settings)
    calls = []

    def fake_fetch(video_id):
        calls.append(video_id)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(provider, "_fetch", fake_fetch)
    return provider, calls


def test_provider_returns_timestamped_transcript(settings, monkeypatch):
    provider, _ = provider_with(settings, monkeypatch, response(item("जय जय राम", 0), item("मेरे राम", 8)))
    result = provider.get_transcript(VIDEO, ["en"], null_progress)
    assert result.source_label == "YouTube Transcript"
    assert [(s.start, s.text) for s in result.segments] == [(0, "जय जय राम"), (8, "मेरे राम")]


def test_provider_refuses_to_save_a_paginated_transcript(settings, monkeypatch):
    provider, _ = provider_with(settings, monkeypatch, response(item("a", 0), more=True))
    with pytest.raises(IncompleteTranscriptError):
        provider.get_transcript(VIDEO, ["en"], null_progress)


def test_provider_empty_panel_means_no_transcript(settings, monkeypatch):
    provider, _ = provider_with(settings, monkeypatch, response())
    with pytest.raises(TranscriptUnavailableError):
        provider.get_transcript(VIDEO, ["en"], null_progress)


def test_provider_block_pauses_further_requests(settings, monkeypatch):
    provider, calls = provider_with(settings, monkeypatch, IpBlockedError())
    for _ in range(2):
        with pytest.raises(IpBlockedError):
            provider.get_transcript(VIDEO, ["en"], null_progress)
    assert len(calls) == 1
    provider.reset_pause()
    with pytest.raises(IpBlockedError):
        provider.get_transcript(VIDEO, ["en"], null_progress)
    assert len(calls) == 2


# ------------------------------------------------------------- fallback order


def chain(settings, captions_error, panel=None, panel_error=None):
    captions = FakeProvider(name="youtube", errors={VIDEO.video_id: captions_error})
    panel_provider = FakeProvider(name="youtube_panel", default=panel, label="YouTube Transcript",
                                  errors={VIDEO.video_id: panel_error} if panel_error else None)
    whisper = FakeProvider(name="whisper", default=make_segments(3), label="Whisper (small)")
    return TranscriptService(settings, [captions, panel_provider, whisper]), panel_provider, whisper


def test_blocked_captions_fall_back_to_panel_not_whisper(settings):
    service, panel, whisper = chain(settings, IpBlockedError(), panel=make_segments(5))
    result = service.get_transcript(VIDEO, null_progress)
    assert result.source_label == "YouTube Transcript"
    assert panel.calls == [VIDEO.video_id] and whisper.calls == []


def test_everything_blocked_without_opt_in_reports_the_block(settings):
    service, _, whisper = chain(settings, IpBlockedError(), panel_error=IpBlockedError())
    with pytest.raises(IpBlockedError):
        service.get_transcript(VIDEO, null_progress)
    assert whisper.calls == []


def test_everything_blocked_with_opt_in_uses_whisper(settings):
    service, _, whisper = chain(settings, IpBlockedError(), panel_error=IpBlockedError())
    assert service.get_transcript(VIDEO, null_progress, whisper_on_rate_limit=True).provider == "whisper"


def test_no_transcript_anywhere_uses_whisper(settings):
    service, _, whisper = chain(settings, TranscriptsDisabledError(),
                                panel_error=TranscriptUnavailableError())
    assert service.get_transcript(VIDEO, null_progress).provider == "whisper"
