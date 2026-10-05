import type { ExtractOptions, ExtractResponse, HealthResponse, JobStatusResponse } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "Cannot reach the transcript server. Make sure the backend is running.",
      "network",
      0,
    );
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      body?.error ?? `The server responded with an error (${response.status}).`,
      body?.code ?? "http_error",
      response.status,
    );
  }
  return body as T;
}

export function startExtraction(url: string, options: ExtractOptions): Promise<ExtractResponse> {
  return request("/api/transcripts/extract", {
    method: "POST",
    body: JSON.stringify({
      url,
      language: options.language,
      overwrite: options.overwrite,
      remove_non_speech: options.removeNonSpeech,
      whisper_on_rate_limit: options.whisperOnBlock,
    }),
  });
}

export function getJob(jobId: string): Promise<JobStatusResponse> {
  return request(`/api/transcripts/jobs/${encodeURIComponent(jobId)}`);
}

export function getHealth(): Promise<HealthResponse> {
  return request("/api/health");
}

export const downloadUrl = (fileId: string) =>
  `${API_BASE}/api/transcripts/download/${encodeURIComponent(fileId)}`;

export const zipUrl = (jobId: string) =>
  `${API_BASE}/api/transcripts/jobs/${encodeURIComponent(jobId)}/download-all`;

export async function fetchTranscript(fileId: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(downloadUrl(fileId));
  } catch {
    throw new ApiError("Cannot reach the transcript server.", "network", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.error ?? "The transcript could not be loaded.", body?.code ?? "http_error", response.status);
  }
  return response.text();
}

/** POST the chosen file ids, then hand the returned ZIP to the browser as a download. */
export async function downloadSelected(fileIds: string[], name?: string | null): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/transcripts/download-zip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_ids: fileIds, name: name ?? undefined }),
    });
  } catch {
    throw new ApiError("Cannot reach the transcript server.", "network", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.error ?? "The ZIP could not be created.", body?.code ?? "http_error", response.status);
  }
  const blob = await response.blob();
  saveBlob(blob, zipFilename(response.headers.get("Content-Disposition")));
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function zipFilename(disposition: string | null): string {
  const utf8 = disposition?.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      /* fall through to the plain filename */
    }
  }
  return disposition?.match(/filename="([^"]+)"/i)?.[1] ?? "transcripts.zip";
}
