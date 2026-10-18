"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bell, CalendarCheck, HelpCircle, ShieldQuestion } from "lucide-react";
import { Badge, Button, Page, PageEmpty } from "@/design";
import { api } from "@/lib/api";
import type { AppNotification } from "@/lib/api/notifications";
import type { PendingApproval } from "@/lib/api/approvals";
import type { Group } from "@/lib/api/groups";
import { Avatar } from "@/components/groups/Avatar";

const ICON = { task_run: CalendarCheck, task_failed: AlertTriangle, approval: ShieldQuestion } as const;
const ICON_TONE = { task_run: "text-success", task_failed: "text-danger", approval: "text-accent-2" } as const;

function when(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

/** What needs you and what happened while you were away: the assistant waiting for a go-ahead or an answer, new messages in your groups, and results of scheduled work. Opening one takes you to its conversation and marks it read. */
export function NotificationsPanel({ onOpenThread, onChanged, groups, onOpenGroup }: { onOpenThread: (threadId: string) => void; onChanged: () => void; groups: Group[]; onOpenGroup: (groupId: string) => void }) {
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [waiting, setWaiting] = useState<PendingApproval[]>([]);

  const load = useCallback(async () => setItems((await api.getNotifications()).items), []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
    const loadWaiting = () => api.getApprovals().then(setWaiting).catch(() => {});
    void loadWaiting();
    const timer = setInterval(loadWaiting, 15000);
    return () => clearInterval(timer);
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
  const unreadGroups = groups.filter((g) => g.unread > 0);
  const nothing = items !== null && items.length === 0 && unreadGroups.length === 0 && waiting.length === 0;

  return (
    <Page
      title="Notifications"
      subtitle="What is waiting on you, new messages in your groups, and results that came in while you were away."
      icon={Bell}
      actions={
        unread > 0 ? (
          <Button variant="secondary" onClick={() => void readAll()}>
            Mark all read
          </Button>
        ) : undefined
      }
    >
      {items === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : nothing ? (
        <PageEmpty icon={Bell} title="You're all caught up">
          Approvals, new messages in your groups and results of scheduled tasks show up here.
        </PageEmpty>
      ) : (
        <>
        {waiting.length > 0 && (
          <section className="space-y-2">
            <h2 className="px-1 text-sm font-semibold text-foreground">Waiting on you</h2>
            <ul className="divide-y divide-border rounded-xl border border-border">
              {waiting.map((item) => (
                <li key={`${item.thread_id}-${item.kind}`} className="flex items-start gap-3 px-4 py-3">
                  {item.kind === "approval" ? <ShieldQuestion className="mt-0.5 size-4 shrink-0 text-accent-2" aria-hidden /> : <HelpCircle className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
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
          </section>
        )}
        {unreadGroups.length > 0 && (
          <section className="space-y-2">
            <h2 className="px-1 text-sm font-semibold text-foreground">New in your groups</h2>
            <ul className="divide-y divide-border rounded-xl border border-border">
              {unreadGroups.map((g) => (
                <li key={g.id}>
                  <Button variant="ghost" className="h-auto w-full items-center justify-start gap-3 rounded-none px-4 py-3 text-left whitespace-normal" onClick={() => onOpenGroup(g.id)}>
                    <Avatar name={g.name} src={g.avatar} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-foreground">{g.name}</span>
                      <span className="mt-0.5 line-clamp-1 block text-xs text-muted">{g.last_sender ? `${g.last_sender}: ` : ""}{g.last_message}</span>
                    </span>
                    <span className="min-w-5 shrink-0 rounded-full bg-accent px-1.5 text-center text-2xs font-semibold leading-5 text-accent-foreground">{g.unread}</span>
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {items.length > 0 && (
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
        </>
      )}
    </Page>
  );
}
