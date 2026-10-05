from pathlib import Path

import pytest

from app.utils.filename import FALLBACK_NAME, safe_join, sanitize_filename, unique_path


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("What is Claude Code?", "What is Claude Code"),
        ("What is Claude Code? | Complete Guide", "What is Claude Code - Complete Guide"),
        ("How to Build an AI Agent: Complete Guide?", "How to Build an AI Agent - Complete Guide"),
        ('He said "hi" <loudly> *now*', "He said hi loudly now"),
        ("AC/DC \\ Live", "AC-DC-Live"),
        ("Too    many     spaces", "Too many spaces"),
        ("Part 1: Intro | Basics", "Part 1 - Intro - Basics"),
        ("Ends with dots...", "Ends with dots"),
        ("Python Variables", "Python Variables"),
    ],
)
def test_sanitize_examples(title, expected):
    assert sanitize_filename(title) == expected


def test_preserves_unicode():
    assert sanitize_filename("Café — 日本語のタイトル 🎉") == "Café — 日本語のタイトル 🎉"


@pytest.mark.parametrize("title", ["", None, "   ", "???", "...", "..", "/", "|:|", "​‮"])
def test_empty_becomes_fallback(title):
    assert sanitize_filename(title) == FALLBACK_NAME


@pytest.mark.parametrize("title", ["../../etc/passwd", "..\\..\\windows\\system32", "/abs/path"])
def test_no_path_traversal(title):
    name = sanitize_filename(title)
    assert "/" not in name and "\\" not in name
    assert not name.startswith(".")


def test_strips_control_and_bidi_chars():
    assert sanitize_filename("Hello\x00\x1f‮World\n") == "HelloWorld"


@pytest.mark.parametrize("title", ["CON", "nul", "COM1", "lpt9"])
def test_windows_reserved_names(title):
    assert sanitize_filename(title).startswith("_")


def test_length_limit_respects_utf8_bytes():
    name = sanitize_filename("é" * 500, max_bytes=100)
    assert len(name.encode("utf-8")) <= 100
    assert name == "é" * 50


def test_truncation_does_not_end_in_separator():
    name = sanitize_filename("a" * 99 + " - b" * 10, max_bytes=101)
    assert not name.endswith((" ", "-", "."))


def test_unique_path_adds_counter(tmp_path: Path):
    (tmp_path / "Title.txt").write_text("x")
    (tmp_path / "Title (2).txt").write_text("x")
    assert unique_path(tmp_path, "Title") == tmp_path / "Title (3).txt"


def test_unique_path_honours_reserved(tmp_path: Path):
    taken = {tmp_path / "Title.txt"}
    assert unique_path(tmp_path, "Title", taken=taken) == tmp_path / "Title (2).txt"


def test_safe_join_rejects_escape(tmp_path: Path):
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "outside.txt")
