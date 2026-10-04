"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, HelpCircle, ShieldQuestion } from "lucide-react";
import { Badge, Button, Page, PageEmpty } from "@/design";
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
export function ApprovalsPanel({ onOpenThread }: { onOpenThread: (threadId: string) => void }) {
  const [items, setItems] = useState<PendingApproval[] | null>(null);
  const load = useCallback(async () => setItems(await api.getApprovals()), []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <Page title="Approvals" subtitle="Where the assistant is waiting for your go-ahead or your answer." icon={ShieldQuestion}>
      {items === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <PageEmpty icon={CheckCircle2} title="Nothing is waiting on you">
          When the assistant needs permission or an answer, it shows up here.
        </PageEmpty>
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
    </Page>
  );
}
