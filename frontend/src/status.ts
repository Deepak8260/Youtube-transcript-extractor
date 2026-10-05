import type { ItemStatus, JobStatusResponse, Stage } from "./types";

/** The ten user-facing states from the spec. */
export type UiState =
  | "idle"
  | "validating"
  | "detecting"
  | "fetching"
  | "extracting"
  | "whisper"
  | "saving"
  | "completed"
  | "partial"
  | "failed";

const STAGE_TO_UI: Record<Stage, UiState> = {
  queued: "detecting",
  detecting_source: "detecting",
  fetching_info: "fetching",
  extracting: "extracting",
  whisper_download: "whisper",
  whisper_transcribe: "whisper",
  saving: "saving",
  completed: "completed",
  partial: "partial",
  failed: "failed",
};

export const UI_LABEL: Record<UiState, string> = {
  idle: "Ready",
  validating: "Validating URL",
  detecting: "Detecting source",
  fetching: "Fetching video information",
  extracting: "Extracting transcript",
  whisper: "Using Whisper fallback",
  saving: "Saving transcript",
  completed: "Completed",
  partial: "Partial success",
  failed: "Failed",
};

export function uiStateForJob(job: JobStatusResponse): UiState {
  if (job.status === "completed") return "completed";
  if (job.status === "partial") return "partial";
  if (job.status === "failed") return "failed";
  return STAGE_TO_UI[job.stage] ?? "extracting";
}

export const isTerminal = (job: JobStatusResponse) =>
  job.status === "completed" || job.status === "partial" || job.status === "failed";

export const ITEM_LABEL: Record<ItemStatus, string> = {
  pending: "Waiting",
  processing: "Processing",
  success: "Extracted",
  existing: "Already extracted",
  unavailable: "Unavailable",
  no_transcript: "Transcript unavailable",
  failed: "Failed",
};

export const ITEM_ICON: Record<ItemStatus, string> = {
  pending: "○",
  processing: "◐",
  success: "✓",
  existing: "✓",
  unavailable: "✕",
  no_transcript: "⚠",
  failed: "✕",
};

const YOUTUBE_URL = /^(https?:\/\/)?([a-z0-9-]+\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)\/\S+$/i;

/** Cheap client-side pre-check; the server does the authoritative validation. */
export function validateUrl(raw: string): string | null {
  const url = raw.trim();
  if (!url) return "Paste a YouTube video or playlist URL.";
  if (!YOUTUBE_URL.test(url)) return "That doesn't look like a YouTube video or playlist URL.";
  return null;
}
