/**
 * Shared types for the ranking-video workflow.
 *
 * These mirror the backend contract (localhost:8000) and the `ranking`
 * block that `POST /api/render/openreel/` expects alongside a serialized
 * OpenReel project file.
 */
import type { ProjectFile, TextAnimationPreset, TextStyle } from "@openreel/core";

/** One ordered entry in the ranking list. */
export interface RankingClipEntry {
  /** Locally generated id (stable across reorders). */
  readonly id: string;
  /** Rank number, 1-based; kept in sync with list order. */
  rank: number;
  /** Media library item this entry points at. */
  mediaId: string;
  /** Imported media item name (for display when the library item is gone). */
  mediaName: string;
  /** Editable overlay title. */
  title: string;
  /** Timeline start / end in seconds (set when placed on the timeline). */
  start: number;
  end: number;
  /** Animation preset applied to the overlay text. */
  textAnim: TextAnimationPreset;
  /** Text style used for the title overlay. */
  textStyle: Partial<TextStyle>;
  /** Original URL the media came from (URL ingestion only). */
  sourceUrl?: string;
  /** Text clip ids created for this entry, if it was added to the timeline. */
  numberTextClipId?: string | null;
  titleTextClipId?: string | null;
  /** Media clip id placed on the timeline for this entry. */
  mediaClipId?: string | null;
  /** True once the entry is on the timeline. */
  onTimeline: boolean;
}

/** `ranking` block of the server-render payload. */
export interface RankingRenderClip {
  mediaId: string;
  rank: number;
  title: string;
  start: number;
  end: number;
  textAnim: TextAnimationPreset;
  textStyle: Partial<TextStyle>;
}

export interface RankingRenderRequest {
  project: ProjectFile;
  ranking: {
    clips: RankingRenderClip[];
    master_title: string;
    aspect: string;
  };
}

/** POST /api/stream-info/ response. */
export interface StreamInfo {
  title: string;
  duration: number;
  thumbnail: string | null;
  stream_url: string | null;
  stream_type: string;
  audio_url?: string | null;
}

/** GET /api/tasks/<id>/ response. */
export interface RenderTask {
  id: string;
  task_type: string;
  status: "PENDING" | "PROCESSING" | "SUCCESS" | "FAILED" | string;
  progress: number;
  error: string;
  result: {
    download_url?: string;
    preview_url?: string;
    encoder_used?: string;
    [key: string]: unknown;
  } | null;
  created_at?: string;
  updated_at?: string;
}

export type RenderPhase = "idle" | "submitting" | "polling" | "done" | "error";

export interface RenderJobState {
  phase: RenderPhase;
  taskId: string | null;
  status: string;
  progress: number;
  message: string;
  outputUrl: string | null;
  encoder: string | null;
}

export interface IngestStatus {
  phase: "idle" | "loading" | "error" | "ready";
  message: string;
}
