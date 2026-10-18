"use client";

import { useCallback, useEffect, useState } from "react";
import { Archive, ArchiveRestore, Trash2 } from "lucide-react";
import { Button, Page, PageEmpty, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import { reportError } from "@/lib/report-error";
import type { Thread } from "@/types";

/** Conversations you archived: kept, out of the way. Restore one to bring it back to the sidebar. */
export function ArchivedTab() {
  const [threads, setThreads] = useState<Thread[] | null>(null);

  const load = useCallback(async () => {
    try {
      setThreads(await api.getThreads({ archived: true }));
    } catch (err) {
      setThreads([]);
      reportError("Couldn't load archived conversations", err);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
  }, [load]);

  const restore = async (t: Thread) => {
    try {
      await api.updateThread(t.id, { archived: false });
      setThreads((all) => (all ?? []).filter((x) => x.id !== t.id));
      toast.success("Restored", "It is back in your conversations.");
    } catch (err) {
      reportError("Couldn't restore the conversation", err);
    }
  };

  const remove = async (t: Thread) => {
    const ok = await confirmAction({ title: "Delete this conversation?", description: `“${t.name}” and its messages are removed. This can't be undone.`, confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.deleteThread(t.id);
      setThreads((all) => (all ?? []).filter((x) => x.id !== t.id));
    } catch (err) {
      reportError("Couldn't delete the conversation", err);
    }
  };

  return (
    <Page title="Archived" subtitle="Conversations you put away. They are kept until you delete them." icon={Archive}>
      {threads === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : threads.length === 0 ? (
        <PageEmpty icon={Archive} title="Nothing archived">
          Archive a conversation from its menu in the sidebar to put it here.
        </PageEmpty>
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {threads.map((t) => (
            <li key={t.id} className="flex items-center gap-2 px-4 py-2.5">
              <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{t.name}</p>
              <span className="shrink-0 text-xs text-muted">{new Date(t.updated_at).toLocaleDateString([], { month: "short", day: "numeric" })}</span>
              <Button variant="secondary" onClick={() => void restore(t)}>
                <ArchiveRestore /> Restore
              </Button>
              <Button variant="danger" size="icon" aria-label={`Delete ${t.name}`} onClick={() => void remove(t)}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}
