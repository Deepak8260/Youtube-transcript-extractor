"""Everything that touches the transcripts directory.

* Files are written atomically (temp file + rename), so a crash or failure
  never leaves a half-written transcript that resume logic would trust.
* Downloads are addressed by opaque random ids, never by path.
* Every path is resolved and checked to live under OUTPUT_DIR.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.exceptions import StorageError
from app.models.domain import PlaylistInfo
from app.utils.filename import safe_join, sanitize_filename, unique_path
from app.utils.formatter import parse_header
from app.utils.url_parser import parse_youtube_url

logger = logging.getLogger(__name__)

MANIFEST_NAME = ".playlist.json"
HEADER_READ_BYTES = 4096
TEMP_PREFIX = ".tmp-"
TEMP_SUFFIX = ".partial"

# Keep full paths under Windows' default 260-char MAX_PATH (long paths are off
# by default). Applied on every OS so output is portable.
MAX_PATH_CHARS = 250
_NAME_OVERHEAD = len(" (999).txt")
MIN_STEM_BYTES = 24


def _stem_budget(directory: Path, prefix_len: int = 0, default: int = 180) -> int:
    """UTF-8 byte budget for a filename stem so ``directory/prefix+stem (n).txt``
    stays under MAX_PATH_CHARS. (Bytes >= chars, so a byte cap is a char cap.)"""
    room = MAX_PATH_CHARS - len(str(directory)) - 1 - prefix_len - _NAME_OVERHEAD
    return max(MIN_STEM_BYTES, min(default, room))


def _is_transcript(path: Path) -> bool:
    """Final transcript files only: never temp files, manifests or other dotfiles."""
    return path.suffix == ".txt" and not path.name.startswith(".")


@dataclass(frozen=True, slots=True)
class StoredFile:
    file_id: str
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.output_dir
        self._temp = settings.temp_dir
        self._files: dict[str, Path] = {}
        self._by_path: dict[Path, str] = {}
        self._lock = threading.Lock()
        # Filenames chosen but not yet written, shared by every job so two
        # concurrent jobs can never pick (and overwrite) the same file.
        self._name_lock = threading.RLock()
        self._reserved: set[Path] = set()

    @property
    def root(self) -> Path:
        return self._root

    def ensure_dirs(self) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            self._temp.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(detail=repr(exc)) from exc

    def cleanup_temp(self) -> None:
        """Remove leftovers of a crashed run: temp audio and half-written files."""
        if self._temp.exists():
            for child in self._temp.iterdir():
                if child.name.startswith("whisper-"):
                    shutil.rmtree(child, ignore_errors=True)
        if self._root.exists():
            for stale in self._root.rglob(f"{TEMP_PREFIX}*"):
                if stale.is_file():
                    logger.info("Removing incomplete file %s", stale.relative_to(self._root))
                    stale.unlink(missing_ok=True)

    # ------------------------------------------------------------ registry

    def register(self, path: Path) -> StoredFile:
        resolved = self._checked(path)
        with self._lock:
            file_id = self._by_path.get(resolved)
            if file_id is None:
                file_id = secrets.token_urlsafe(16)
                self._files[file_id] = resolved
                self._by_path[resolved] = file_id
        return StoredFile(file_id, resolved)

    def resolve(self, file_id: str) -> Path | None:
        with self._lock:
            path = self._files.get(file_id)
        if path is None:
            return None
        try:
            path = self._checked(path)
        except StorageError:
            return None
        return path if path.is_file() else None

    def _checked(self, path: Path) -> Path:
        try:
            return safe_join(self._root, str(path.resolve().relative_to(self._root)))
        except ValueError as exc:
            raise StorageError(detail=f"path outside output dir: {path}") from exc

    # ------------------------------------------------------------- writing

    def write_atomic(self, path: Path, content: str) -> Path:
        target = self._checked(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, target)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StorageError(detail=repr(exc)) from exc
        logger.info("Saved transcript %s", target.relative_to(self._root))
        return target

    # ----------------------------------------------------- existing files

    @staticmethod
    def video_id_of(path: Path) -> str | None:
        """Video id recorded in a transcript file's header, if it is ours."""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(HEADER_READ_BYTES)
        except OSError:
            return None
        url = parse_header(head).get("URL")
        if not url:
            return None
        try:
            return parse_youtube_url(url).video_id
        except Exception:
            return None

    def reserve_single(self, title: str, video_id: str) -> tuple[Path, Path | None]:
        """Return ``(path_to_write, existing_transcript_of_same_video_or_None)``.

        "Title.txt" belonging to a *different* video yields "Title (2).txt".
        The returned path is reserved; call :meth:`release` when done with it.
        """
        stem = sanitize_filename(title, max_bytes=_stem_budget(self._root))
        with self._name_lock:
            candidate = self._root / f"{stem}.txt"
            counter = 2
            while candidate.exists() or candidate in self._reserved:
                if candidate.exists() and self.video_id_of(candidate) == video_id:
                    self._reserved.add(candidate)
                    return candidate, candidate
                candidate = self._root / f"{stem} ({counter}).txt"
                counter += 1
            self._reserved.add(candidate)
            return candidate, None

    def reserve_playlist_target(self, directory: Path, index: int, width: int, title: str) -> Path:
        """Pick and reserve ``NNN_Title.txt`` in ``directory``."""
        prefix = f"{index:0{width}d}_"
        stem = prefix + sanitize_filename(title, max_bytes=_stem_budget(directory, len(prefix)))
        with self._name_lock:
            target = unique_path(directory, stem, taken=self._reserved)
            self._reserved.add(target)
            return target

    def release(self, path: Path) -> None:
        with self._name_lock:
            self._reserved.discard(path)

    def index_directory(self, directory: Path) -> dict[str, Path]:
        """Map video id -> transcript file for every transcript in ``directory``."""
        found: dict[str, Path] = {}
        if not directory.is_dir():
            return found
        for path in sorted(p for p in directory.glob("*.txt") if _is_transcript(p)):
            video_id = self.video_id_of(path)
            if video_id and video_id not in found:
                found[video_id] = path
        return found

    # ---------------------------------------------------------- playlists

    def playlist_dir(self, playlist: PlaylistInfo) -> Path:
        """``transcripts/<Playlist Title>/``. Re-running the same playlist reuses
        its folder (enabling resume); a different playlist with the same title
        gets ``<Title> [<playlist id>]``."""
        # Leave room for " [<id>]" and a reasonably long file name inside.
        budget = MAX_PATH_CHARS - len(str(self._root)) - 1 - len(playlist.playlist_id) - 3 - 80
        stem = sanitize_filename(playlist.title, max_bytes=max(MIN_STEM_BYTES, min(120, budget)))
        with self._name_lock:  # two playlists must not claim the same folder
            for name in (stem, f"{stem} [{playlist.playlist_id}]"):
                directory = safe_join(self._root, name)
                owner = self._manifest_owner(directory)
                if owner == playlist.playlist_id:
                    return directory
                if owner is None and (not directory.exists() or not any(directory.iterdir())):
                    self._write_manifest(directory, playlist)
                    return directory
        raise StorageError(detail=f"no free directory for playlist {playlist.playlist_id}")

    @staticmethod
    def _manifest_owner(directory: Path) -> str | None:
        try:
            data = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        owner = data.get("playlist_id") if isinstance(data, dict) else None
        return owner if isinstance(owner, str) else None

    @staticmethod
    def _write_manifest(directory: Path, playlist: PlaylistInfo) -> None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / MANIFEST_NAME).write_text(
                json.dumps({"playlist_id": playlist.playlist_id, "title": playlist.title},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageError(detail=repr(exc)) from exc

    # ---------------------------------------------------------------- zip

    def build_zip(self, files: Iterable[Path]) -> bytes:
        """ZIP of transcript .txt files only, flat, deterministic order."""
        buffer = io.BytesIO()
        seen: set[str] = set()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(files, key=lambda p: p.name):
                checked = self._checked(path)
                if not _is_transcript(checked) or not checked.is_file() or checked.name in seen:
                    continue
                seen.add(checked.name)
                zf.write(checked, arcname=checked.name)
        return buffer.getvalue()
