import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getHealth, getJob, startExtraction } from "./api";
import { ExtractForm } from "./components/ExtractForm";
import { JobView } from "./components/JobView";
import { isTerminal } from "./status";
import type { ExtractOptions, HealthResponse, JobStatusResponse } from "./types";

const POLL_MS = 1000;
const MAX_POLL_FAILURES = 5;
const STORAGE_KEY = "yte:lastJob";

function rememberJob(jobId: string | null) {
  try {
    if (jobId) sessionStorage.setItem(STORAGE_KEY, jobId);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable (private mode) — resuming after reload is optional */
  }
}

function recalledJob(): string | null {
  // ?job=<id> reopens a specific job; otherwise resume the last one after a reload.
  const fromUrl = new URLSearchParams(window.location.search).get("job");
  if (fromUrl && /^[A-Za-z0-9_-]{8,64}$/.test(fromUrl)) return fromUrl;
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export default function App() {
  const [jobId, setJobId] = useState<string | null>(recalledJob);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | "loading" | "down">("loading");
  const failures = useRef(0);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth("down"));
  }, []);

  // Poll the job until it reaches a terminal state.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        failures.current = 0;
        setJob(next);
        if (isTerminal(next)) {
          rememberJob(null);
          return;
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          rememberJob(null);
          setJobId(null);
          setJob(null);
          return;
        }
        failures.current += 1;
        if (failures.current >= MAX_POLL_FAILURES) {
          setError(err instanceof ApiError ? err.message : "Lost connection to the server.");
          return;
        }
      }
      timer = window.setTimeout(tick, POLL_MS);
    };

    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  const submit = useCallback(async (url: string, options: ExtractOptions) => {
    setError(null);
    setJob(null);
    setJobId(null);
    setValidating(true);
    try {
      const started = await startExtraction(url, options);
      failures.current = 0;
      rememberJob(started.job_id);
      setJobId(started.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The request could not be sent.");
    } finally {
      setValidating(false);
    }
  }, []);

  const busy = validating || (job !== null && !isTerminal(job)) || (jobId !== null && job === null);

  return (
    <div className="page">
      <header className="header">
        <h1>YouTube Transcript Extractor</h1>
        <p className="subtitle">Extract complete, timestamped transcripts from YouTube videos and playlists.</p>
      </header>

      <main>
        <ExtractForm busy={busy} onSubmit={submit} />

        {validating && (
          <section className="card card-compact">
            <div className="card-head">
              <h2>Processing…</h2>
              <span className="badge badge-validating">Validating URL</span>
            </div>
          </section>
        )}

        {error && (
          <div className="notice notice-error" role="alert">
            <p>{error}</p>
          </div>
        )}

        {job && <JobView job={job} />}
      </main>

      <footer className="footer">
        {health === "loading" ? (
          <span>Connecting…</span>
        ) : health === "down" ? (
          <span>Backend not reachable. Start it with: uvicorn app.main:app</span>
        ) : (
          <span>
            Captions via YouTube · Whisper fallback{" "}
            {health.whisper_available ? `on (${health.whisper_model})` : "off"}
          </span>
        )}
      </footer>
    </div>
  );
}
