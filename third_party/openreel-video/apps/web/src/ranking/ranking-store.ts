/**
 * Ranking workflow state.
 *
 * Owns the ordered rank list, URL ingestion, timeline placement (video clip +
 * the two overlay text clips styled with the ranking look) and the
 * server-render job that ships {project, ranking} to the backend.
 */
import { create } from "zustand";
import { v4 as uuidv4 } from "uuid";
import {
  RANK_DEFAULT_NUMBER_ANIMATION,
  RANK_DEFAULT_TITLE_ANIMATION,
  RANK_MASTER_TITLE_TEXT_STYLE,
  RANK_NUMBER_TEXT_STYLE,
  RANK_TITLE_TEXT_STYLE,
  createProjectSerializer,
  createStorageEngine,
  type MediaItem,
  type ProjectFile,
} from "@openreel/core";
import { useProjectStore } from "../stores/project-store";
import { useTimelineStore } from "../stores/timeline-store";
import { insertTimelineOverlay } from "../stores/project/insert-timeline-overlay";
import {
  BACKEND_OPEN_HINT,
  absoluteMediaUrl,
  fetchStreamAsFile,
  fetchStreamInfo,
  pollTaskUntilSettled,
  submitOpenReelRender,
} from "./ranking-api";
import type {
  IngestStatus,
  RankingClipEntry,
  RankingRenderClip,
  RankingRenderRequest,
  RenderJobState,
} from "./types";

export const DEFAULT_RANK_DURATION = 5;

/** Rank overlay geometry (normalized canvas coords). */
export const RANK_NUMBER_POSITION = { x: 0.5, y: 0.34 };
export const RANK_TITLE_POSITION = { x: 0.5, y: 0.74 };

export interface RankingState {
  entries: RankingClipEntry[];
  masterTitle: string;
  railVisible: boolean;
  ingest: IngestStatus;
  renderJob: RenderJobState;
  lastImportedMediaId: string | null;

  setIngest: (status: IngestStatus) => void;
  setMasterTitle: (title: string) => void;
  setRailVisible: (visible: boolean) => void;
  clearEntries: () => void;

  ingestUrl: (url: string) => Promise<{ ok: boolean; message: string }>;
  addLocalFiles: (
    files: FileList | File[],
  ) => Promise<{ ok: boolean; message: string }>;
  addEntryForMedia: (
    mediaId: string,
    options?: { title?: string; sourceUrl?: string; mediaName?: string },
  ) => RankingClipEntry | null;
  removeEntry: (entryId: string) => void;
  moveEntry: (fromIndex: number, toIndex: number) => void;
  updateEntry: (
    entryId: string,
    patch: Partial<Pick<RankingClipEntry, "title" | "textAnim" | "start" | "end">>,
  ) => void;
  addEntryToTimeline: (
    entryId: string,
  ) => Promise<{ ok: boolean; message: string }>;
  addAllToTimeline: () => Promise<{ ok: boolean; message: string }>;
  buildRenderPayload: () => { payload?: RankingRenderRequest; error?: string };
  startServerRender: () => Promise<{ ok: boolean; message: string }>;
  resetRenderJob: () => void;
  applyMasterTitleToTimeline: () => Promise<{ ok: boolean; message: string }>;
}

const IDLE_RENDER_JOB: RenderJobState = {
  phase: "idle",
  taskId: null,
  status: "",
  progress: 0,
  message: "",
  outputUrl: null,
  encoder: null,
};

/** Re-number entries after a reorder / removal. */
function withRanks(entries: RankingClipEntry[]): RankingClipEntry[] {
  return entries.map((entry, index) => ({ ...entry, rank: index + 1 }));
}

function mediaFor(mediaId: string): MediaItem | undefined {
  return useProjectStore.getState().getMediaItem(mediaId);
}

function clipDuration(mediaId: string): number {
  const media = mediaFor(mediaId);
  const duration = media?.metadata?.duration ?? 0;
  return duration > 0.25 ? duration : DEFAULT_RANK_DURATION;
}

/** Sequential placement: each new clip lands after the previous ones. */
function nextStartTime(entries: RankingClipEntry[], fallback: number): number {
  const placed = entries.filter((entry) => entry.onTimeline && entry.end > 0);
  if (!placed.length) return Math.max(0, fallback);
  return placed.reduce((max, entry) => Math.max(max, entry.end), 0);
}

/** Set `originalUrl`/`sourceFile` metadata on a freshly imported media item. */
function tagImportedMedia(
  mediaId: string,
  metadata: { originalUrl?: string; mediaName?: string; file?: File },
): void {
  const store = useProjectStore.getState();
  const project = store.project;
  const items = project.mediaLibrary.items.map((item) =>
    item.id === mediaId
      ? {
          ...item,
          originalUrl: metadata.originalUrl ?? item.originalUrl,
          sourceFile: metadata.file
            ? {
                name: metadata.file.name,
                size: metadata.file.size,
                lastModified: metadata.file.lastModified,
              }
            : item.sourceFile,
        }
      : item,
  );
  useProjectStore.setState({
    project: { ...project, mediaLibrary: { items }, modifiedAt: Date.now() },
  });
}

function newClipIdsForMedia(mediaId: string, knownIds: Set<string>): string | null {
  const project = useProjectStore.getState().project;
  for (const track of project.timeline.tracks) {
    for (const clip of track.clips) {
      if (clip.mediaId === mediaId && !knownIds.has(clip.id)) return clip.id;
    }
  }
  return null;
}

function collectClipIds(): Set<string> {
  const ids = new Set<string>();
  for (const track of useProjectStore.getState().project.timeline.tracks) {
    for (const clip of track.clips) ids.add(clip.id);
  }
  return ids;
}

/** Compute a "16:9" style aspect label from the project canvas. */
export function projectAspect(): string {
  const { width, height } = useProjectStore.getState().project.settings;
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const divisor = gcd(width, height) || 1;
  return `${Math.round(width / divisor)}:${Math.round(height / divisor)}`;
}

export const useRankingStore = create<RankingState>((set, get) => ({
  entries: [],
  masterTitle: "",
  railVisible: true,
  ingest: { phase: "idle", message: "" },
  renderJob: { ...IDLE_RENDER_JOB },
  lastImportedMediaId: null,

  setIngest: (status) => set({ ingest: status }),
  setMasterTitle: (title) => set({ masterTitle: title }),
  setRailVisible: (visible) => set({ railVisible: visible }),
  clearEntries: () =>
    set((state) => ({
      entries: [],
      lastImportedMediaId: null,
      ingest: { phase: "idle", message: "" },
      renderJob: { ...state.renderJob },
    })),

  addEntryForMedia: (mediaId, options) => {
    const media = mediaFor(mediaId);
    const entries = get().entries;
    const entry: RankingClipEntry = {
      id: `rank-${uuidv4()}`,
      rank: entries.length + 1,
      mediaId,
      mediaName: options?.mediaName ?? media?.name ?? "clip",
      title: options?.title ?? media?.name?.replace(/\.[^.]+$/, "") ?? "",
      start: 0,
      end: 0,
      textAnim: RANK_DEFAULT_TITLE_ANIMATION,
      textStyle: { ...RANK_TITLE_TEXT_STYLE },
      sourceUrl: options?.sourceUrl,
      numberTextClipId: null,
      titleTextClipId: null,
      mediaClipId: null,
      onTimeline: false,
    };
    set({ entries: withRanks([...entries, entry]), lastImportedMediaId: mediaId });
    return entry;
  },

  /**
   * URL ingestion: POST /api/stream-info/ then pull the 720p proxy into the
   * media library through the editor's native import path.
   */
  ingestUrl: async (url) => {
    const trimmed = url.trim();
    if (!trimmed) {
      set({ ingest: { phase: "error", message: "Paste a video URL first." } });
      return { ok: false, message: "Paste a video URL first." };
    }
    set({ ingest: { phase: "loading", message: "Resolving stream…" } });

    const info = await fetchStreamInfo(trimmed);
    if (!info.ok || !info.data) {
      const message = info.error || "Could not resolve this URL.";
      set({ ingest: { phase: "error", message } });
      return { ok: false, message };
    }

    const streamUrl = absoluteMediaUrl(info.data.stream_url);
    if (!streamUrl || info.data.stream_type === "none") {
      const message = `No direct stream for this URL. ${BACKEND_OPEN_HINT}`;
      set({ ingest: { phase: "error", message } });
      return { ok: false, message };
    }

    set({ ingest: { phase: "loading", message: "Downloading 720p proxy…" } });
    const rawName = info.data.title?.trim() || `ranking-${Date.now()}`;
    const safeName = rawName.replace(/[\\/:*?"<>|]+/g, " ").slice(0, 80).trim();
    const { file, error } = await fetchStreamAsFile(streamUrl, safeName);
    if (!file) {
      const message = error || `Could not download the stream. ${BACKEND_OPEN_HINT}`;
      set({ ingest: { phase: "error", message } });
      return { ok: false, message };
    }

    set({ ingest: { phase: "loading", message: "Importing into media library…" } });
    const imported = await useProjectStore.getState().importMedia(file);
    if (!imported.success || !imported.actionId) {
      const message = imported.error?.message || "Media import failed.";
      set({ ingest: { phase: "error", message } });
      return { ok: false, message };
    }

    const mediaId = imported.actionId;
    tagImportedMedia(mediaId, {
      originalUrl: trimmed,
      mediaName: info.data.title,
      file,
    });
    get().addEntryForMedia(mediaId, {
      title: info.data.title || safeName,
      sourceUrl: trimmed,
      mediaName: info.data.title || safeName,
    });
    const message = `Imported "${info.data.title || safeName}" as a 720p proxy.`;
    set({ ingest: { phase: "ready", message } });
    return { ok: true, message };
  },

  /** Local file drag-drop import (mirrors the native Media tab flow). */
  addLocalFiles: async (files) => {
    const list = Array.from(files ?? []);
    if (!list.length) return { ok: false, message: "No files dropped." };
    let imported = 0;
    for (const file of list) {
      set({
        ingest: { phase: "loading", message: `Importing ${file.name}…` },
      });
      const result = await useProjectStore.getState().importMedia(file);
      if (result.success && result.actionId) {
        tagImportedMedia(result.actionId, { file });
        get().addEntryForMedia(result.actionId, { title: file.name.replace(/\.[^.]+$/, "") });
        imported += 1;
      }
    }
    const message = imported
      ? `Added ${imported} clip${imported > 1 ? "s" : ""} to the ranking list.`
      : "No clips could be imported.";
    set({ ingest: { phase: imported ? "ready" : "error", message } });
    return { ok: imported > 0, message };
  },

  removeEntry: (entryId) => {
    const state = get();
    const entry = state.entries.find((item) => item.id === entryId);
    if (entry) {
      const store = useProjectStore.getState();
      for (const clipId of [entry.numberTextClipId, entry.titleTextClipId]) {
        if (clipId) store.deleteTextClip(clipId);
      }
    }
    set({ entries: withRanks(state.entries.filter((item) => item.id !== entryId)) });
  },

  moveEntry: (fromIndex, toIndex) => {
    const entries = [...get().entries];
    if (
      fromIndex === toIndex ||
      fromIndex < 0 ||
      toIndex < 0 ||
      fromIndex >= entries.length ||
      toIndex >= entries.length
    ) {
      return;
    }
    const [moved] = entries.splice(fromIndex, 1);
    entries.splice(toIndex, 0, moved);
    set({ entries: withRanks(entries) });
  },

  updateEntry: (entryId, patch) => {
    set({
      entries: get().entries.map((entry) =>
        entry.id === entryId ? { ...entry, ...patch } : entry,
      ),
    });
  },

  /**
   * Place the clip on the timeline and author its two overlays:
   * yellow `#N` (pop) and the white title (rise), both with the thick-stroke
   * 3D look, plus the title animation the user picked.
   */
  addEntryToTimeline: async (entryId) => {
    const entry = get().entries.find((item) => item.id === entryId);
    if (!entry) return { ok: false, message: "Entry not found." };
    const project = useProjectStore.getState();
    const media = mediaFor(entry.mediaId);
    if (!media) {
      return { ok: false, message: "Media for this entry is missing from the library." };
    }

    const duration = clipDuration(entry.mediaId);
    const start = nextStartTime(get().entries, useTimelineStore.getState().playheadPosition);
    const end = start + duration;

    // 1. Video clip on a fresh track (native placement).
    const before = collectClipIds();
    const placed = await project.addClipToNewTrack(entry.mediaId, start);
    const mediaClipId = placed.success ? newClipIdsForMedia(entry.mediaId, before) : null;

    // 2. Overlay text clips via the native text tooling.
    const numberText = `#${entry.rank}`;
    const numberClip = await insertTimelineOverlay(start, duration, (trackId) =>
      useProjectStore
        .getState()
        .createTextClip(trackId, start, numberText, duration, RANK_NUMBER_TEXT_STYLE),
    );
    let numberTextClipId: string | null = null;
    if (numberClip) {
      numberTextClipId = numberClip.id;
      useProjectStore
        .getState()
        .updateTextTransform(numberClip.id, { position: RANK_NUMBER_POSITION });
      useProjectStore
        .getState()
        .applyTextAnimationPreset(
          numberClip.id,
          RANK_DEFAULT_NUMBER_ANIMATION,
          0.45,
          0.35,
        );
    }

    const titleText = entry.title.trim() || entry.mediaName;
    const titleClip = await insertTimelineOverlay(start, duration, (trackId) =>
      useProjectStore
        .getState()
        .createTextClip(trackId, start, titleText, duration, {
          ...RANK_TITLE_TEXT_STYLE,
          ...entry.textStyle,
        }),
    );
    let titleTextClipId: string | null = null;
    if (titleClip) {
      titleTextClipId = titleClip.id;
      useProjectStore
        .getState()
        .updateTextTransform(titleClip.id, { position: RANK_TITLE_POSITION });
      useProjectStore
        .getState()
        .applyTextAnimationPreset(titleClip.id, entry.textAnim, 0.6, 0.4);
    }

    set({
      entries: get().entries.map((item) =>
        item.id === entryId
          ? {
              ...item,
              start,
              end,
              mediaClipId,
              numberTextClipId,
              titleTextClipId,
              onTimeline: Boolean(numberTextClipId || titleTextClipId || mediaClipId),
            }
          : item,
      ),
    });

    const ok = Boolean(mediaClipId || numberTextClipId || titleTextClipId);
    return {
      ok,
      message: ok
        ? `#${entry.rank} placed at ${start.toFixed(1)}s on the timeline.`
        : "Could not place this entry on the timeline.",
    };
  },

  addAllToTimeline: async () => {
    const entries = [...get().entries];
    let placed = 0;
    for (const entry of entries) {
      const result = await get().addEntryToTimeline(entry.id);
      if (result.ok) placed += 1;
    }
    return {
      ok: placed > 0,
      message: placed
        ? `Placed ${placed}/${entries.length} entries on the timeline.`
        : "Nothing was placed on the timeline.",
    };
  },

  /** Project JSON (project-serializer) + ranking metadata. */
  buildRenderPayload: () => {
    const entries = get().entries;
    if (!entries.length) {
      return { error: "Add at least one clip to the ranking list first." };
    }
    const project = useProjectStore.getState().getFullProject();
    const serializer = createProjectSerializer(createStorageEngine());
    let projectFile: ProjectFile;
    try {
      projectFile = JSON.parse(serializer.exportToJson(project)) as ProjectFile;
    } catch {
      return { error: "Could not serialize the current project." };
    }
    const clips: RankingRenderClip[] = entries.map((entry) => ({
      mediaId: entry.mediaId,
      rank: entry.rank,
      title: entry.title,
      start: entry.start,
      end: entry.end,
      textAnim: entry.textAnim,
      textStyle: entry.textStyle,
    }));
    return {
      payload: {
        project: projectFile,
        ranking: {
          clips,
          master_title: get().masterTitle,
          aspect: projectAspect(),
        },
      },
    };
  },

  startServerRender: async () => {
    const { payload, error } = get().buildRenderPayload();
    if (!payload) {
      set({
        renderJob: {
          ...IDLE_RENDER_JOB,
          phase: "error",
          message: error || "Nothing to render.",
        },
      });
      return { ok: false, message: error || "Nothing to render." };
    }

    set({
      renderJob: {
        ...IDLE_RENDER_JOB,
        phase: "submitting",
        message: "Uploading project to the render server…",
      },
    });

    const submitted = await submitOpenReelRender(payload);
    const taskId = submitted.data?.task_id ?? submitted.data?.id ?? null;
    if (!submitted.ok || !taskId) {
      const message =
        submitted.status === 404
          ? "Server render endpoint /api/render/openreel/ is not available yet."
          : submitted.error || "Render submission failed.";
      set({
        renderJob: { ...IDLE_RENDER_JOB, phase: "error", message },
      });
      return { ok: false, message };
    }

    set({
      renderJob: {
        ...IDLE_RENDER_JOB,
        phase: "polling",
        taskId,
        status: "PENDING",
        message: "Render queued…",
      },
    });

    const finished = await pollTaskUntilSettled(taskId, (task) => {
      set({
        renderJob: {
          phase: "polling",
          taskId,
          status: task.status,
          progress: Number.isFinite(task.progress) ? task.progress : 0,
          message: `Server render: ${task.status.toLowerCase()}…`,
          outputUrl: null,
          encoder: null,
        },
      });
    });

    if (finished.ok && finished.data) {
      const outputUrl = absoluteMediaUrl(
        finished.data.result?.download_url ?? finished.data.result?.preview_url ?? null,
      );
      set({
        renderJob: {
          phase: "done",
          taskId,
          status: "SUCCESS",
          progress: 1,
          message: "Server render finished.",
          outputUrl,
          encoder:
            typeof finished.data.result?.encoder_used === "string"
              ? finished.data.result.encoder_used
              : null,
        },
      });
      return { ok: true, message: "Server render finished." };
    }

    const message = finished.error || finished.data?.error || "Server render failed.";
    set({
      renderJob: {
        phase: "error",
        taskId,
        status: finished.data?.status || "FAILED",
        progress: finished.data?.progress ?? 0,
        message,
        outputUrl: null,
        encoder: null,
      },
    });
    return { ok: false, message };
  },

  resetRenderJob: () => set({ renderJob: { ...IDLE_RENDER_JOB } }),

  /** Master title overlay across the whole ranking video. */
  applyMasterTitleToTimeline: async () => {
    const title = get().masterTitle.trim();
    if (!title) return { ok: false, message: "Set a master title first." };
    const entries = get().entries.filter((entry) => entry.end > 0);
    if (!entries.length) {
      return { ok: false, message: "Place at least one entry on the timeline first." };
    }
    const start = Math.min(...entries.map((entry) => entry.start));
    const end = Math.max(...entries.map((entry) => entry.end));
    const clip = await insertTimelineOverlay(start, end - start, (trackId) =>
      useProjectStore
        .getState()
        .createTextClip(trackId, start, title, end - start, RANK_MASTER_TITLE_TEXT_STYLE),
    );
    if (!clip) return { ok: false, message: "Could not create the master title." };
    useProjectStore.getState().updateTextTransform(clip.id, { position: { x: 0.5, y: 0.12 } });
    useProjectStore.getState().applyTextAnimationPreset(clip.id, "drop", 0.6, 0.4);
    return { ok: true, message: "Master title added to the timeline." };
  },
}));

/** Convenience selector used by the panel + rail. */
export function rankingRailEntries(state: RankingState) {
  return state.entries.map((entry) => ({
    mediaId: entry.mediaId,
    rank: entry.rank,
    title: entry.title || entry.mediaName,
    start: entry.start,
    end: entry.end,
  }));
}
