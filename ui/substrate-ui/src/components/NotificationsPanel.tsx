"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowLeft, Bell, CalendarCheck, ShieldQuestion } from "lucide-react";
import { Button } from "@/design";
import { api } from "@/lib/api";
import type { AppNotification } from "@/lib/api/notifications";

const ICON = { task_run: CalendarCheck, task_failed: AlertTriangle, approval: ShieldQuestion } as const;
const ICON_TONE = { task_run: "text-success", task_failed: "text-danger", approval: "text-accent-2" } as const;

function when(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** What happened while you were away. Opening one takes you to its conversation and marks it read. */
export function NotificationsPanel({ onBack, onOpenThread, onChanged }: { onBack: () => void; onOpenThread: (threadId: string) => void; onChanged: () => void }) {
  const [items, setItems] = useState<AppNotification[] | null>(null);

  const load = useCallback(async () => setItems((await api.getNotifications()).items), []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
  }, [load]);

  const open = async (n: AppNotification) => {
    if (!n.read_at) {
      await api.markNotificationRead(n.id).catch(() => {});
      onChanged();
    }
    if (n.thread_id) onOpenThread(n.thread_id);
    else void load();
  };

  const readAll = async () => {
    await api.markAllNotificationsRead().catch(() => {});
    onChanged();
    void load();
  };

  const unread = (items ?? []).filter((n) => !n.read_at).length;

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-6 sm:px-6">
      <div className="mb-6 flex items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-foreground">Notifications</h1>
          <p className="text-sm text-muted">Scheduled results, failures and requests that came in while you were away.</p>
        </div>
        {unread > 0 && (
          <Button variant="secondary" onClick={() => void readAll()}>
            Mark all read
          </Button>
        )}
      </div>

      {items === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-12 text-center">
          <Bell className="mx-auto size-6 text-muted" aria-hidden />
          <p className="mt-2 text-sm font-medium text-foreground">You&apos;re all caught up</p>
          <p className="mt-1 text-xs text-muted">Results of scheduled tasks show up here.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {items.map((n) => {
            const Icon = ICON[n.kind];
            return (
              <li key={n.id}>
                <Button variant="ghost" className="h-auto w-full items-start justify-start gap-3 rounded-none px-4 py-3 text-left whitespace-normal" onClick={() => void open(n)}>
                  <Icon className={`mt-0.5 size-4 shrink-0 ${ICON_TONE[n.kind]}`} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className={`block text-sm ${n.read_at ? "text-muted" : "font-semibold text-foreground"}`}>{n.title}</span>
                    {n.body && <span className="mt-0.5 line-clamp-2 block text-xs text-muted">{n.body}</span>}
                  </span>
                  <span className="shrink-0 text-xs text-muted">{when(n.created_at)}</span>
                  {!n.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-accent-2" aria-label="Unread" />}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
