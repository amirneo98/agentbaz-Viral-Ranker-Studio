/**
 * `rank-rail` element — the persistent, order-aware ranking list drawn over
 * the preview stage.
 *
 * The drawing itself lives in the engine (`renderRankRail` from
 * `@openreel/core`) so the same output is used by the preview, the export
 * pipeline and the server renderer. This component only wires editor state
 * (project canvas size, playhead, ranking entries) into that renderer.
 */
import React, { useEffect, useMemo, useRef } from "react";
import {
  renderRankRail,
  type RankRailEntry,
  type RankRailModel,
} from "@openreel/core";
import { useProjectStore } from "../stores/project-store";
import { useTimelineStore } from "../stores/timeline-store";
import { useRankingStore } from "./ranking-store";

function toRailEntries(
  entries: ReturnType<typeof useRankingStore.getState>["entries"],
): RankRailEntry[] {
  return entries.map((entry) => ({
    mediaId: entry.mediaId,
    rank: entry.rank,
    title: entry.title || entry.mediaName,
    start: entry.start,
    end: entry.end,
  }));
}

export const RankRailOverlay: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const entries = useRankingStore((state) => state.entries);
  const masterTitle = useRankingStore((state) => state.masterTitle);
  const railVisible = useRankingStore((state) => state.railVisible);
  const settings = useProjectStore((state) => state.project.settings);
  const playheadPosition = useTimelineStore((state) => state.playheadPosition);

  const model = useMemo<RankRailModel>(
    () => ({ entries: toRailEntries(entries), masterTitle }),
    [entries, masterTitle],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = Math.max(2, settings.width);
    canvas.height = Math.max(2, settings.height);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!railVisible || model.entries.length === 0) return;

    renderRankRail(ctx, model, {
      width: canvas.width,
      height: canvas.height,
      time: playheadPosition,
    });
  }, [model, playheadPosition, settings.width, settings.height, railVisible]);

  if (!railVisible || entries.length === 0) return null;

  return (
    <canvas
      ref={canvasRef}
      data-openreel-element="rank-rail"
      aria-label="Rank rail"
      className="pointer-events-none absolute inset-0 z-10 h-full w-full"
    />
  );
};

export default RankRailOverlay;
