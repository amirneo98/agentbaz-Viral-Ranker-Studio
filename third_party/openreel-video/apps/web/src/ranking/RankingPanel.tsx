/**
 * Ranking panel — the "Ranking" mode in the editor sidebar.
 *
 * Additive surface: ingest clips (URL via the backend stream-info proxy, or
 * local drag-drop), order them (#1..N, drag to reorder), author the rank text
 * overlays on the timeline, toggle the engine-rendered rank rail and hand the
 * whole thing to the server renderer.
 */
import React, { useCallback, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Download,
  Film,
  GripVertical,
  Loader2,
  Play,
  Plus,
  Trash2,
  Trophy,
  Upload,
} from "@/icons/lucide-compat";
import {
  RANK_ANIMATION_PRESETS,
  formatRankBadge,
  type TextAnimationPreset,
} from "@openreel/core";
import { ToolcraftButton as Button } from "@openreel/ui";
import { toast } from "../stores/notification-store";
import { useRankingStore } from "./ranking-store";
import { ServerRenderModal } from "./ServerRenderModal";
import { useRankingFontReady } from "./use-ranking-font";

const ANIMATION_LABELS: Record<string, string> = {
  pop: "Pop (default)",
  rise: "Rise (default)",
  drop: "Drop",
  elastic: "Elastic",
  "zoom-blur": "Zoom Blur",
  "slide-up": "Slide Up",
  "slide-down": "Slide Down",
  scale: "Scale",
  bounce: "Bounce",
  cascade: "Cascade",
  wave: "Wave",
  flip: "Flip",
  glitch: "Glitch",
  rotate: "Rotate",
  swing: "Swing",
  fade: "Fade",
  typewriter: "Typewriter",
  "word-by-word": "Word by Word",
  blur: "Blur",
  shake: "Shake",
  split: "Split",
  rainbow: "Rainbow",
};

const FRAME = "rounded-lg border border-border bg-bg-2";
const INPUT =
  "w-full rounded-md border border-border bg-bg px-2.5 py-2 text-[12px] text-fg outline-none focus:border-accent";

export const RankingPanel: React.FC = () => {
  const [url, setUrl] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null);
  const [renderModalOpen, setRenderModalOpen] = useState(false);
  const [busyEntryId, setBusyEntryId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const entries = useRankingStore((state) => state.entries);
  const masterTitle = useRankingStore((state) => state.masterTitle);
  const railVisible = useRankingStore((state) => state.railVisible);
  const ingest = useRankingStore((state) => state.ingest);
  const setMasterTitle = useRankingStore((state) => state.setMasterTitle);
  const setRailVisible = useRankingStore((state) => state.setRailVisible);
  const ingestUrl = useRankingStore((state) => state.ingestUrl);
  const addLocalFiles = useRankingStore((state) => state.addLocalFiles);
  const removeEntry = useRankingStore((state) => state.removeEntry);
  const moveEntry = useRankingStore((state) => state.moveEntry);
  const updateEntry = useRankingStore((state) => state.updateEntry);
  const addEntryToTimeline = useRankingStore((state) => state.addEntryToTimeline);
  const addAllToTimeline = useRankingStore((state) => state.addAllToTimeline);
  const applyMasterTitleToTimeline = useRankingStore(
    (state) => state.applyMasterTitleToTimeline,
  );
  const fontReady = useRankingFontReady();

  const placedCount = useMemo(
    () => entries.filter((entry) => entry.onTimeline).length,
    [entries],
  );

  const handleUrlFetch = useCallback(async () => {
    const result = await ingestUrl(url);
    if (result.ok) {
      setUrl("");
      toast.success(result.message);
    } else {
      toast.error(result.message);
    }
  }, [ingestUrl, url]);

  const handleFiles = useCallback(
    async (files: FileList | File[] | null) => {
      if (!files || !files.length) return;
      const result = await addLocalFiles(files);
      if (result.ok) toast.success(result.message);
      else toast.error(result.message);
    },
    [addLocalFiles],
  );

  const handleAddToTimeline = useCallback(
    async (entryId: string) => {
      setBusyEntryId(entryId);
      try {
        const result = await addEntryToTimeline(entryId);
        if (result.ok) toast.success(result.message);
        else toast.error(result.message);
      } finally {
        setBusyEntryId(null);
      }
    },
    [addEntryToTimeline],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragOver(false);
      void handleFiles(event.dataTransfer?.files ?? null);
    },
    [handleFiles],
  );

  return (
    <div
      className="flex min-h-0 flex-1 flex-col border-t border-border/70"
      data-testid="ranking-panel"
    >
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain custom-scrollbar px-4 py-3 space-y-3">
        {/* ── URL ingestion ─────────────────────────────────────── */}
        <section className={`${FRAME} p-3`}>
          <div className="mb-2 flex items-center gap-2">
            <Trophy size={14} className="text-accent" />
            <span className="text-[12px] font-semibold text-fg">Source clips</span>
          </div>
          <div className="flex gap-2">
            <input
              aria-label="Video URL"
              data-testid="ranking-url-input"
              className={INPUT}
              placeholder="https://youtube.com/watch?v=…"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleUrlFetch();
              }}
            />
            <Button
              label="Fetch 720p"
              variant="primary"
              isDisabled={ingest.phase === "loading" || !url.trim()}
              onClick={() => void handleUrlFetch()}
              className="shrink-0 rounded-md bg-accent px-3 py-2 text-[12px] font-semibold text-black disabled:opacity-50"
            >
              {ingest.phase === "loading" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Download size={13} />
              )}
              Fetch
            </Button>
          </div>

          <div
            data-testid="ranking-dropzone"
            onDrop={handleDrop}
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onClick={() => fileInputRef.current?.click()}
            className={`mt-2 flex cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed px-3 py-3 text-[11px] transition-colors ${
              dragOver
                ? "border-accent bg-accent-soft text-fg"
                : "border-border text-fg-muted hover:border-accent/60"
            }`}
          >
            <Upload size={13} />
            Drop local video files here, or click to browse
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,image/*,audio/*"
            multiple
            className="hidden"
            aria-label="Import local clips for ranking"
            onChange={(event) => {
              void handleFiles(event.target.files);
              event.target.value = "";
            }}
          />

          {ingest.message && (
            <div
              data-testid="ranking-ingest-status"
              className={`mt-2 flex items-start gap-2 rounded-md px-2 py-1.5 text-[11px] ${
                ingest.phase === "error"
                  ? "bg-red-500/10 text-red-400"
                  : ingest.phase === "ready"
                    ? "bg-green-500/10 text-green-400"
                    : "bg-white/5 text-fg-2"
              }`}
            >
              {ingest.phase === "error" && (
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              )}
              {ingest.phase === "loading" && (
                <Loader2 size={12} className="mt-0.5 shrink-0 animate-spin" />
              )}
              <span>{ingest.message}</span>
            </div>
          )}
        </section>

        {/* ── Ranking list ──────────────────────────────────────── */}
        <section className={`${FRAME} p-3`}>
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Film size={14} className="text-accent" />
              <span className="text-[12px] font-semibold text-fg">
                Ranking list ({entries.length})
              </span>
            </div>
            <span className="text-[10px] text-fg-muted">{placedCount} on timeline</span>
          </div>

          <input
            aria-label="Master title"
            data-testid="ranking-master-title"
            className={`${INPUT} mb-2`}
            placeholder="Master title (e.g. Top 5 Goals)"
            value={masterTitle}
            onChange={(event) => setMasterTitle(event.target.value)}
          />
          <div className="mb-2 flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-[11px] text-fg-2">
              <input
                type="checkbox"
                aria-label="Show rank rail"
                checked={railVisible}
                onChange={(event) => setRailVisible(event.target.checked)}
              />
              Rank rail overlay
            </label>
            <Button
              label="Add master title to timeline"
              variant="ghost"
              isDisabled={!placedCount}
              onClick={async () => {
                const result = await applyMasterTitleToTimeline();
                if (result.ok) toast.success(result.message);
                else toast.error(result.message);
              }}
              className="rounded-md border border-border px-2 py-1 text-[10px] text-fg-2 disabled:opacity-40"
            >
              + Master title
            </Button>
          </div>

          {entries.length === 0 ? (
            <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-[11px] text-fg-muted">
              No clips yet. Fetch a URL or drop a file to start the ranking.
            </div>
          ) : (
            <ul className="space-y-2">
              {entries.map((entry, index) => (
                <li
                  key={entry.id}
                  data-testid={`ranking-entry-${index}`}
                  draggable
                  onDragStart={() => setDraggingIndex(index)}
                  onDragEnd={() => setDraggingIndex(null)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (draggingIndex !== null) moveEntry(draggingIndex, index);
                    setDraggingIndex(null);
                  }}
                  className={`rounded-md border p-2 transition-colors ${
                    draggingIndex === index
                      ? "border-accent bg-accent-soft"
                      : "border-border bg-bg-1"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <GripVertical
                      size={13}
                      className="shrink-0 cursor-grab text-fg-muted"
                      aria-hidden="true"
                    />
                    <span className="grid h-6 min-w-[34px] place-items-center rounded bg-[#FFD400] px-1 text-[12px] font-extrabold text-black">
                      {formatRankBadge(entry.rank)}
                    </span>
                    <input
                      aria-label={`Title for rank ${entry.rank}`}
                      className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1.5 py-1 text-[12px] font-semibold text-fg hover:border-border focus:border-accent focus:outline-none"
                      value={entry.title}
                      onChange={(event) =>
                        updateEntry(entry.id, { title: event.target.value })
                      }
                      placeholder="Title"
                    />
                    <button
                      type="button"
                      aria-label={`Remove rank ${entry.rank}`}
                      onClick={() => removeEntry(entry.id)}
                      className="rounded p-1 text-fg-muted hover:bg-red-500/10 hover:text-red-400"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>

                  <div className="mt-2 flex items-center gap-2">
                    <select
                      aria-label={`Animation for rank ${entry.rank}`}
                      className="min-w-0 flex-1 rounded border border-border bg-bg px-1.5 py-1 text-[11px] text-fg-2"
                      value={entry.textAnim}
                      onChange={(event) =>
                        updateEntry(entry.id, {
                          textAnim: event.target.value as TextAnimationPreset,
                        })
                      }
                    >
                      {RANK_ANIMATION_PRESETS.map((preset) => (
                        <option key={preset} value={preset}>
                          {ANIMATION_LABELS[preset] ?? preset}
                        </option>
                      ))}
                    </select>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        aria-label="Move up"
                        disabled={index === 0}
                        onClick={() => moveEntry(index, index - 1)}
                        className="rounded border border-border p-1 text-fg-muted disabled:opacity-30"
                      >
                        <ArrowUp size={11} />
                      </button>
                      <button
                        type="button"
                        aria-label="Move down"
                        disabled={index === entries.length - 1}
                        onClick={() => moveEntry(index, index + 1)}
                        className="rounded border border-border p-1 text-fg-muted disabled:opacity-30"
                      >
                        <ArrowDown size={11} />
                      </button>
                      <Button
                        label={`Add rank ${entry.rank} to timeline`}
                        variant="ghost"
                        isDisabled={busyEntryId === entry.id}
                        onClick={() => void handleAddToTimeline(entry.id)}
                        className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] font-medium text-fg"
                      >
                        {busyEntryId === entry.id ? (
                          <Loader2 size={11} className="animate-spin" />
                        ) : (
                          <Play size={11} />
                        )}
                        {entry.onTimeline ? "Re-add" : "Add to timeline"}
                      </Button>
                    </div>
                  </div>

                  {entry.onTimeline && (
                    <div className="mt-1.5 font-mono text-[10px] text-fg-muted">
                      {entry.start.toFixed(1)}s → {entry.end.toFixed(1)}s · #{entry.rank}{" "}
                      + title overlay
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-2 flex gap-2">
            <Button
              label="Add all to timeline"
              variant="ghost"
              isDisabled={!entries.length}
              onClick={async () => {
                const result = await addAllToTimeline();
                if (result.ok) toast.success(result.message);
                else toast.error(result.message);
              }}
              className="flex flex-1 items-center justify-center gap-1 rounded-md border border-border px-2 py-1.5 text-[11px] text-fg disabled:opacity-40"
            >
              <Plus size={12} /> Add all to timeline
            </Button>
          </div>
        </section>

        {/* ── Export ────────────────────────────────────────────── */}
        <section className={`${FRAME} p-3`}>
          <div className="mb-2 text-[12px] font-semibold text-fg">Export</div>
          <Button
            label="Export to server render"
            variant="primary"
            isDisabled={!entries.length}
            onClick={() => setRenderModalOpen(true)}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-accent px-3 py-2 text-[12px] font-semibold text-black disabled:opacity-50"
          >
            <Download size={13} /> Export → Server Render
          </Button>
          <p className="mt-2 text-[10px] leading-relaxed text-fg-muted">
            Sends the OpenReel project JSON plus the ranking metadata to the
            backend renderer (ffmpeg/NVENC) and polls the task until the MP4 is
            ready. Vazirmatn font:{" "}
            <span className={fontReady ? "text-green-400" : "text-fg-muted"}>
              {fontReady ? "loaded" : "loading…"}
            </span>
          </p>
        </section>
      </div>

      <ServerRenderModal
        isOpen={renderModalOpen}
        onClose={() => setRenderModalOpen(false)}
      />
    </div>
  );
};

export default RankingPanel;
