import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, downloadUrl, fetchTranscript } from "../api";
import type { FileInfo } from "../types";

interface Props {
  file: FileInfo;
  onClose: () => void;
}

interface Entry {
  time: string;
  text: string;
}

const RULE = /^=+$/;
const STAMP = /^\[(\d{2,}:\d{2}:\d{2})\]$/;

/** Split a transcript file into its header fields and timestamped entries. */
function parseTranscript(raw: string): { header: [string, string][]; entries: Entry[] } {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const header: [string, string][] = [];
  let i = 0;
  if (RULE.test(lines[0] ?? "")) {
    for (i = 1; i < lines.length && !RULE.test(lines[i]); i++) {
      const at = lines[i].indexOf(":");
      if (at > 0) header.push([lines[i].slice(0, at).trim(), lines[i].slice(at + 1).trim()]);
    }
    i++;
  }
  const entries: Entry[] = [];
  for (; i < lines.length; i++) {
    const match = lines[i].match(STAMP);
    if (!match) continue;
    const text: string[] = [];
    while (i + 1 < lines.length && lines[i + 1].trim() !== "" && !STAMP.test(lines[i + 1])) {
      text.push(lines[++i]);
    }
    entries.push({ time: match[1], text: text.join(" ") });
  }
  return { header, entries };
}

export function TranscriptViewer({ file, onClose }: Props) {
  const [raw, setRaw] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    setRaw(null);
    setError(null);
    fetchTranscript(file.file_id)
      .then((text) => !cancelled && setRaw(text))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "The transcript could not be loaded."));
    return () => {
      cancelled = true;
    };
  }, [file.file_id]);

  // Modal behaviour: focus inside, Esc closes, background does not scroll,
  // focus returns to the button that opened it.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      opener?.focus();
    };
  }, [onClose]);

  const parsed = useMemo(() => (raw ? parseTranscript(raw) : null), [raw]);
  const needle = query.trim().toLowerCase();
  const visible = parsed && needle ? parsed.entries.filter((e) => e.text.toLowerCase().includes(needle)) : parsed?.entries;
  const meta = parsed?.header.filter(([key]) => key !== "Title" && key !== "Source") ?? [];

  async function copy() {
    if (!raw) return;
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Copying is not allowed in this browser. Use Download instead.");
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="viewer-title">
        <header className="modal-head">
          <div className="modal-title-block">
            <h2 id="viewer-title">{file.title}</h2>
            <p className="modal-sub mono">{file.filename}</p>
          </div>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="Close transcript">
            ✕
          </button>
        </header>

        {meta.length > 0 && (
          <dl className="modal-meta">
            {meta.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}

        <div className="modal-toolbar">
          <input
            className="search-input"
            type="search"
            placeholder="Search transcript"
            aria-label="Search transcript"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={!parsed}
          />
          {parsed && (
            <span className="hint">
              {needle ? `${visible?.length ?? 0} of ${parsed.entries.length}` : `${parsed.entries.length} lines`}
            </span>
          )}
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={copy} disabled={!raw}>
              {copied ? "Copied" : "Copy"}
            </button>
            <a className="btn btn-primary" href={downloadUrl(file.file_id)} download={file.filename}>
              Download
            </a>
          </div>
        </div>

        <div className="modal-body">
          {error && <div className="notice notice-error"><p>{error}</p></div>}
          {!raw && !error && <p className="hint">Loading transcript…</p>}
          {visible && visible.length === 0 && <p className="hint">No lines match “{query}”.</p>}
          {visible && visible.length > 0 && (
            <ol className="transcript">
              {visible.map((entry, i) => (
                <li key={`${entry.time}-${i}`}>
                  <span className="transcript-time mono">{entry.time}</span>
                  <span className="transcript-text">{entry.text}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
