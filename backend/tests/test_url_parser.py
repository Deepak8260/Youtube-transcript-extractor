import pytest

from app.exceptions import InvalidURLError, UnsupportedURLError
from app.models.domain import SourceKind
from app.utils.url_parser import parse_youtube_url

VID = "dQw4w9WgXcQ"
PL = "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=42s",
        f"http://m.youtube.com/watch?feature=share&v={VID}",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://youtu.be/{VID}?si=abc123",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        f"https://www.youtube-nocookie.com/embed/{VID}",
        f"www.youtube.com/watch?v={VID}",
        f"  https://www.youtube.com/watch?v={VID}  ",
    ],
)
def test_video_urls(url):
    parsed = parse_youtube_url(url)
    assert parsed.kind == SourceKind.VIDEO
    assert parsed.video_id == VID


def test_watch_url_with_list_is_treated_as_video():
    parsed = parse_youtube_url(f"https://www.youtube.com/watch?v={VID}&list={PL}")
    assert parsed.kind == SourceKind.VIDEO
    assert parsed.video_id == VID


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/playlist?list={PL}",
        f"https://m.youtube.com/playlist?list={PL}&si=xyz",
        f"youtube.com/playlist?list={PL}",
    ],
)
def test_playlist_urls(url):
    parsed = parse_youtube_url(url)
    assert parsed.kind == SourceKind.PLAYLIST
    assert parsed.playlist_id == PL


@pytest.mark.parametrize("url", ["", "   ", "not a url", "ftp://youtube.com/watch?v=" + VID,
                                 "https://", "x" * 3000])
def test_invalid_urls(url):
    with pytest.raises(InvalidURLError):
        parse_youtube_url(url)


@pytest.mark.parametrize(
    "url",
    [
        f"https://vimeo.com/{VID}",
        f"https://evil.com/watch?v={VID}",
        f"https://youtube.com.evil.com/watch?v={VID}",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ;rm&x=1",
        "https://www.youtube.com/watch?v=../../etc/pass",
        "https://www.youtube.com/@SomeChannel",
        "https://www.youtube.com/",
        "https://www.youtube.com/results?search_query=python",
        "https://www.youtube.com/playlist?list=WL",
        "https://www.youtube.com/playlist?list=bad%20id",
        "https://youtu.be/",
    ],
)
def test_unsupported_urls(url):
    with pytest.raises(UnsupportedURLError):
        parse_youtube_url(url)


def test_non_string_input():
    with pytest.raises(InvalidURLError):
        parse_youtube_url(None)  # type: ignore[arg-type]
