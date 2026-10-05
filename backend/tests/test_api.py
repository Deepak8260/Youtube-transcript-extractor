import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.container import build_container
from app.main import create_app
from app.models.domain import VideoInfo

from .conftest import FakeProvider, FakeYouTube, make_segments, playlist_of


@pytest.fixture
def client(settings):
    pl = playlist_of("Python Variables", "Python Loops", unavailable=[2])
    youtube = FakeYouTube(
        videos={"dQw4w9WgXcQ": VideoInfo("dQw4w9WgXcQ", "What is Claude Code?", 40.0)},
        playlist=pl,
    )
    container = build_container(settings, youtube=youtube, providers=[FakeProvider(default=make_segments(10))])
    with TestClient(create_app(settings, container)) as c:
        yield c


def wait_for(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/transcripts/jobs/{job_id}").json()
        if body["status"] in ("completed", "partial", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["whisper_available"] is False


def test_extract_video_end_to_end(client):
    resp = client.post("/api/transcripts/extract", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["type"] == "video"

    job = wait_for(client, body["job_id"])
    assert job["status"] == "completed"
    assert job["progress"] == 100
    file = job["files"][0]
    assert file["filename"] == "What is Claude Code.txt"
    assert file["transcript_source"] == "YouTube Captions"
    assert "path" not in file  # server paths are never exposed

    download = client.get(f"/api/transcripts/download/{file['file_id']}")
    assert download.status_code == 200
    assert download.text.startswith("=" * 50)
    assert "[00:00:00]\nline 0" in download.text
    assert "What%20is%20Claude%20Code.txt" in download.headers["content-disposition"]


def test_extract_playlist_and_zip(client):
    resp = client.post("/api/transcripts/extract",
                       json={"url": "https://www.youtube.com/playlist?list=PLtest1234567"})
    assert resp.json()["type"] == "playlist"
    job = wait_for(client, resp.json()["job_id"])
    assert job["status"] == "partial"
    assert job["summary"]["succeeded"] == 1 and job["summary"]["unavailable"] == 1
    assert job["zip_available"]

    z = client.get(f"/api/transcripts/jobs/{job['job_id']}/download-all")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ["001_Python Variables.txt"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"url": "not a url"}, "invalid_url"),
        ({"url": "https://vimeo.com/123"}, "unsupported_url"),
        ({"url": "https://www.youtube.com/@channel"}, "unsupported_url"),
    ],
)
def test_extract_rejects_bad_urls(client, payload, code):
    resp = client.post("/api/transcripts/extract", json=payload)
    assert resp.status_code == 400
    assert resp.json()["code"] == code
    assert resp.json()["error"]


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "https://youtu.be/dQw4w9WgXcQ", "language": "../x"}])
def test_extract_validation_errors_are_friendly(client, payload):
    resp = client.post("/api/transcripts/extract", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_request"
    assert "Traceback" not in resp.text


def test_unknown_job_and_file(client):
    assert client.get("/api/transcripts/jobs/0123456789abcdef").status_code == 404
    assert client.get("/api/transcripts/jobs/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/transcripts/download/doesnotexist123").status_code == 404
    assert client.get("/api/transcripts/download/..%2F..%2Fsecret").status_code == 404
    assert client.get("/api/transcripts/jobs/0123456789abcdef/download-all").status_code == 404


def test_download_selected_zip(client):
    resp = client.post("/api/transcripts/extract",
                       json={"url": "https://www.youtube.com/playlist?list=PLtest1234567"})
    job = wait_for(client, resp.json()["job_id"])
    file_id = job["files"][0]["file_id"]

    z = client.post("/api/transcripts/download-zip",
                    json={"file_ids": [file_id, file_id, "unknown-id-123", "../etc"], "name": "My: Pick"})
    assert z.status_code == 200
    assert 'filename="My - Pick.zip"' in z.headers["content-disposition"]
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ["001_Python Variables.txt"]

    assert client.post("/api/transcripts/download-zip", json={"file_ids": ["nope-nope-nope"]}).status_code == 404
    assert client.post("/api/transcripts/download-zip", json={"file_ids": []}).status_code == 422
