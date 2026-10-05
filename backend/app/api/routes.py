"""HTTP endpoints. Kept thin: validation, dispatch to services, response shaping."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response

from app.container import Container
from app.exceptions import ExtractorError
from app.models.domain import SourceKind
from app.models.schemas import (
    ErrorResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    JobStatusResponse,
    ZipRequest,
)
from app.services.extraction_service import ExtractionOptions
from app.services.job_manager import Job
from app.utils.filename import sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def get_container(request: Request) -> Container:
    return request.app.state.container


def _error(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=ErrorResponse(error=message, code=code).model_dump())


def _get_job(container: Container, job_id: str) -> Job | None:
    if not _ID_RE.match(job_id):
        return None
    return container.jobs.get(job_id)


@router.get("/health", response_model=HealthResponse)
def health(container: Container = Depends(get_container)) -> HealthResponse:
    s = container.settings
    return HealthResponse(
        status="ok",
        whisper_available=s.enable_whisper_fallback and container.whisper.is_installed(),
        whisper_model=s.whisper_model,
        ffmpeg_available=container.youtube.ffmpeg_available,
        default_language=s.default_language,
    )


@router.post(
    "/transcripts/extract",
    response_model=ExtractResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}},
)
def extract(body: ExtractRequest, container: Container = Depends(get_container)):
    try:
        parsed = container.youtube.parse_url(body.url)
    except ExtractorError as exc:
        return _error(status.HTTP_400_BAD_REQUEST, exc.user_message, exc.code)

    options = ExtractionOptions(
        language=(body.language or container.settings.default_language).lower(),
        overwrite=body.overwrite,
        remove_non_speech=body.remove_non_speech,
        whisper_on_rate_limit=body.whisper_on_rate_limit,
    )

    kind = parsed.kind
    source_id = parsed.playlist_id if kind == SourceKind.PLAYLIST else parsed.video_id
    assert source_id

    # The same video/playlist must never be processed twice at once (both runs
    # would write the same files); hand back the job that is already running.
    active = container.jobs.find_active(kind, source_id)
    if active is not None:
        logger.info("Job %s already running for %s %s", active.id, kind, source_id)
        return ExtractResponse(job_id=active.id, type=active.kind, status=active.status)

    job = container.jobs.create(kind, source_id)
    logger.info("Job %s: %s detected %s", job.id, kind, source_id)
    if kind == SourceKind.PLAYLIST:
        worker = lambda j: container.playlists.run_playlist_job(j, source_id, options)  # noqa: E731
    else:
        worker = lambda j: container.extraction.run_video_job(j, source_id, options)  # noqa: E731
    container.jobs.submit(job, worker)
    return ExtractResponse(job_id=job.id, type=job.kind, status=job.status)


@router.get(
    "/transcripts/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
def job_status(job_id: str, container: Container = Depends(get_container)):
    job = _get_job(container, job_id)
    if job is None:
        return _error(status.HTTP_404_NOT_FOUND, "Job not found. It may have expired.", "job_not_found")
    return job.snapshot()


@router.get("/transcripts/download/{file_id}", responses={404: {"model": ErrorResponse}})
def download_file(file_id: str, container: Container = Depends(get_container)):
    path = container.storage.resolve(file_id) if _ID_RE.match(file_id) else None
    if path is None:
        return _error(status.HTTP_404_NOT_FOUND, "File not found.", "file_not_found")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)


@router.get("/transcripts/jobs/{job_id}/download-all", responses={404: {"model": ErrorResponse}})
def download_all(job_id: str, container: Container = Depends(get_container)):
    job = _get_job(container, job_id)
    if job is None:
        return _error(status.HTTP_404_NOT_FOUND, "Job not found. It may have expired.", "job_not_found")
    paths = [p for p in (container.storage.resolve(fid) for fid in job.file_ids()) if p]
    if not paths:
        return _error(status.HTTP_404_NOT_FOUND, "This job has no transcripts to download.", "no_files")

    return _zip_response(container, paths, job.snapshot().playlist_title)


@router.post("/transcripts/download-zip", responses={404: {"model": ErrorResponse}})
def download_selected(body: ZipRequest, container: Container = Depends(get_container)):
    """ZIP of the transcripts the user ticked. Unknown ids are ignored."""
    ids = list(dict.fromkeys(fid for fid in body.file_ids if _ID_RE.match(fid)))
    paths = [p for p in (container.storage.resolve(fid) for fid in ids) if p]
    if not paths:
        return _error(status.HTTP_404_NOT_FOUND, "None of the selected transcripts were found.", "no_files")
    return _zip_response(container, paths, body.name)


def _zip_response(container: Container, paths: list, title: str | None) -> Response:
    name = sanitize_filename(title or "transcripts") + ".zip"
    try:
        data = container.storage.build_zip(paths)
    except ExtractorError as exc:
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, exc.user_message, exc.code)
    ascii_name = name.encode("ascii", "ignore").decode() or "transcripts.zip"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": disposition})
