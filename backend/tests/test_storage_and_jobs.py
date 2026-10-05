import io
import zipfile

import pytest

from app.container import build_container
from app.exceptions import (
    NetworkError,
    PrivateVideoError,
    TranscriptsDisabledError,
)
from app.models.domain import PlaylistInfo, SourceKind, TranscriptSegment, VideoInfo
from app.models.schemas import ItemStatus, JobStatus
from app.services.extraction_service import ExtractionOptions
from app.services.job_manager import Job
from app.services.storage_service import MANIFEST_NAME, StorageService

from .conftest import FakeProvider, FakeYouTube, make_segments, playlist_of

OPTS = ExtractionOptions(language="en")


def container_with(settings, youtube, provider):
    c = build_container(settings, youtube=youtube, providers=[provider])
    c.storage.ensure_dirs()
    return c


# ---------------------------------------------------------------- storage


def test_atomic_write_and_registry(settings):
    storage = StorageService(settings)
    storage.ensure_dirs()
    path = storage.write_atomic(settings.output_dir / "A.txt", "hello\n")
    assert path.read_text(encoding="utf-8") == "hello\n"
    assert not list(settings.output_dir.glob(".tmp-*"))
    stored = storage.register(path)
    assert storage.resolve(stored.file_id) == path
    assert storage.register(path).file_id == stored.file_id
    assert storage.resolve("unknown-id") is None


def test_write_outside_root_is_refused(settings, tmp_path):
    from app.exceptions import StorageError

    storage = StorageService(settings)
    with pytest.raises(StorageError):
        storage.write_atomic(tmp_path / "escape.txt", "x")
    with pytest.raises(StorageError):
        storage.register(settings.output_dir / ".." / "escape.txt")


def test_zip_contains_only_txt(settings):
    storage = StorageService(settings)
    storage.ensure_dirs()
    a = storage.write_atomic(settings.output_dir / "pl" / "001_A.txt", "a")
    b = storage.write_atomic(settings.output_dir / "pl" / "002_B.txt", "b")
    (settings.output_dir / "pl" / MANIFEST_NAME).write_text("{}")
    names = zipfile.ZipFile(io.BytesIO(storage.build_zip([b, a]))).namelist()
    assert names == ["001_A.txt", "002_B.txt"]


def test_playlist_dir_reuse_and_collision(settings):
    storage = StorageService(settings)
    storage.ensure_dirs()
    first = storage.playlist_dir(PlaylistInfo("PLaaaaaaaaaa", "Course: Part 1", []))
    assert first.name == "Course - Part 1"
    assert storage.playlist_dir(PlaylistInfo("PLaaaaaaaaaa", "Course: Part 1", [])) == first
    other = storage.playlist_dir(PlaylistInfo("PLbbbbbbbbbb", "Course: Part 1", []))
    assert other.name == "Course - Part 1 [PLbbbbbbbbbb]"


# ----------------------------------------------------------- single video


def run_video(container, video_id, options=OPTS):
    job = Job(SourceKind.VIDEO, video_id)
    container.extraction.run_video_job(job, video_id, options)
    return job.snapshot()


def test_single_video_saves_title_named_file(settings):
    yt = FakeYouTube(videos={"abcdefghijk": VideoInfo("abcdefghijk", "What is Claude Code?", 40.0)})
    c = container_with(settings, yt, FakeProvider(default=make_segments(10)))
    snap = run_video(c, "abcdefghijk")
    assert snap.status == JobStatus.COMPLETED
    assert snap.files[0].filename == "What is Claude Code.txt"
    text = (settings.output_dir / "What is Claude Code.txt").read_text(encoding="utf-8")
    assert "Title: What is Claude Code?" in text
    assert "[00:00:36]\nline 9" in text
    assert snap.progress == 100


def test_existing_transcript_is_skipped_then_overwritten(settings):
    provider = FakeProvider(default=make_segments(3))
    c = container_with(settings, FakeYouTube(), provider)
    run_video(c, "abcdefghijk")
    snap = run_video(c, "abcdefghijk")
    assert snap.items[0].status == ItemStatus.EXISTING
    assert provider.calls == ["abcdefghijk"]

    provider.default = make_segments(5)
    snap = run_video(c, "abcdefghijk", ExtractionOptions(language="en", overwrite=True))
    assert snap.items[0].status == ItemStatus.SUCCESS
    assert len(list(settings.output_dir.glob("*.txt"))) == 1
    assert "line 4" in (settings.output_dir / "Video abcdefghijk.txt").read_text(encoding="utf-8")


def test_same_title_different_video_gets_suffix(settings):
    yt = FakeYouTube(videos={
        "aaaaaaaaaaa": VideoInfo("aaaaaaaaaaa", "Intro", 10.0),
        "bbbbbbbbbbb": VideoInfo("bbbbbbbbbbb", "Intro", 10.0),
    })
    c = container_with(settings, yt, FakeProvider(default=make_segments(2)))
    run_video(c, "aaaaaaaaaaa")
    snap = run_video(c, "bbbbbbbbbbb")
    assert snap.files[0].filename == "Intro (2).txt"


def test_single_video_failure_is_human_readable(settings):
    yt = FakeYouTube(errors={"abcdefghijk": PrivateVideoError()})
    c = container_with(settings, yt, FakeProvider(default=make_segments(2)))
    snap = run_video(c, "abcdefghijk")
    assert snap.status == JobStatus.FAILED
    assert snap.items[0].status == ItemStatus.UNAVAILABLE
    assert snap.error == "This video is private."
    assert not list(settings.output_dir.glob("*.txt"))


def test_unexpected_exception_does_not_leak(settings):
    provider = FakeProvider(errors={"abcdefghijk": RuntimeError("secret /etc/path")})
    c = container_with(settings, FakeYouTube(), provider)
    snap = run_video(c, "abcdefghijk")
    assert snap.status == JobStatus.FAILED
    assert "secret" not in (snap.error or "")


# --------------------------------------------------------------- playlist


def run_playlist(container, options=OPTS):
    job = Job(SourceKind.PLAYLIST, "PLtest1234567")
    container.playlists.run_playlist_job(job, "PLtest1234567", options)
    return job.snapshot()


def test_playlist_numbered_in_order(settings):
    pl = playlist_of("Python Variables", "Python Conditions", "Python Loops", "Python Functions")
    c = container_with(settings, FakeYouTube(playlist=pl), FakeProvider(default=make_segments(5)))
    snap = run_playlist(c)
    assert snap.status == JobStatus.COMPLETED
    folder = settings.output_dir / "Test Course"
    assert sorted(p.name for p in folder.glob("*.txt")) == [
        "001_Python Variables.txt", "002_Python Conditions.txt",
        "003_Python Loops.txt", "004_Python Functions.txt",
    ]
    assert snap.zip_available


def test_playlist_continues_after_failures(settings):
    pl = playlist_of("A", "B", "C", "D", "E", unavailable=[2])
    ids = [e.video_id for e in pl.entries]
    provider = FakeProvider(
        default=make_segments(5),
        errors={ids[2]: TranscriptsDisabledError(), ids[3]: NetworkError()},
    )
    c = container_with(settings, FakeYouTube(playlist=pl), provider)
    snap = run_playlist(c)
    assert snap.status == JobStatus.PARTIAL
    assert [i.status for i in snap.items] == [
        ItemStatus.SUCCESS, ItemStatus.UNAVAILABLE, ItemStatus.NO_TRANSCRIPT,
        ItemStatus.FAILED, ItemStatus.SUCCESS,
    ]
    assert (snap.summary.succeeded, snap.summary.unavailable,
            snap.summary.no_transcript, snap.summary.failed) == (2, 1, 1, 1)
    # Failed videos leave no partial or placeholder files behind.
    names = sorted(p.name for p in (settings.output_dir / "Test Course").glob("*.txt"))
    assert names == ["001_A.txt", "005_E.txt"]


def test_playlist_resume_skips_completed_videos(settings):
    pl = playlist_of("A", "B", "C")
    ids = [e.video_id for e in pl.entries]
    provider = FakeProvider(default=make_segments(5), errors={ids[2]: NetworkError()})
    c = container_with(settings, FakeYouTube(playlist=pl), provider)
    run_playlist(c)

    provider.errors.clear()
    provider.calls.clear()
    snap = run_playlist(c)
    assert provider.calls == [ids[2]]
    assert [i.status for i in snap.items] == [ItemStatus.EXISTING, ItemStatus.EXISTING, ItemStatus.SUCCESS]
    assert snap.status == JobStatus.COMPLETED


def test_playlist_duplicate_titles_get_unique_files(settings):
    pl = playlist_of("Same", "Same", "Same")
    c = container_with(settings, FakeYouTube(playlist=pl), FakeProvider(default=make_segments(1)))
    run_playlist(c)
    names = sorted(p.name for p in (settings.output_dir / "Test Course").glob("*.txt"))
    assert names == ["001_Same.txt", "002_Same.txt", "003_Same.txt"]


def test_playlist_all_failed_is_failed(settings):
    pl = playlist_of("A", "B", unavailable=[1, 2])
    c = container_with(settings, FakeYouTube(playlist=pl), FakeProvider(default=make_segments(1)))
    snap = run_playlist(c)
    assert snap.status == JobStatus.FAILED
    assert snap.error


def test_long_transcript_is_not_truncated(settings):
    segments = [TranscriptSegment(i * 3.0, 3.0, f"sentence number {i}") for i in range(5000)]
    yt = FakeYouTube(videos={"abcdefghijk": VideoInfo("abcdefghijk", "Long", 15000.0)})
    c = container_with(settings, yt, FakeProvider(default=segments))
    run_video(c, "abcdefghijk")
    text = (settings.output_dir / "Long.txt").read_text(encoding="utf-8")
    assert text.count("sentence number") == 5000
    assert "[04:09:57]\nsentence number 4999" in text
