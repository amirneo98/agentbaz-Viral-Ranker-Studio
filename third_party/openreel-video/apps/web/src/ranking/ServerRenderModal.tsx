/**
 * Server-render progress modal for the ranking workflow.
 *
 * Submits `{project, ranking}` to the backend and follows
 * `GET /api/tasks/<id>/` at a 1.5s interval until the job settles.
 */
import React, { useEffect, useState } from "react";
import { ToolcraftButton as Button } from "@openreel/ui";
import { RANKING_API_BASE } from "./ranking-api";
import { useRankingStore } from "./ranking-store";

const STATUS_LABEL: Record<string, string> = {
  idle: "Idle",
  submitting: "Submitting",
  polling: "Rendering",
  done: "Complete",
  error: "Failed",
};

export const ServerRenderModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
}> = ({ isOpen, onClose }) => {
  const renderJob = useRankingStore((state) => state.renderJob);
  const startServerRender = useRankingStore((state) => state.startServerRender);
  const resetRenderJob = useRankingStore((state) => state.resetRenderJob);
  const entries = useRankingStore((state) => state.entries);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isOpen) setCopied(false);
  }, [isOpen]);

  if (!isOpen) return null;

  const percent = Math.round(Math.max(0, Math.min(1, renderJob.progress)) * 100);
  const busy = renderJob.phase === "submitting" || renderJob.phase === "polling";

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 p-4">
      <div
        role="dialog"
        aria-label="Server render"
        data-testid="ranking-server-render-modal"
        className="w-full max-w-[520px] rounded-xl border border-border bg-bg-1 p-5 shadow-2xl"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <div className="text-[16px] font-bold text-fg">Export → Server Render</div>
            <div className="mt-1 text-[12px] text-fg-muted">
              {entries.length} ranked clip{entries.length === 1 ? "" : "s"} ·{" "}
              {RANKING_API_BASE}
            </div>
          </div>
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              renderJob.phase === "error"
                ? "bg-red-500/15 text-red-400"
                : renderJob.phase === "done"
                  ? "bg-green-500/15 text-green-400"
                  : "bg-white/10 text-fg-2"
            }`}
          >
            {STATUS_LABEL[renderJob.phase] ?? renderJob.phase}
          </span>
        </div>

        <div className="mb-2 h-2 w-full overflow-hidden rounded-full bg-bg-3">
          <div
            className="h-full rounded-full bg-accent transition-all duration-300"
            style={{ width: `${renderJob.phase === "done" ? 100 : percent}%` }}
          />
        </div>
        <div className="mb-4 flex items-center justify-between text-[11px] text-fg-muted">
          <span data-testid="ranking-render-message">
            {renderJob.message || "Ready to render on the server."}
          </span>
          <span className="tabular-nums">{renderJob.phase === "done" ? 100 : percent}%</span>
        </div>

        {renderJob.taskId && (
          <div className="mb-3 truncate rounded-md border border-border bg-bg-2 px-3 py-2 font-mono text-[11px] text-fg-muted">
            task {renderJob.taskId}
          </div>
        )}

        {renderJob.encoder && (
          <div className="mb-3 text-[11px] text-fg-muted">encoder: {renderJob.encoder}</div>
        )}

        {renderJob.outputUrl && (
          <div className="mb-3 space-y-2 rounded-md border border-green-500/40 bg-green-500/5 p-3">
            <div className="text-[12px] font-semibold text-green-400">Render ready</div>
            <a
              href={renderJob.outputUrl}
              download
              className="block truncate text-[11px] text-accent underline"
            >
              {renderJob.outputUrl}
            </a>
            <div className="flex gap-2">
              <Button
                label="Download file"
                variant="ghost"
                onClick={() => window.open(renderJob.outputUrl as string, "_blank")}
                className="rounded-md border border-border px-3 py-1.5 text-[12px] text-fg"
              >
                Download file
              </Button>
              <Button
                label="Copy URL"
                variant="ghost"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(renderJob.outputUrl as string);
                    setCopied(true);
                  } catch {
                    setCopied(false);
                  }
                }}
                className="rounded-md border border-border px-3 py-1.5 text-[12px] text-fg"
              >
                {copied ? "Copied" : "Copy URL"}
              </Button>
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button
            label="Close"
            variant="ghost"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-[12px] text-fg-2"
          >
            {busy ? "Hide" : "Close"}
          </Button>
          {renderJob.phase === "error" && (
            <Button
              label="Retry"
              variant="ghost"
              onClick={() => {
                resetRenderJob();
                void startServerRender();
              }}
              className="rounded-md border border-border px-3 py-1.5 text-[12px] text-fg"
            >
              Retry
            </Button>
          )}
          <Button
            label="Start server render"
            variant="primary"
            isDisabled={busy || entries.length === 0}
            onClick={() => {
              resetRenderJob();
              void startServerRender();
            }}
            className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-black disabled:opacity-50"
          >
            {busy ? "Rendering…" : "Start server render"}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default ServerRenderModal;
