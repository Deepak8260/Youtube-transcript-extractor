// Mirrors backend/app/models/schemas.py

export type SourceKind = "video" | "playlist";
export type JobStatus = "queued" | "processing" | "completed" | "partial" | "failed";
export type Stage =
  | "queued"
  | "detecting_source"
  | "fetching_info"
  | "extracting"
  | "whisper_download"
  | "whisper_transcribe"
  | "saving"
  | "completed"
  | "partial"
  | "failed";
export type ItemStatus =
  | "pending"
  | "processing"
  | "success"
  | "existing"
  | "unavailable"
  | "no_transcript"
  | "failed";

export interface FileInfo {
  file_id: string;
  filename: string;
  title: string;
  transcript_source: string | null;
  duration: string | null;
  size_bytes: number;
}

export interface JobItem {
  index: number;
  video_id: string;
  title: string;
  status: ItemStatus;
  stage_message: string | null;
  transcript_source: string | null;
  duration: string | null;
  file: FileInfo | null;
  message: string | null;
  error_code: string | null;
  warnings: string[];
}

export interface JobSummary {
  total: number;
  succeeded: number;
  existing: number;
  unavailable: number;
  no_transcript: number;
  failed: number;
  pending: number;
}

export interface JobStatusResponse {
  job_id: string;
  type: SourceKind;
  status: JobStatus;
  stage: Stage;
  stage_message: string;
  progress: number;
  current_video: string | null;
  current_index: number | null;
  total: number;
  playlist_title: string | null;
  items: JobItem[];
  files: FileInfo[];
  summary: JobSummary;
  error: string | null;
  error_code: string | null;
  warnings: string[];
  zip_available: boolean;
  created_at: string;
  finished_at: string | null;
}

export interface ExtractResponse {
  job_id: string;
  type: SourceKind;
  status: JobStatus;
}

export interface ExtractOptions {
  language: string;
  overwrite: boolean;
  removeNonSpeech: boolean;
  whisperOnBlock: boolean;
}

export interface HealthResponse {
  status: string;
  whisper_available: boolean;
  whisper_model: string;
  ffmpeg_available: boolean;
  default_language: string;
}
