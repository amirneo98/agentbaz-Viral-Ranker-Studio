/**
 * Ranking video support — the "rank-rail" element.
 *
 * A rank rail is the persistent ordered list drawn on the outer margin of a
 * ranking-style video (the "Top 5 …" look): every entry shows its rank badge
 * plus title, the entry currently under the playhead is highlighted, and all
 * others are dimmed. The renderer is intentionally framework-free so it can be
 * reused by the editor preview, the export pipeline and the server renderer.
 */
import type { TextAnimationPreset, TextStyle } from "./types";

export interface RankRailEntry {
  /** Media item id this entry points at (project media library id). */
  readonly mediaId: string;
  /** 1-based rank shown in the badge. */
  readonly rank: number;
  /** Display title for the entry. */
  readonly title: string;
  /** Timeline start (seconds). */
  readonly start: number;
  /** Timeline end (seconds). */
  readonly end: number;
  /** Optional thumbnail url for rich rails; ignored by the flat renderer. */
  readonly thumbnailUrl?: string | null;
}

export interface RankRailModel {
  readonly entries: readonly RankRailEntry[];
  /** Optional video-level title drawn above the rail. */
  readonly masterTitle?: string;
  /** Optional per-entry accent colours (falls back to the render options). */
  readonly activeColor?: string;
  readonly idleColor?: string;
}

export interface RankRailHighlight {
  /** Index of the entry under the playhead, or -1 when none matches. */
  readonly activeIndex: number;
  /** Progress (0..1) of the playhead through the active entry. */
  readonly progress: number;
}

export interface RankRailRenderOptions {
  readonly width: number;
  readonly height: number;
  /** Timeline time used to resolve the active entry. */
  readonly time?: number;
  /** Draw the rail on the right margin and right-align text (fa/he/ar). */
  readonly rtl?: boolean;
  /** Outer padding as a fraction of width. Default 0.035. */
  readonly paddingX?: number;
  /** Vertical offset as a fraction of height. Default 0.14. */
  readonly topOffset?: number;
  readonly fontFamily?: string;
  readonly numberColor?: string;
  readonly titleColor?: string;
  readonly idleTitleColor?: string;
  readonly accentColor?: string;
  readonly strokeColor?: string;
  /** Backing panel behind the rail. */
  readonly showPanel?: boolean;
  /** 0..1 opacity of inactive rows. Default 0.42. */
  readonly idleOpacity?: number;
}

export const RANK_RAIL_DEFAULTS = {
  paddingX: 0.035,
  topOffset: 0.14,
  numberColor: "#FFD400",
  titleColor: "#FFFFFF",
  idleTitleColor: "#D7DAE0",
  accentColor: "#FFD400",
  strokeColor: "#000000",
  showPanel: true,
  idleOpacity: 0.42,
  fontFamily: "Vazirmatn",
} as const;

/**
 * The ranking look used by "Add to timeline": thick black stroke, drop
 * shadow, yellow rank number, white title — the YouTube-shorts style.
 */
export const RANK_NUMBER_TEXT_STYLE: Partial<TextStyle> = {
  fontFamily: RANK_RAIL_DEFAULTS.fontFamily,
  fontSize: 160,
  fontWeight: 900,
  color: RANK_RAIL_DEFAULTS.numberColor,
  strokeColor: RANK_RAIL_DEFAULTS.strokeColor,
  strokeWidth: 14,
  shadowColor: "rgba(0, 0, 0, 0.65)",
  shadowBlur: 18,
  shadowOffsetX: 0,
  shadowOffsetY: 8,
  textAlign: "center",
  verticalAlign: "middle",
  lineHeight: 1.05,
  letterSpacing: -2,
};

export const RANK_TITLE_TEXT_STYLE: Partial<TextStyle> = {
  fontFamily: RANK_RAIL_DEFAULTS.fontFamily,
  fontSize: 64,
  fontWeight: 800,
  color: RANK_RAIL_DEFAULTS.titleColor,
  strokeColor: RANK_RAIL_DEFAULTS.strokeColor,
  strokeWidth: 8,
  shadowColor: "rgba(0, 0, 0, 0.55)",
  shadowBlur: 12,
  shadowOffsetX: 0,
  shadowOffsetY: 5,
  textAlign: "center",
  verticalAlign: "middle",
  lineHeight: 1.1,
  letterSpacing: -0.5,
};

export const RANK_MASTER_TITLE_TEXT_STYLE: Partial<TextStyle> = {
  ...RANK_TITLE_TEXT_STYLE,
  fontSize: 92,
  fontWeight: 900,
  color: RANK_RAIL_DEFAULTS.numberColor,
  strokeWidth: 10,
};

/** Default animation presets for ranking text overlays. */
export const RANK_DEFAULT_NUMBER_ANIMATION: TextAnimationPreset = "pop";
export const RANK_DEFAULT_TITLE_ANIMATION: TextAnimationPreset = "rise";

/** Animation presets offered in the ranking panel (subset of the 26 presets). */
export const RANK_ANIMATION_PRESETS: readonly TextAnimationPreset[] = [
  "pop",
  "rise",
  "drop",
  "elastic",
  "zoom-blur",
  "slide-up",
  "slide-down",
  "scale",
  "bounce",
  "cascade",
  "wave",
  "flip",
  "glitch",
  "rotate",
  "swing",
  "fade",
  "typewriter",
  "word-by-word",
  "blur",
  "shake",
  "split",
  "rainbow",
];

/** "#3" — the badge text for a rank. */
export function formatRankBadge(rank: number): string {
  return `#${rank}`;
}

/**
 * Compose the two-line overlay text for an entry: the rank badge on its own
 * line, the title below it.
 */
export function formatRankOverlayText(rank: number, title: string): string {
  const clean = title.trim();
  return `${formatRankBadge(rank)}\n${clean}`;
}

function normalizeEntries(entries: readonly RankRailEntry[]): RankRailEntry[] {
  return [...entries]
    .map((entry, index) => ({
      ...entry,
      rank: Number.isFinite(entry.rank) && entry.rank > 0 ? entry.rank : index + 1,
      start: Number.isFinite(entry.start) ? entry.start : 0,
      end: Number.isFinite(entry.end) ? entry.end : 0,
    }))
    .sort((a, b) => a.rank - b.rank);
}

/**
 * Which rail entry is under the playhead.
 *
 * Entries are matched by their `[start, end)` window. A zero-length window
 * (entry not yet placed on the timeline) is treated as never active so the
 * rail does not light up spuriously. Returns -1 when nothing matches.
 */
export function findActiveRankIndex(
  entries: readonly RankRailEntry[],
  time: number,
): number {
  if (!entries.length || !Number.isFinite(time)) return -1;
  let fallback = -1;
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const span = entry.end - entry.start;
    if (span <= 0) continue;
    if (time >= entry.start && time < entry.end) return index;
    // Keep the most recent finished entry as a fallback so the rail keeps a
    // sensible highlight during gaps between clips.
    if (time >= entry.end && (fallback < 0 || entry.end > entries[fallback].end)) {
      fallback = index;
    }
  }
  return fallback;
}

export function computeRankRailHighlight(
  entries: readonly RankRailEntry[],
  time: number,
): RankRailHighlight {
  const activeIndex = findActiveRankIndex(entries, time);
  if (activeIndex < 0) return { activeIndex: -1, progress: 0 };
  const entry = entries[activeIndex];
  const span = entry.end - entry.start;
  const progress = span > 0 ? Math.max(0, Math.min(1, (time - entry.start) / span)) : 0;
  return { activeIndex, progress };
}

type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

function roundRect(
  ctx: Ctx2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + w - radius, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
  ctx.lineTo(x + w, y + h - radius);
  ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
  ctx.lineTo(x + radius, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

function drawOutlinedText(
  ctx: Ctx2D,
  text: string,
  x: number,
  y: number,
  strokeColor: string,
  strokeWidth: number,
): void {
  if (strokeWidth > 0) {
    ctx.lineJoin = "round";
    ctx.miterLimit = 2;
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = strokeWidth;
    ctx.strokeText(text, x, y);
  }
  ctx.fillText(text, x, y);
}

/**
 * Draw the rank rail into a 2D context.
 *
 * Layout: a translucent rounded panel pinned to the outer margin (left for
 * LTR, right for RTL) with one row per entry — rank badge, then title. The
 * active row is drawn at full opacity with an accent bar and a slightly
 * larger badge; the others are dimmed.
 */
export function renderRankRail(
  ctx: Ctx2D,
  model: RankRailModel,
  options: RankRailRenderOptions,
): RankRailHighlight {
  const {
    width,
    height,
    time = 0,
    rtl = false,
    paddingX = RANK_RAIL_DEFAULTS.paddingX,
    topOffset = RANK_RAIL_DEFAULTS.topOffset,
    fontFamily = RANK_RAIL_DEFAULTS.fontFamily,
    numberColor = RANK_RAIL_DEFAULTS.numberColor,
    titleColor = RANK_RAIL_DEFAULTS.titleColor,
    idleTitleColor = RANK_RAIL_DEFAULTS.idleTitleColor,
    accentColor = RANK_RAIL_DEFAULTS.accentColor,
    strokeColor = RANK_RAIL_DEFAULTS.strokeColor,
    showPanel = RANK_RAIL_DEFAULTS.showPanel,
    idleOpacity = RANK_RAIL_DEFAULTS.idleOpacity,
  } = options;

  const entries = normalizeEntries(model.entries);
  const highlight = computeRankRailHighlight(entries, time);
  if (!entries.length) return highlight;

  const numberFont = Math.max(14, Math.round(height * 0.038));
  const titleFont = Math.max(11, Math.round(height * 0.021));
  const rowHeight = numberFont * 1.34;
  const totalHeight = rowHeight * entries.length;
  const padX = width * paddingX;
  const panelWidth = Math.min(
    width * 0.44,
    Math.max(width * 0.26, numberFont * 5.6),
  );
  const maxTextWidth = panelWidth - numberFont * 2.1;
  const panelX = rtl ? width - padX - panelWidth : padX;
  const top = height * topOffset;
  const panelHeight = totalHeight + numberFont * 0.9;

  ctx.save();

  if (showPanel) {
    ctx.globalAlpha = 0.42;
    roundRect(
      ctx,
      panelX - numberFont * 0.35,
      top - numberFont * 0.45,
      panelWidth + numberFont * 0.7,
      panelHeight,
      numberFont * 0.42,
    );
    ctx.fillStyle = "rgba(0, 0, 0, 0.92)";
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  if (model.masterTitle && model.masterTitle.trim()) {
    ctx.save();
    ctx.font = `900 ${numberFont * 0.72}px "${fontFamily}", sans-serif`;
    ctx.textAlign = rtl ? "right" : "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = numberColor;
    drawOutlinedText(
      ctx,
      model.masterTitle.trim(),
      rtl ? width - padX : padX,
      top - numberFont * 0.85,
      strokeColor,
      Math.max(2, numberFont * 0.1),
    );
    ctx.restore();
  }

  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const isActive = index === highlight.activeIndex;
    const rowTop = top + index * rowHeight;
    const badge = formatRankBadge(entry.rank);

    ctx.save();
    ctx.globalAlpha = isActive ? 1 : idleOpacity;
    ctx.textBaseline = "middle";

    // Accent bar marks the active row on the outer edge of the rail.
    if (isActive) {
      const barWidth = numberFont * 0.14;
      const barHeight = rowHeight * 0.74;
      const barX = rtl ? panelX + panelWidth + numberFont * 0.12 : panelX - numberFont * 0.45;
      roundRect(ctx, barX, rowTop + (rowHeight - barHeight) / 2, barWidth, barHeight, barWidth / 2);
      ctx.fillStyle = accentColor;
      ctx.fill();
    }

    const numberFontSize = isActive ? numberFont * 1.16 : numberFont;
    const badgeWidth = numberFontSize * 1.85;

    ctx.font = `900 ${numberFontSize}px "${fontFamily}", sans-serif`;
    ctx.textAlign = rtl ? "right" : "left";
    ctx.fillStyle = isActive ? numberColor : idleTitleColor;
    drawOutlinedText(
      ctx,
      badge,
      rtl ? panelX + panelWidth - numberFont * 0.35 : panelX + numberFont * 0.35,
      rowTop + rowHeight / 2,
      strokeColor,
      Math.max(2, numberFontSize * 0.14),
    );

    ctx.font = `800 ${titleFont}px "${fontFamily}", sans-serif`;
    ctx.fillStyle = isActive ? titleColor : idleTitleColor;
    const title = truncateToWidth(ctx, entry.title || "Untitled", maxTextWidth - badgeWidth * 0.35);
    ctx.textAlign = rtl ? "right" : "left";
    drawOutlinedText(
      ctx,
      title,
      rtl
        ? panelX + panelWidth - numberFont * 0.35 - badgeWidth
        : panelX + numberFont * 0.35 + badgeWidth,
      rowTop + rowHeight / 2,
      strokeColor,
      Math.max(1.5, titleFont * 0.16),
    );

    ctx.restore();
  }

  ctx.restore();
  return highlight;
}

function truncateToWidth(ctx: Ctx2D, text: string, maxWidth: number): string {
  if (maxWidth <= 0) return "";
  if (ctx.measureText(text).width <= maxWidth) return text;
  let result = text;
  while (result.length > 1 && ctx.measureText(`${result}…`).width > maxWidth) {
    result = result.slice(0, -1);
  }
  return `${result.trimEnd()}…`;
}

/** Convenience wrapper that allocates an offscreen canvas for the rail. */
export function createRankRailCanvas(
  model: RankRailModel,
  options: RankRailRenderOptions,
): { canvas: HTMLCanvasElement | OffscreenCanvas; highlight: RankRailHighlight } {
  const canvas =
    typeof OffscreenCanvas !== "undefined"
      ? new OffscreenCanvas(options.width, options.height)
      : (() => {
          const el = document.createElement("canvas");
          el.width = options.width;
          el.height = options.height;
          return el;
        })();
  const ctx = canvas.getContext("2d") as Ctx2D | null;
  if (!ctx) return { canvas, highlight: { activeIndex: -1, progress: 0 } };
  const highlight = renderRankRail(ctx, model, options);
  return { canvas, highlight };
}
