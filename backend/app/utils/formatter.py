"""Render transcript segments to the .txt output format, and read headers back."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.models.domain import TranscriptResult, TranscriptSegment, VideoInfo
from app.utils.timestamps import format_timestamp

RULE = "=" * 50
HEADER_SCAN_LINES = 20


def group_segments(
    segments: Sequence[TranscriptSegment], max_block_seconds: float
) -> list[TranscriptSegment]:
    """Losslessly join consecutive segments into blocks of ``max_block_seconds``.

    The block keeps the first segment's start time and every word of every
    segment. With ``max_block_seconds <= 0`` the input is returned unchanged.
    """
    if max_block_seconds <= 0 or not segments:
        return list(segments)

    blocks: list[TranscriptSegment] = []
    start = segments[0].start
    end = segments[0].end
    texts = [segments[0].text]
    for seg in segments[1:]:
        if seg.start - start >= max_block_seconds:
            blocks.append(TranscriptSegment(start, max(0.0, end - start), " ".join(texts)))
            start, end, texts = seg.start, seg.end, [seg.text]
        else:
            end = max(end, seg.end)
            texts.append(seg.text)
    blocks.append(TranscriptSegment(start, max(0.0, end - start), " ".join(texts)))
    return blocks


def format_header(
    video: VideoInfo,
    result: TranscriptResult,
    duration_display: str | None = None,
) -> list[str]:
    lines = [
        RULE,
        f"Title: {_one_line(video.title)}",
        "Source: YouTube",
        f"URL: {video.url}",
        f"Transcript Source: {result.source_label}",
    ]
    if result.language_code:
        if result.language_name:
            lines.append(f"Language: {_one_line(result.language_name)} ({result.language_code})")
        else:
            lines.append(f"Language: {result.language_code}")
    if duration_display:
        lines.append(f"Duration: {duration_display}")
    for warning in result.warnings:
        lines.append(f"Warning: {_one_line(warning)}")
    lines.append(RULE)
    return lines


def format_body(segments: Iterable[TranscriptSegment]) -> list[str]:
    lines: list[str] = []
    for seg in segments:
        lines.append(f"[{format_timestamp(seg.start)}]")
        lines.append(seg.text)
        lines.append("")
    return lines


def format_transcript(
    video: VideoInfo,
    result: TranscriptResult,
    *,
    group_seconds: float = 0.0,
    duration_display: str | None = None,
) -> str:
    """Full file contents: metadata header followed by timestamped segments."""
    segments = group_segments(result.segments, group_seconds)
    lines = format_header(video, result, duration_display)
    lines.append("")
    lines.extend(format_body(segments))
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_header(text: str) -> dict[str, str]:
    """Read ``Key: value`` pairs from the header of a transcript file."""
    fields: dict[str, str] = {}
    lines = text.splitlines()[:HEADER_SCAN_LINES]
    if not lines or lines[0].strip() != RULE:
        return fields
    for line in lines[1:]:
        if line.strip() == RULE:
            break
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), value.strip())
    return fields


def _one_line(value: str) -> str:
    return " ".join(str(value).split())
