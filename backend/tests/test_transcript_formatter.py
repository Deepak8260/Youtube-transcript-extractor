from app.models.domain import TranscriptResult, TranscriptSegment, VideoInfo
from app.utils.formatter import RULE, format_transcript, group_segments, parse_header

VIDEO = VideoInfo("dQw4w9WgXcQ", "What is Claude Code?", 2538.0)


def result(segments, **kw):
    return TranscriptResult(segments=segments, source_label="YouTube Captions", provider="youtube",
                            language_code="en", language_name="English", **kw)


def test_full_format_matches_spec():
    segs = [
        TranscriptSegment(0.0, 4.2, "Welcome everyone to this video."),
        TranscriptSegment(8.1, 5.0, "Today we are going to learn about Claude Code."),
        TranscriptSegment(4354.0, 3.0, "Late segment."),
    ]
    text = format_transcript(VIDEO, result(segs), duration_display="00:42:18")
    assert text == (
        f"{RULE}\n"
        "Title: What is Claude Code?\n"
        "Source: YouTube\n"
        "URL: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
        "Transcript Source: YouTube Captions\n"
        "Language: English (en)\n"
        "Duration: 00:42:18\n"
        f"{RULE}\n"
        "\n"
        "[00:00:00]\n"
        "Welcome everyone to this video.\n"
        "\n"
        "[00:00:08]\n"
        "Today we are going to learn about Claude Code.\n"
        "\n"
        "[01:12:34]\n"
        "Late segment.\n"
    )


def test_every_segment_is_kept_with_its_timestamp():
    segs = [TranscriptSegment(i * 2.5, 2.5, f"words {i}") for i in range(2000)]
    text = format_transcript(VIDEO, result(segs))
    for i in range(2000):
        assert f"words {i}\n" in text
    assert text.count("\n[") == 2000
    assert "[01:23:17]" in text  # 1999 * 2.5 = 4997.5s


def test_empty_transcript_still_has_header():
    text = format_transcript(VIDEO, result([]))
    assert text.startswith(RULE)
    assert "[00:" not in text


def test_warnings_go_in_header():
    text = format_transcript(VIDEO, result([TranscriptSegment(0, 1, "hi")], warnings=["Ends early."]))
    assert "Warning: Ends early." in text.split(RULE)[1]


def test_title_newlines_cannot_break_header():
    video = VideoInfo("dQw4w9WgXcQ", "Line one\nURL: https://evil", None)
    header = parse_header(format_transcript(video, result([TranscriptSegment(0, 1, "x")])))
    assert header["URL"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_parse_header_roundtrip():
    header = parse_header(format_transcript(VIDEO, result([TranscriptSegment(0, 1, "x")])))
    assert header["Title"] == "What is Claude Code?"
    assert header["URL"].endswith("v=dQw4w9WgXcQ")
    assert header["Transcript Source"] == "YouTube Captions"


def test_parse_header_rejects_foreign_files():
    assert parse_header("just some notes\nURL: https://x") == {}


def test_group_segments_is_lossless():
    segs = [TranscriptSegment(i * 2.0, 2.0, f"w{i}") for i in range(10)]
    blocks = group_segments(segs, 6.0)
    assert [b.start for b in blocks] == [0.0, 6.0, 12.0, 18.0]
    assert " ".join(b.text for b in blocks) == " ".join(s.text for s in segs)
    assert blocks[0].text == "w0 w1 w2"


def test_group_segments_disabled():
    segs = [TranscriptSegment(0, 1, "a"), TranscriptSegment(1, 1, "b")]
    assert group_segments(segs, 0) == segs
