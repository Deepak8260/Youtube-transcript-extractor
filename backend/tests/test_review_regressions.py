"""Regression tests for issues found in code review."""

import threading
from datetime import timedelta

import pytest
from yt_dlp.utils import DownloadError

from app.exceptions import RateLimitedError
from app.models.domain import PlaylistInfo, SourceKind
from app.models.schemas import JobStatus
from app.services.job_manager import JobManager
from app.services.storage_service import MAX_PATH_CHARS, StorageService
from app.services.youtube_service import YouTubeService

HEADER = (
    "==================================================\n"
    "Title: X\nSource: YouTube\nURL: https://www.youtube.com/watch?v=abcdefghijk\n"
    "==================================================\n\n[00:00:00]\npartial"
)


def test_leftover_temp_file_is_never_trusted(settings):
    """A write killed mid-way must not count as a finished transcript."""
    storage = StorageService(settings)
    storage.ensure_dirs()
    folder = settings.output_dir / "Course"
    folder.mkdir()
    for name in (".tmp-abc.txt", ".tmp-def.partial"):
        (folder / name).write_text(HEADER, encoding="utf-8")

    assert storage.index_directory(folder) == {}
    assert storage.build_zip([folder / ".tmp-abc.txt"]) != b""  # empty but valid zip
    storage.cleanup_temp()
    assert list(folder.iterdir()) == []


def test_atomic_write_temp_name_is_not_a_txt(settings, monkeypatch):
    storage = StorageService(settings)
    storage.ensure_dirs()
    seen = []
    import app.services.storage_service as mod

    real = mod.tempfile.mkstemp
    monkeypatch.setattr(mod.tempfile, "mkstemp", lambda **kw: seen.append(kw) or real(**kw))
    storage.write_atomic(settings.output_dir / "A.txt", "x")
    assert seen[0]["suffix"] != ".txt" and seen[0]["prefix"].startswith(".")


def test_concurrent_same_title_reservations_never_collide(settings):
    storage = StorageService(settings)
    storage.ensure_dirs()
    results, barrier = [], threading.Barrier(8)

    def reserve(i):
        barrier.wait()
        results.append(storage.reserve_single("Intro", f"vid{i:08d}"[:11])[0])

    threads = [threading.Thread(target=reserve, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 8


def test_playlist_targets_reserved_across_callers(settings):
    storage = StorageService(settings)
    folder = settings.output_dir / "pl"
    a = storage.reserve_playlist_target(folder, 1, 3, "Same")
    b = storage.reserve_playlist_target(folder, 1, 3, "Same")
    assert a != b
    storage.release(a)
    assert storage.reserve_playlist_target(folder, 1, 3, "Same") == a


def test_long_titles_fit_windows_max_path(settings):
    storage = StorageService(settings)
    storage.ensure_dirs()
    title = "A very long video title that goes on and on " * 10
    folder = storage.playlist_dir(PlaylistInfo("PL" + "x" * 32, title, []))
    target = storage.reserve_playlist_target(folder, 123, 3, title)
    assert len(str(target)) + len(" (999)") <= MAX_PATH_CHARS
    single, _ = storage.reserve_single(title, "abcdefghijk")
    assert len(str(single)) + len(" (999)") <= MAX_PATH_CHARS


def test_playlist_listing_rate_limit_is_not_reported_as_missing(settings, monkeypatch):
    class Boom:
        def __init__(self, opts):
            assert opts["ignoreerrors"] == "only_download"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **kw):
            raise DownloadError("ERROR: HTTP Error 429: Too Many Requests")

    import app.services.youtube_service as mod

    monkeypatch.setattr(mod.yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(RateLimitedError):
        YouTubeService(settings).get_playlist("PLtest1234567")


def test_job_stays_queued_until_a_worker_starts():
    manager = JobManager(max_workers=1, retention=timedelta(hours=1))
    gate = threading.Event()
    first = manager.create(SourceKind.VIDEO, "aaaaaaaaaaa")
    manager.submit(first, lambda j: gate.wait(5))
    second = manager.create(SourceKind.VIDEO, "bbbbbbbbbbb")
    manager.submit(second, lambda j: None)
    try:
        assert second.status == JobStatus.QUEUED
        assert manager.find_active(SourceKind.VIDEO, "bbbbbbbbbbb") is second
        assert manager.find_active(SourceKind.PLAYLIST, "bbbbbbbbbbb") is None
    finally:
        gate.set()
        manager.shutdown()
