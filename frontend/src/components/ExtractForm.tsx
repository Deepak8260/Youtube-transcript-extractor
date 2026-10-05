import { useState, type FormEvent } from "react";
import type { ExtractOptions } from "../types";
import { validateUrl } from "../status";

// English is the default. More caption languages can be listed here; the
// backend accepts any ISO code and falls back to the video's own language.
const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "hi", label: "Hindi" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "ja", label: "Japanese" },
];

interface Props {
  busy: boolean;
  onSubmit: (url: string, options: ExtractOptions) => void;
}

export function ExtractForm({ busy, onSubmit }: Props) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<ExtractOptions>({
    language: "en",
    overwrite: false,
    removeNonSpeech: true,
    whisperOnBlock: false,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const problem = validateUrl(url);
    setError(problem);
    if (!problem) onSubmit(url.trim(), options);
  }

  return (
    <form className="form" onSubmit={submit} noValidate>
      <div className="input-row">
        <input
          className={`url-input${error ? " has-error" : ""}`}
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste YouTube video or playlist URL"
          aria-label="YouTube video or playlist URL"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "url-error" : undefined}
          value={url}
          disabled={busy}
          onChange={(e) => {
            setUrl(e.target.value);
            if (error) setError(null);
          }}
        />
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Extracting…" : "Extract Transcript"}
        </button>
      </div>
      {error && (
        <p className="field-error" id="url-error" role="alert">
          {error}
        </p>
      )}

      <div className="options">
        <label className="option">
          <span>Language</span>
          <select
            value={options.language}
            disabled={busy}
            onChange={(e) => setOptions({ ...options, language: e.target.value })}
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
        <label className="option checkbox">
          <input
            type="checkbox"
            checked={!options.removeNonSpeech}
            disabled={busy}
            onChange={(e) => setOptions({ ...options, removeNonSpeech: !e.target.checked })}
          />
          <span>Keep non-speech cues ([Music], [Applause])</span>
        </label>
        <label className="option checkbox">
          <input
            type="checkbox"
            checked={options.overwrite}
            disabled={busy}
            onChange={(e) => setOptions({ ...options, overwrite: e.target.checked })}
          />
          <span>Overwrite existing transcripts</span>
        </label>
        <label className="option checkbox" title="Slower and less accurate than real captions">
          <input
            type="checkbox"
            checked={options.whisperOnBlock}
            disabled={busy}
            onChange={(e) => setOptions({ ...options, whisperOnBlock: e.target.checked })}
          />
          <span>Use Whisper if YouTube blocks captions</span>
        </label>
      </div>
    </form>
  );
}
