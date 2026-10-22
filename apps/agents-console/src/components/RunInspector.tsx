"use client";

import { useEffect, useState } from "react";
import { Check, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { Button, Dialog, DialogContent, DialogFooter, cn } from "@/design";
import { api } from "@/lib/api";
import type { RunDetail } from "@/lib/api/runs";
import { reportError } from "@/lib/report-error";

const money = (usd: number) => (usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`);
const seconds = (ms: number | null) => (ms == null ? "…" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

/** What the assistant actually did for each answer in this conversation: the model, tokens and cost, which tools it called and whether they worked. Rate an answer here too. */
export function RunInspector({ threadId, open, onClose, ratings, onRate }: {
  threadId: string | null;
  open: boolean;
  onClose: () => void;
  ratings: Record<string, -1 | 1>;
  onRate: (runId: string, value: -1 | 0 | 1) => void;
}) {
  const [runs, setRuns] = useState<RunDetail[] | null>(null);

  useEffect(() => {
    if (!open || !threadId) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset while this conversation's runs load
    setRuns(null);
    api
      .getRuns(threadId)
      .then((r) => !cancelled && setRuns(r))
      .catch((err) => {
        if (!cancelled) setRuns([]);
        reportError("Couldn't load the run details", err);
      });
    return () => {
      cancelled = true;
    };
  }, [open, threadId]);

  const shown = runs ? [...runs].reverse() : [];

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Run details" description="What happened behind each answer, newest first.">
        {runs === null ? (
          <p className="py-6 text-center text-sm text-muted">Loading…</p>
        ) : shown.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">Nothing has run in this conversation yet.</p>
        ) : (
          <ul className="space-y-3">
            {shown.map((run) => {
              const rating = ratings[run.run_id] ?? 0;
              return (
                <li key={run.run_id} className="rounded-xl border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{run.user_message || "(no message)"}</p>
                    <span className="flex items-center gap-1">
                      <Button variant="ghost" size="icon" aria-label="Good answer" aria-pressed={rating === 1} onClick={() => onRate(run.run_id, rating === 1 ? 0 : 1)} className={cn(rating === 1 && "text-success")}>
                        <ThumbsUp />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label="Bad answer" aria-pressed={rating === -1} onClick={() => onRate(run.run_id, rating === -1 ? 0 : -1)} className={cn(rating === -1 && "text-danger")}>
                        <ThumbsDown />
                      </Button>
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    {run.status} · {seconds(run.duration_ms)} · {run.model ?? "no model call"} · {run.tokens.toLocaleString()} tokens · {money(run.cost_usd)}
                    {run.started_at ? ` · ${new Date(run.started_at).toLocaleString()}` : ""}
                  </p>
                  {run.error && <p className="mt-2 rounded-md bg-badge px-2 py-1.5 text-xs text-danger">{run.error}</p>}
                  {run.tools.length > 0 && (
                    <ul className="mt-2 divide-y divide-border rounded-md border border-border">
                      {run.tools.map((t, i) => (
                        <li key={t.call_id || i} className="px-2.5 py-2">
                          <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                            {t.ok === true ? <Check className="size-3.5 text-success" aria-label="worked" /> : t.ok === false ? <X className="size-3.5 text-danger" aria-label="failed" /> : <span className="size-3.5 text-center text-muted" aria-label="no result">?</span>}
                            {t.name}
                            {t.risk && t.risk !== "safe" ? <span className="rounded bg-badge px-1.5 text-xs text-muted">{t.risk}</span> : null}
                          </p>
                          {t.args && t.args !== "{}" && <p className="mt-0.5 truncate font-mono text-xs text-muted">{t.args}</p>}
                          {t.output && <p className="mt-0.5 line-clamp-3 text-xs text-muted">{t.output}</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <DialogFooter>
          <Button onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
