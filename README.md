# YouTube Transcript Extractor

Paste a YouTube **video** or **playlist** URL and get the **complete, timestamped
transcript** of every video as a `.txt` file named after the real YouTube title.

- Uses YouTube's own captions when they exist (manual first, then auto-generated).
- If YouTube blocks the caption server, reads the same text from the page's **"Show transcript"** panel instead.
- Falls back to local speech-to-text with **faster-whisper** when they don't.
- Never summarizes, paraphrases or truncates. No LLMs, no API keys.

```
==================================================
Title: Why I Love My Bad Days | Alexi Pappas | TED
Source: YouTube
URL: https://www.youtube.com/watch?v=kz-I5zIGbj4
Transcript Source: YouTube Captions
Language: English (en)
Duration: 00:05:11
==================================================

[00:00:04]
I was one month out from my Olympic race in Rio,

[00:00:08]
the 10,000 meters,
...
```

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Requirements](#requirements)
- [Quick start with Docker](#quick-start-with-docker)
- [Local development](#local-development)
- [Installing FFmpeg](#installing-ffmpeg)
- [Usage](#usage)
- [Supported URLs](#supported-urls)
- [How it works](#how-it-works)
- [Output](#output)
- [Configuration](#configuration)
- [API](#api)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Future improvements](#future-improvements)

---

## Features

- **Single videos and playlists.** The app detects which one you pasted.
- **Real timestamps** in `[HH:MM:SS]` format, taken from YouTube or Whisper and never invented.
- **Complete transcripts.** Every caption segment is kept, and transcripts that end well before the video does are flagged in the file and the UI.
- **Whisper fallback** for videos without captions. It downloads audio only and deletes it afterwards.
- **Title-based filenames** that are safe on every OS: `What is Claude Code? | Guide` becomes `What is Claude Code - Guide.txt`.
- **Playlist order preserved** with numbered files (`001_…`, `002_…`) in a folder per playlist.
- **Resume.** Re-running a playlist skips videos that already have a transcript.
- **Resilient playlists.** One private or caption-less video never stops the rest, and a summary is shown at the end.
- **Download** single files, or a whole playlist as a ZIP.
- **Background jobs** with live progress in the UI.
- **Optional removal** of non-speech cues such as `[Music]` and `[Applause]`. Spoken words are never removed.

## Architecture

```
┌──────────────┐  POST /extract  ┌──────────────────────────────────────────────┐
│ React UI     │ ───────────────▶│ FastAPI (routes.py)                          │
│ (Vite, TS)   │ ◀── poll ────── │   validates URL → creates Job → returns 202  │
└──────────────┘  GET /jobs/{id} └───────────────┬──────────────────────────────┘
                                                 │ JobManager (thread pool)
                                                 ▼
                        ┌────────────────────────────────────────────┐
                        │ ExtractionService / PlaylistService        │
                        │   YouTubeService  (yt-dlp: metadata,       │
                        │                    playlists, audio)       │
                        │   TranscriptService                        │
                        │     ├─ YouTubeTranscriptProvider           │
                        │     └─ WhisperTranscriptProvider (fallback)│
                        │   StorageService  (atomic writes, resume,  │
                        │                    file ids, ZIP)          │
                        └────────────────────────────────────────────┘
```

```
backend/
  app/
    main.py                 App factory, logging, error handlers
    container.py            Dependency wiring (swap in fakes for tests)
    config.py               Settings from env / .env
    exceptions.py           Domain errors with user-safe messages
    api/routes.py           Thin HTTP layer
    models/
      domain.py             VideoInfo, PlaylistInfo, TranscriptSegment, ...
      schemas.py            Pydantic API models
    services/
      youtube_service.py    URL type, metadata, playlist listing, audio download
      transcript_service.py TranscriptProvider ABC, YouTube captions, cleaning, verification
      whisper_service.py    faster-whisper model + WhisperTranscriptProvider
      extraction_service.py One video → one saved file
      playlist_service.py   Ordered, resumable, bounded-concurrency playlist runs
      storage_service.py    Atomic writes, duplicate/resume detection, ZIP, file ids
      job_manager.py        Thread-safe job state + background execution
      progress.py           Stages + progress callback type
    utils/
      url_parser.py  filename.py  timestamps.py  formatter.py  retry.py
  tests/                    159 tests, no network access
frontend/
  src/App.tsx               State machine + polling
  src/components/           ExtractForm, JobView, ItemList, ProgressBar
examples/                   Real output sample
docker-compose.yml
```

The job runner is a plain `Callable[[Job], None]`. To move to Celery or RQ, replace
`JobManager.submit` with an enqueue call and store `Job` state in Redis. The services
stay unchanged.

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, pydantic-settings, Uvicorn |
| YouTube metadata, playlists, audio | yt-dlp (+ yt-dlp-ejs with Deno or Node for YouTube's JS challenges) |
| Captions | youtube-transcript-api |
| Speech-to-text fallback | faster-whisper (CTranslate2) |
| Audio | PyAV (bundled with faster-whisper); FFmpeg as fallback decoder |
| UI | React 19 + Vite + TypeScript. React + Vite is enough for a single page with no server rendering. |
| Deploy | Docker, docker compose, nginx |

## Requirements

- **Docker** (recommended), _or_:
- **Python 3.11+** (tested on 3.12 and 3.14)
- **Node.js 20+** (for the UI)
- **FFmpeg**: recommended, see [below](#installing-ffmpeg)
- **Deno or Node.js** on `PATH`: lets yt-dlp solve YouTube's player challenges for audio download (Whisper fallback)

## Quick start with Docker

```bash
docker compose up -d --build      # -d = run in the background
docker ps                         # backend + frontend should be "Up"
docker compose logs -f backend    # follow the extractor's log (Ctrl+C stops following, not the app)
docker compose down               # stop everything
```

Open **http://localhost:8080**. Without `-d`, compose stays attached to your terminal
and streams logs, and closing the terminal stops the app.

- Transcripts are written to `./backend/transcripts/` on your machine, so they survive restarts.
- Whisper models are cached in the `whisper-models` volume. The first fallback downloads the model, about 460 MB for `small`.
- The backend image includes FFmpeg and Deno.
- Optional: copy `backend/.env.example` to `backend/.env` to change settings.
- The image installs the exact library versions this project was tested with (`backend/constraints.txt`).
- **Linux hosts:** the container runs as UID 1000. If your user has a different UID, make the
  output folder writable first with `sudo chown -R 1000:1000 backend/transcripts`.

### Prebuilt images (Docker Hub)

| Image | Tags |
|---|---|
| [`kumar3472/utube-transcript-backend`](https://hub.docker.com/r/kumar3472/utube-transcript-backend) | `latest`, `1` |
| [`kumar3472/utube-transcript-frontend`](https://hub.docker.com/r/kumar3472/utube-transcript-frontend) | `latest`, `1` |

Run without building, on any machine with Docker and this repo's `docker-compose.yml`:

```bash
docker compose pull && docker compose up -d     # http://localhost:8080
TAG=1 docker compose pull && TAG=1 docker compose up -d   # a specific version
```

Publish a new version: bump the number, then build and push both tags:

```bash
TAG=2 docker compose build && TAG=2 docker compose push
docker compose build && docker compose push     # also move "latest"
```

## Local development

**Backend**

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # optional
uvicorn app.main:app --reload --port 8000
```

**Frontend** (second terminal)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Vite forwards `/api` to `http://127.0.0.1:8000`, so
no CORS setup is needed. Set `API_PROXY` to point the proxy somewhere else.

## Installing FFmpeg

faster-whisper decodes audio with PyAV, which bundles FFmpeg's libraries, so most
videos work without a system FFmpeg. The `ffmpeg` binary is used as a fallback
decoder for unusual audio formats, and by yt-dlp for post-processing. Installing it
is recommended:

| OS | Command |
|---|---|
| Windows | `winget install Gyan.FFmpeg` or `choco install ffmpeg` |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |

`GET /api/health` reports `"ffmpeg_available"`.

## Usage

1. Paste a video or playlist URL.
2. Optionally choose a language, keep non-speech cues, or turn on **Overwrite existing transcripts**.
3. Click **Extract Transcript**.
4. Watch the progress, then download the `.txt` file, or **Download All Transcripts** as a ZIP for playlists.

The UI walks through these states: Validating URL → Detecting source → Fetching
video information → Extracting transcript → Using Whisper fallback → Saving
transcript → Completed / Partial success / Failed.

Reloading the page resumes polling the last job. `/?job=<id>` reopens any recent job.

## Supported URLs

| Kind | Examples |
|---|---|
| Video | `youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/shorts/ID`, `/embed/ID`, `/live/ID`, `m.` and `music.` subdomains, `youtube-nocookie.com/embed/ID` |
| Playlist | `youtube.com/playlist?list=ID` |

- `watch?v=ID&list=…` is treated as **the single video**. Paste the `/playlist?list=…` URL to process the whole playlist.
- Channel pages, search results, and personal lists (Watch Later, Liked) are rejected with a clear message.
- Every video and playlist ID is checked against a strict pattern before anything is sent to yt-dlp.

## How it works

### Transcript extraction

1. `yt-dlp` reads the video's **real title** and duration. Nothing is downloaded. If yt-dlp is blocked, the public oEmbed endpoint supplies the title.
2. `youtube-transcript-api` lists the caption tracks and picks one in this order:
   1. Manual track in the requested language (`en` also matches `en-US`, `en-GB`, …)
   2. Auto-generated track in the requested language
   3. If `ALLOW_OTHER_LANGUAGES=true`: a manual track, then an auto-generated track, in any language
3. The whole track is fetched in one request, with no pagination and no time cap.
4. Cleaning normalizes whitespace and, by default, removes cues that are **entirely**
   non-speech: `[Music]`, `(applause)`, `[♪♪♪]`, and localized tags such as `[Música]` or `[संगीत]`.
   `[inaudible]`, `(John)` and song lyrics are kept. A segment is dropped only if nothing remains after cleaning.
5. Verification: if the last segment ends before 80% of the video and more than 60 s early,
   a `Warning:` line is added to the header and shown in the UI.
6. The file is written to a temp file and renamed into place, so a crash never leaves a half-written transcript.

### Transcript-panel fallback

YouTube serves the same transcript through two endpoints:

| Source | Endpoint | Notes |
|---|---|---|
| Caption track (primary) | `/api/timedtext` via youtube-transcript-api | Language choice, manual vs auto label, exact timings |
| "Show transcript" panel (fallback) | `youtubei/v1/get_panel`, the call the watch page itself makes | Whole-second start times, YouTube's default track, labelled `YouTube Transcript` |

When the caption server fails or rate-limits your IP (HTTP 429), the app reads the
panel instead. This needs no browser, only two plain HTTPS requests per video, and
often still works while the caption server is blocked. If the panel ever returns the
transcript in pages, which isn't supported yet, the video fails rather than saving a
partial transcript. Turn this off with `ENABLE_TRANSCRIPT_PANEL=false`.

### Whisper fallback

Whisper is used when there is no usable caption track: captions disabled, no track
found, an empty track, or only non-speech cues.

```
YouTube captions ──available──▶ clean ─▶ verify ─▶ save .txt
       │ unavailable
       ▼
yt-dlp downloads best audio-only stream ─▶ temp/whisper-<random>/
       ▼
faster-whisper (VAD on, beam 5) ─▶ timestamped segments
       ▼
verify audio length ≈ video length ─▶ clean ─▶ save .txt (same format)
       ▼
temp directory deleted (always, including on failure)
```

- The model is set by `WHISPER_MODEL` (default `small`), loaded once, and shared across jobs.
- Only one transcription runs at a time, because Whisper saturates the CPU or GPU on its own.
- If the downloaded audio is clearly shorter than the video, the job **fails** rather than saving a partial transcript.
- A duration limit (`MAX_AUDIO_DURATION_SECONDS`), a size limit (`MAX_AUDIO_FILESIZE_MB`) and a timeout (`WHISPER_TIMEOUT_SECONDS`) guard against runaway jobs.
- If YouTube **rate-limits** caption requests, the app does _not_ switch to Whisper by
  default. Rate limits are temporary, and Whisper is slower and lower quality than real captions.
  The video is reported as failed with a "try again later" message, and resume picks it up on the
  next run. Set `WHISPER_ON_RATE_LIMIT=true` to change this.

### Timestamps

Each segment's `start` comes straight from YouTube's caption track or from Whisper's
segment timing. It is formatted as `HH:MM:SS` and floored, so a line never appears to
start later than it does. Hours are always shown (`[00:04:12]`, `[01:12:34]`). By default
there is one timestamp per source segment. `SEGMENT_GROUP_SECONDS=30` joins segments
into ~30 s blocks without losing any words. Each block takes the timestamp of its first segment.

### Playlists

1. yt-dlp lists the playlist with `extract_flat`. This is one cheap request that returns every ID and title in order, up to `MAX_PLAYLIST_VIDEOS`. If the playlist is larger, the UI says so.
2. Output goes to `transcripts/<Playlist Title>/`. A small `.playlist.json` records which playlist owns the folder, so a different playlist with the same title gets `<Title> [<id>]`.
3. Existing `.txt` files in that folder are indexed by the video URL in their header. On a re-run those videos are reported as **Already extracted** and not fetched again. This is the resume behaviour.
4. Videos are processed by a small worker pool (`PLAYLIST_CONCURRENCY=2`) with a pause between videos. Temporary errors are retried with exponential backoff.
5. Every video ends in one of: ✓ extracted · ✓ already extracted · ✕ private/unavailable · ⚠ transcript unavailable · ✕ failed. One failure never stops the rest.

### File naming

`sanitize_filename(title)`:

- turns `:` and `|` into ` - `, turns `/` and `\` into `-`, and removes `? * " < >`
- removes control and invisible characters (zero-width, bidi overrides)
- collapses whitespace and strips leading and trailing dots and spaces (no hidden files, no `..`)
- keeps Unicode (accents, CJK, emoji) and NFC-normalizes it
- prefixes Windows reserved names (`CON`, `NUL`, `COM1`, …) with `_`
- caps the name at 180 UTF-8 bytes without splitting characters, and falls back to `Untitled`

Single videos are saved as `Title.txt`. If that file belongs to a **different** video
with the same title, the new file is `Title (2).txt`. If it belongs to the **same**
video, extraction is skipped unless **Overwrite** is on.

## Output

```
backend/transcripts/
├── What is Claude Code.txt
└── Python Programming Course/
    ├── .playlist.json
    ├── 001_Python Variables.txt
    ├── 002_Python Conditions.txt
    └── 003_Python Loops.txt
```

See [`examples/Me at the zoo.txt`](examples/Me%20at%20the%20zoo.txt) for a real file.
Header fields are Title, Source, URL, Transcript Source, Language and Duration, plus
`Warning:` lines only when something looks off.

## Configuration

All settings are environment variables, which can also go in `backend/.env`. See
[`backend/.env.example`](backend/.env.example) for the full list. The most useful ones:

| Variable | Default | Purpose |
|---|---|---|
| `OUTPUT_DIR` | `./transcripts` | Final transcript files |
| `TEMP_DIR` | `./temp` | Temporary audio (always cleaned) |
| `DEFAULT_LANGUAGE` | `en` | Preferred caption language |
| `ALLOW_OTHER_LANGUAGES` | `true` | Use the video's own language if the preferred one is missing |
| `REMOVE_NON_SPEECH` | `true` | Strip `[Music]`-style cues |
| `ENABLE_WHISPER_FALLBACK` | `true` | Use Whisper when there are no captions |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda` or `auto` |
| `MAX_PLAYLIST_VIDEOS` | `500` | Playlist cap |
| `PLAYLIST_CONCURRENCY` | `2` | Parallel videos per playlist |
| `ENABLE_TRANSCRIPT_PANEL` | `true` | Fall back to the "Show transcript" panel when captions are blocked |
| `PROXY_URL` | _(empty)_ | HTTP(S) proxy for all YouTube traffic |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/transcripts/extract` | `{"url": "...", "language": "en", "overwrite": false, "remove_non_speech": true}` → `202 {"job_id", "type", "status"}` |
| `GET` | `/api/transcripts/jobs/{job_id}` | Status, stage, progress, current video, per-video items, files, summary |
| `GET` | `/api/transcripts/download/{file_id}` | Download one transcript |
| `GET` | `/api/transcripts/jobs/{job_id}/download-all` | ZIP of a playlist job's transcripts |
| `GET` | `/api/health` | Whisper and FFmpeg availability |

Interactive docs are at `http://localhost:8000/docs`. Errors always look like
`{"error": "<human-readable message>", "code": "<machine code>"}`. Tracebacks and
server paths are logged on the server and never returned to the client. Files are
addressed by random IDs, never by path.

## Testing

```bash
cd backend
pytest            # 159 tests, about 2 s, no network
ruff check app tests
```

```bash
cd frontend
npm run build     # type-check + production build
```

The tests cover URL parsing (valid, invalid and hostile URLs), filename sanitization
(traversal, reserved names, Unicode, byte limits), timestamp formatting, the exact
output format, noise cleaning, track selection, provider fallback and error messages,
completeness warnings, atomic writes, duplicates, resume, playlist resilience,
long transcripts (5,000 segments, none lost), ZIP contents, and the HTTP API end to
end. YouTube and Whisper are replaced with fakes.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "YouTube is temporarily blocking caption downloads from this machine" | YouTube blocked your IP for caption downloads, usually after many requests in a short time. Cloud server IPs are often blocked permanently. The app stops sending caption requests for 15 minutes (`CAPTION_BLOCK_COOLDOWN_SECONDS`) instead of making the block worse. The transcript-panel fallback normally avoids this; you only see the message if the panel is blocked too. Choose one: wait a few hours and re-run (finished videos are skipped); tick **Use Whisper if YouTube blocks captions** (audio download usually still works, but it's slower); or set `PROXY_URL`. |
| "Captions are disabled… Automatic transcription is not enabled" | Turn on `ENABLE_WHISPER_FALLBACK=true`. |
| "The Whisper fallback is not installed" | Run `pip install -r requirements.txt` (faster-whisper). |
| `TypeError: open() got an unexpected keyword argument 'metadata_errors'` | PyAV 19 is incompatible with faster-whisper 1.2. `requirements.txt` pins `av<19`, so reinstall. |
| Whisper fallback fails while downloading audio | yt-dlp needs a JS runtime: install **Deno** (or have Node.js on `PATH`). Run `pip install -U "yt-dlp[default]"`, since YouTube changes often. |
| "FFmpeg is required…" | Install FFmpeg ([see above](#installing-ffmpeg)). |
| First Whisper run is slow | The model is downloading. Later runs use the cache. Use `WHISPER_MODEL=base` or `tiny` for speed, or `WHISPER_DEVICE=cuda` with a GPU. |
| UI says "Backend not reachable" | Start the backend on port 8000, or check `docker compose logs backend`. |
| Age-restricted or members-only videos fail | These need a signed-in account, which this tool deliberately doesn't use. |

## Limitations

- **Transcript quality is the source's quality.** Auto-generated captions and Whisper output contain recognition errors. The app does not correct them, by design.
- **YouTube rate limits and IP blocks** are the main operational limit, especially from cloud servers. Use a proxy or residential IP for large batches.
- **Private, age-restricted, members-only and premiere videos** cannot be processed without signing in.
- **Whisper on CPU is slow**: roughly real time to 5× real time for `small`. Long videos without captions take a while.
- **Job state is in memory.** Restarting the server forgets jobs and download links, but the transcript files on disk stay, and resume still works.
- **No authentication.** This is meant to run locally. Don't expose it to the internet as-is.
- Changes on YouTube's side can break yt-dlp or youtube-transcript-api until they're updated (`pip install -U yt-dlp youtube-transcript-api`).

## Future improvements

The structure leaves room for these without major restructuring:

- **Export formats**: SRT, VTT, JSON and Markdown are a new formatter over the same `TranscriptSegment` list.
- **Transcript preview, search, editing**: a read endpoint plus UI, built on the `StorageService` file index.
- **More languages**: the language list in `ExtractForm.tsx`. The backend already accepts any ISO code.
- **Persistent jobs / workers**: Redis-backed `Job` state plus Celery or RQ behind `JobManager.submit`.
- **Transcript database, user accounts, cloud deployment**
- **Optional LLM features** (summaries, notes, chapters, Q&A, embeddings and RAG over a playlist): a separate step over the saved transcripts. Extraction stays LLM-free.
