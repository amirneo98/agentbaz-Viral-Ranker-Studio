/**
 * Backend bridge for the ranking workflow.
 *
 * Talks to the local Viral Ranker Studio backend (Django, port 8000):
 *   POST /api/stream-info/       {url}      -> StreamInfo (720p proxy url)
 *   POST /api/download-section/  {clip_id}  -> {task_id}
 *   POST /api/render/openreel/   {project, ranking} -> {task_id}
 *   GET  /api/tasks/<task_id>/              -> RenderTask
 */
import type {
  RankingRenderRequest,
  RenderTask,
  StreamInfo,
} from "./types";

const DEFAULT_API_BASE = "http://localhost:8000";

function resolveApiBase(): string {
  const configured =
    typeof import.meta !== "undefined"
      ? (import.meta.env?.VITE_RANKING_API_BASE as string | undefined)
      : undefined;
  return (configured || DEFAULT_API_BASE).replace(/\/+$/, "");
}

export const RANKING_API_BASE = resolveApiBase();

/** Hint shown when the backend cannot hand us a playable stream. */
export const BACKEND_OPEN_HINT = `Open ${RANKING_API_BASE} in your browser to fetch the source first, then retry.`;

export interface ApiResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
  status?: number;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as unknown;
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      for (const key of ["error", "detail", "message"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return value;
      }
    }
  } catch {
    // fall through to the status text
  }
  return `${response.status} ${response.statusText}`.trim();
}

async function postJson<T>(
  path: string,
  body: unknown,
  timeoutMs = 45000,
): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${RANKING_API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, error: await parseError(response), status: response.status };
    }
    const data = (await response.json()) as T;
    return { ok: true, data, status: response.status };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof Error && error.name === "AbortError"
          ? "Request timed out."
          : `Backend unreachable at ${RANKING_API_BASE}`,
    };
  } finally {
    clearTimeout(timer);
  }
}

async function getJson<T>(path: string, timeoutMs = 15000): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${RANKING_API_BASE}${path}`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, error: await parseError(response), status: response.status };
    }
    return { ok: true, data: (await response.json()) as T, status: response.status };
  } catch {
    return { ok: false, error: `Backend unreachable at ${RANKING_API_BASE}` };
  } finally {
    clearTimeout(timer);
  }
}

/** Resolve relative backend paths (`/media/...`) to absolute URLs. */
export function absoluteMediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url) || url.startsWith("blob:") || url.startsWith("data:")) {
    return url;
  }
  return `${RANKING_API_BASE}${url.startsWith("/") ? "" : "/"}${url}`;
}

export function fetchStreamInfo(url: string): Promise<ApiResult<StreamInfo>> {
  return postJson<StreamInfo>("/api/stream-info/", { url }, 60000);
}

/**
 * Create a server-side 720p proxy download for any source URL.
 * Returns the pollable task; the task result carries proxy_url
 * (served by OUR backend, so it is always CORS-safe for the browser).
 */
export function submitProxyDownload(
  url: string,
): Promise<ApiResult<{ task_id: string }>> {
  return postJson<{ task_id: string }>("/api/proxy/", { url }, 60000);
}

interface ProxyTaskResult {
  status: string;
  progress?: number;
  error?: string;
  result?: { proxy_url?: string } | null;
}

/**
 * Resolve any source URL to a browser-playable, CORS-safe media file URL
 * hosted by our backend (720p proxy). Polls the proxy task until it
 * settles and returns the proxy URL on success.
 */
export async function resolvePlayableMedia(
  url: string,
  onProgress?: (message: string) => void,
): Promise<{ mediaUrl?: string; error?: string }> {
  onProgress?.("Preparing playable preview…");
  const task = await submitProxyDownload(url);
  if (!task.ok || !task.data?.task_id) {
    return { error: task.error || "Could not start the proxy download." };
  }
  const taskId = task.data.task_id;
  // Poll up to ~4 minutes (proxy downloads are usually <20s).
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500));
    const resp = await fetch(`${RANKING_API_BASE}/api/tasks/${taskId}/`);
    if (!resp.ok) {
      return { error: `Task polling failed (${resp.status}).` };
    }
    const data = (await resp.json()) as ProxyTaskResult;
    if (data.status === "SUCCESS") {
      const proxyUrl = data.result?.proxy_url;
      if (!proxyUrl) {
        return { error: "Proxy task finished without a media URL." };
      }
      return { mediaUrl: absoluteMediaUrl(proxyUrl) ?? undefined };
    }
    if (data.status === "FAILED") {
      return {
        error: data.error || "The server could not fetch this source.",
      };
    }
    const pct = typeof data.progress === "number"
      ? Math.round(data.progress)
      : undefined;
    onProgress?.(
      typeof pct === "number"
        ? `Fetching preview… ${pct}%`
        : "Fetching preview…",
    );
  }
  return { error: "Proxy download timed out." };
}

/** HD section fetch fallback — reuses the FETCH task polling path. */
export function fetchSectionDownload(
  clipId: string,
): Promise<ApiResult<{ task_id?: string; id?: string }>> {
  return postJson<{ task_id?: string; id?: string }>("/api/download-section/", {
    clip_id: clipId,
  });
}

export function submitOpenReelRender(
  payload: RankingRenderRequest,
): Promise<ApiResult<{ task_id?: string; id?: string }>> {
  return postJson<{ task_id?: string; id?: string }>(
    "/api/render/openreel/",
    payload,
    120000,
  );
}

export function fetchRenderTask(taskId: string): Promise<ApiResult<RenderTask>> {
  return getJson<RenderTask>(`/api/tasks/${taskId}/`);
}

/**
 * Download a remote stream into a File so it can go through the editor's
 * native media import path (blob + metadata + thumbnails).
 */
export async function fetchStreamAsFile(
  streamUrl: string,
  filename: string,
): Promise<{ file?: File; error?: string }> {
  try {
    const response = await fetch(absoluteMediaUrl(streamUrl) as string);
    if (!response.ok) {
      return { error: `Stream download failed (${response.status}). ${BACKEND_OPEN_HINT}` };
    }
    const blob = await response.blob();
    if (!blob.size) {
      return { error: `Stream download returned an empty file. ${BACKEND_OPEN_HINT}` };
    }
    const type = blob.type || "video/mp4";
    const name = /\.[a-z0-9]{2,5}$/i.test(filename)
      ? filename
      : `${filename}.mp4`;
    return { file: new File([blob], name, { type, lastModified: Date.now() }) };
  } catch {
    return {
      error: `Browser could not read the stream (CORS/network). ${BACKEND_OPEN_HINT}`,
    };
  }
}

/** Poll a render/fetch task until it settles. */
export async function pollTaskUntilSettled(
  taskId: string,
  onUpdate: (task: RenderTask) => void,
  intervalMs = 1500,
  maxAttempts = 1200,
): Promise<ApiResult<RenderTask>> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const result = await fetchRenderTask(taskId);
    if (!result.ok || !result.data) {
      return result;
    }
    const task = result.data;
    onUpdate(task);
    if (task.status === "SUCCESS" || task.status === "FAILED") {
      return { ok: task.status === "SUCCESS", data: task, error: task.error || undefined };
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return { ok: false, error: "Render task timed out." };
}
