import { useCallback, useEffect, useState } from "react";
import { ApiError, downloadSelected, downloadUrl, zipUrl } from "../api";
import { UI_LABEL, uiStateForJob } from "../status";
import type { FileInfo, JobStatusResponse } from "../types";
import { ItemList } from "./ItemList";
import { ProgressBar } from "./ProgressBar";
import { TranscriptViewer } from "./TranscriptViewer";

interface Props {
  job: JobStatusResponse;
}

interface ViewProps extends Props {
  onView: (file: FileInfo) => void;
}

export function JobView({ job }: Props) {
  const [viewing, setViewing] = useState<FileInfo | null>(null);
  const close = useCallback(() => setViewing(null), []);
  return (
    <>
      {job.type === "playlist" ? <PlaylistView job={job} onView={setViewing} /> : <VideoView job={job} onView={setViewing} />}
      {viewing && <TranscriptViewer file={viewing} onClose={close} />}
    </>
  );
}

function StatusBadge({ job }: Props) {
  const state = uiStateForJob(job);
  return <span className={`badge badge-${state}`}>{UI_LABEL[state]}</span>;
}

function Warnings({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="notice notice-warning">
      {warnings.map((w) => (
        <p key={w}>{w}</p>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------- single video */

function VideoView({ job, onView }: ViewProps) {
  const item = job.items[0];
  const state = uiStateForJob(job);
  const title = item && item.title !== item.video_id ? item.title : null;

  if (state === "completed" && item?.file) {
    const file = item.file;
    return (
      <section className="card">
        <div className="card-head">
          <h2>
            {item.status === "existing" ? "Transcript already extracted." : "Transcript extracted successfully."}
          </h2>
          <StatusBadge job={job} />
        </div>
        <dl className="details">
          <dt>Title</dt>
          <dd>{item.title}</dd>
          <dt>Transcript</dt>
          <dd>{item.transcript_source ?? "—"}</dd>
          <dt>Duration</dt>
          <dd>{item.duration ?? "—"}</dd>
          <dt>File</dt>
          <dd className="mono">{file.filename}</dd>
        </dl>
        <Warnings warnings={item.warnings} />
        {item.status === "existing" && (
          <p className="hint">Enable “Overwrite existing transcripts” to extract it again.</p>
        )}
        <div className="actions">
          <button className="btn btn-secondary" onClick={() => onView(file)}>
            View Transcript
          </button>
          <a className="btn btn-primary" href={downloadUrl(file.file_id)} download={file.filename}>
            Download Transcript
          </a>
        </div>
      </section>
    );
  }

  if (state === "failed") {
    return (
      <section className="card">
        <div className="card-head">
          <h2>Transcript could not be extracted.</h2>
          <StatusBadge job={job} />
        </div>
        {title && (
          <dl className="details">
            <dt>Title</dt>
            <dd>{title}</dd>
          </dl>
        )}
        <div className="notice notice-error">
          <p>{job.error ?? item?.message ?? "Something went wrong."}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Processing…</h2>
        <StatusBadge job={job} />
      </div>
      <dl className="details">
        <dt>Video</dt>
        <dd>{title ?? "Fetching title…"}</dd>
        <dt>Status</dt>
        <dd>{item?.stage_message ?? job.stage_message}</dd>
      </dl>
      <ProgressBar value={job.progress} label="Extraction progress" />
    </section>
  );
}

/* --------------------------------------------------------------- playlist */

function PlaylistView({ job, onView }: ViewProps) {
  const state = uiStateForJob(job);
  const finished = state === "completed" || state === "partial" || state === "failed";
  const fileIds = job.files.map((f) => f.file_id);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(fileIds));
  const [downloading, setDownloading] = useState(false);
  const [zipError, setZipError] = useState<string | null>(null);

  // Everything starts selected once the job is done; untick what you don't want.
  useEffect(() => {
    if (finished) setSelected(new Set(job.files.map((f) => f.file_id)));
  }, [job.job_id, finished]);

  const chosen = fileIds.filter((id) => selected.has(id));
  const allChosen = fileIds.length > 0 && chosen.length === fileIds.length;
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () => setSelected(allChosen ? new Set() : new Set(fileIds));

  async function downloadChosen() {
    setZipError(null);
    setDownloading(true);
    try {
      const name = chosen.length === fileIds.length ? job.playlist_title : `${job.playlist_title ?? "transcripts"} (${chosen.length} selected)`;
      await downloadSelected(chosen, name);
    } catch (err) {
      setZipError(err instanceof ApiError ? err.message : "The ZIP could not be created.");
    } finally {
      setDownloading(false);
    }
  }
  const s = job.summary;
  const processed = s.total - s.pending;
  const position = job.current_index ?? Math.min(processed + 1, s.total);
  // One reason shared by many videos (e.g. rate limiting) is shown once, not per row.
  const sharedError = job.error ?? commonMessage(job);

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {!finished
            ? "Extracting transcripts"
            : state === "failed"
              ? "Transcript Extraction Failed"
              : "Transcript Extraction Complete"}
        </h2>
        <StatusBadge job={job} />
      </div>

      {job.playlist_title && <p className="playlist-title">{job.playlist_title}</p>}

      {!finished && (
        <>
          <p className="counter">
            {s.total > 0 ? (
              <>
                Processing video <strong>{position}</strong> of <strong>{s.total}</strong>
              </>
            ) : (
              "Fetching playlist videos…"
            )}
          </p>
          <ProgressBar value={job.progress} label="Playlist progress" />
          <dl className="details">
            <dt>Current</dt>
            <dd>{job.current_video ?? "—"}</dd>
            <dt>Status</dt>
            <dd>{job.stage_message}</dd>
          </dl>
        </>
      )}

      {finished && (
        <>
          <p className="counter">
            <strong>
              {processed} / {s.total}
            </strong>{" "}
            videos processed
          </p>
          <ul className="summary">
            <li>
              <span className="dot dot-ok" />
              {s.succeeded} transcripts generated
            </li>
            {s.existing > 0 && (
              <li>
                <span className="dot dot-muted" />
                {s.existing} already extracted (skipped)
              </li>
            )}
            {s.unavailable > 0 && (
              <li>
                <span className="dot dot-error" />
                {s.unavailable} private/unavailable {s.unavailable === 1 ? "video" : "videos"}
              </li>
            )}
            {s.no_transcript > 0 && (
              <li>
                <span className="dot dot-warn" />
                {s.no_transcript} transcript unavailable
              </li>
            )}
            {s.failed > 0 && (
              <li>
                <span className="dot dot-error" />
                {s.failed} failed
              </li>
            )}
          </ul>
          {sharedError && (
            <div className="notice notice-error">
              <p>{sharedError}</p>
            </div>
          )}
        </>
      )}

      <Warnings warnings={job.warnings} />

      {job.zip_available && (
        <div className="selection-bar">
          <label className="option checkbox">
            <input
              type="checkbox"
              checked={allChosen}
              ref={(el) => {
                if (el) el.indeterminate = chosen.length > 0 && !allChosen;
              }}
              onChange={toggleAll}
            />
            <span>{allChosen ? "Deselect all" : "Select all"}</span>
          </label>
          <span className="hint">
            {chosen.length} of {fileIds.length} selected
          </span>
          <div className="selection-actions">
            <button className="btn btn-primary" onClick={downloadChosen} disabled={chosen.length === 0 || downloading}>
              {downloading ? "Preparing ZIP…" : `Download Selected (${chosen.length})`}
            </button>
            <a className="btn btn-secondary" href={zipUrl(job.job_id)}>
              Download All
            </a>
          </div>
        </div>
      )}
      {zipError && (
        <div className="notice notice-error">
          <p>{zipError}</p>
        </div>
      )}

      {job.items.length > 0 && (
        <ItemList
          items={job.items}
          hiddenMessage={finished ? sharedError : null}
          selected={job.zip_available ? selected : undefined}
          onToggle={job.zip_available ? toggle : undefined}
          onView={onView}
        />
      )}
    </section>
  );
}

/** The failure message shared by two or more videos, if there is one. */
function commonMessage(job: JobStatusResponse): string | null {
  const counts = new Map<string, number>();
  for (const item of job.items) {
    if (item.message && (item.status === "failed" || item.status === "no_transcript")) {
      counts.set(item.message, (counts.get(item.message) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let max = 1;
  for (const [message, count] of counts) {
    if (count > max) [best, max] = [message, count];
  }
  return best;
}
