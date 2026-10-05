"""FastAPI application entry point: ``uvicorn app.main:app``."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings, get_settings
from app.container import Container, build_container

logger = logging.getLogger("app")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Model downloads are chatty at INFO; keep the log about our own events.
    for noisy in ("httpx", "faster_whisper"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.access").addFilter(_QuietPolling())


class _QuietPolling(logging.Filter):
    """Drop access-log lines for the UI's once-a-second status polling and
    health checks, so the log shows what the extractor is actually doing."""

    _PATHS = ("/api/transcripts/jobs/", "/api/health")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        path = str(args[2]) if len(args) > 2 else ""
        status = args[4] if len(args) > 4 else 200
        return not (path.startswith(self._PATHS) and status == 200 and "download" not in path)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.storage.ensure_dirs()
        container.storage.cleanup_temp()
        logger.info(
            "Transcript extractor ready (output=%s, whisper=%s, ffmpeg=%s)",
            settings.output_dir.name,
            "on" if settings.enable_whisper_fallback and container.whisper.is_installed() else "off",
            "found" if container.youtube.ffmpeg_available else "missing",
        )
        yield
        container.jobs.shutdown()

    app = FastAPI(title="YouTube Transcript Extractor", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("Rejected request: %s", exc.errors())
        return JSONResponse(
            status_code=422,
            content={"error": "The request was invalid. Paste a YouTube video or playlist URL.",
                     "code": "invalid_request"},
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": "An unexpected server error occurred.", "code": "server_error"},
        )

    app.include_router(router)
    return app


app = create_app()
