"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, HelpCircle, ShieldQuestion } from "lucide-react";
import { Badge, Button } from "@/design";
import { api } from "@/lib/api";
import type { PendingApproval } from "@/lib/api/approvals";

function ago(iso: string | null): string {
  if (!iso) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

/** Everything the assistant is waiting on you for, across all conversations. Answering happens in the conversation. */
export function ApprovalsPanel({ onBack, onOpenThread }: { onBack: () => void; onOpenThread: (threadId: string) => void }) {
  const [items, setItems] = useState<PendingApproval[] | null>(null);

  const load = useCallback(async () => setItems(await api.getApprovals()), []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-6 sm:px-6">
      <div className="mb-6 flex items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <div>
          <h1 className="text-lg font-semibold text-foreground">Approvals</h1>
          <p className="text-sm text-muted">Where the assistant is waiting for your go-ahead or your answer.</p>
        </div>
      </div>

      {items === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-12 text-center">
          <CheckCircle2 className="mx-auto size-6 text-success" aria-hidden />
          <p className="mt-2 text-sm font-medium text-foreground">Nothing is waiting on you</p>
          <p className="mt-1 text-xs text-muted">When the assistant needs permission or an answer, it shows up here.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {items.map((item) => (
            <li key={`${item.thread_id}-${item.kind}`} className="flex items-start gap-3 px-4 py-3">
              {item.kind === "approval" ? <ShieldQuestion className="mt-0.5 size-4 shrink-0 text-accent-2" aria-hidden /> : <HelpCircle className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-foreground">{item.thread_name ?? "Conversation"}</p>
                  <Badge tone={item.kind === "approval" ? "warning" : "accent"}>{item.kind === "approval" ? "Needs approval" : "Has a question"}</Badge>
                </div>
                <p className="mt-0.5 text-sm text-muted">{item.summary}</p>
                {item.requested_at && <p className="mt-0.5 text-xs text-muted">{ago(item.requested_at)}</p>}
              </div>
              <Button variant="primary" onClick={() => onOpenThread(item.thread_id)}>
                Review
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
