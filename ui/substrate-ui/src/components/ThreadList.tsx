"use client";

import { useEffect, useMemo, useState } from "react";
import { Archive, ArchiveRestore, Download, MoreHorizontal, Pencil, Pin, PinOff, Share2, Trash2, X, Check } from "lucide-react";
import { Button, Menu, NavItem, MenuContent, MenuItem, MenuSeparator, MenuSub, MenuSubContent, MenuSubTrigger, MenuTrigger, confirmAction } from "@/design";
import { api } from "@/lib/api";
import { reportError } from "@/lib/report-error";
import { ShareDialog } from "@/components/ShareDialog";
import type { Thread, ThreadSearchHit } from "@/types";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  search: string;
  showArchived: boolean;
  hasMore: boolean;
  onSelect: (threadId: string) => void;
  onRename: (threadId: string, newName: string) => void;
  onDelete: (threadId: string) => Promise<void> | void;
  onPin: (threadId: string, pinned: boolean) => void;
  onArchive: (threadId: string, archived: boolean) => void;
  onLoadMore: () => void;
  onToggleArchived: (archived: boolean) => void;
};

function groupByDate(threads: Thread[]): [string, Thread[]][] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = (n: number) => new Date(today.getTime() - n * 86_400_000);
  const buckets: [string, Date][] = [
    ["Today", today],
    ["Yesterday", days(1)],
    ["Last 7 days", days(7)],
    ["Last 30 days", days(30)],
  ];
  const groups = new Map<string, Thread[]>([...buckets.map(([label]) => [label, [] as Thread[]] as [string, Thread[]]), ["Older", []]]);
  for (const thread of threads) {
    const at = new Date(thread.updated_at || thread.created_at);
    const label = buckets.find(([, since]) => at >= since)?.[0] ?? "Older";
    groups.get(label)!.push(thread);
  }
  return [...groups].filter(([, items]) => items.length > 0);
}

const SEARCH_DELAY_MS = 300;

/** The sidebar's conversation list: pinned first, then by date; search matches titles at once and message text a moment later. */
export function ThreadList(props: Props) {
  const { threads, currentThreadId, search, showArchived, hasMore, onSelect, onRename, onDelete, onPin, onArchive, onLoadMore, onToggleArchived } = props;
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [sharing, setSharing] = useState<Thread | null>(null);
  const [hits, setHits] = useState<ThreadSearchHit[]>([]);

  const query = search.trim();
  const titleMatches = useMemo(() => (query ? threads.filter((t) => t.name.toLowerCase().includes(query.toLowerCase())) : threads), [threads, query]);

  useEffect(() => {
    if (query.length < 2) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clear stale results when the query is emptied
      setHits([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .searchThreads(query)
        .then((found) => !cancelled && setHits(found))
        .catch(() => !cancelled && setHits([]));
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const pinned = titleMatches.filter((t) => t.pinned_at);
  const rest = titleMatches.filter((t) => !t.pinned_at);

  const remove = async (thread: Thread) => {
    const ok = await confirmAction({
      title: "Delete this conversation?",
      description: `“${thread.name}” and its messages are removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (ok) await onDelete(thread.id);
  };

  const exportAs = async (thread: Thread, format: "md" | "json") => {
    try {
      await api.exportThread(thread.id, format);
    } catch (err) {
      reportError("Couldn't export the conversation", err);
    }
  };

  const row = (thread: Thread) => (
    <ThreadRow
      key={thread.id}
      thread={thread}
      active={thread.id === currentThreadId}
      editing={editing?.id === thread.id ? editing.name : null}
      archivedView={showArchived}
      onSelect={() => onSelect(thread.id)}
      onEditChange={(name) => setEditing({ id: thread.id, name })}
      onStartEdit={() => setEditing({ id: thread.id, name: thread.name })}
      onSaveEdit={() => {
        if (editing?.name.trim()) onRename(thread.id, editing.name.trim());
        setEditing(null);
      }}
      onCancelEdit={() => setEditing(null)}
      onPin={() => onPin(thread.id, !thread.pinned_at)}
      onArchive={() => onArchive(thread.id, !thread.archived_at && !showArchived)}
      onShare={() => setSharing(thread)}
      onExport={(format) => void exportAs(thread, format)}
      onDelete={() => void remove(thread)}
    />
  );

  return (
    <div className="flex-1 overflow-y-auto px-2 pb-3 pt-4">
      <p className="px-1 pb-2 text-2xs font-semibold uppercase tracking-wider text-muted">Recents</p>

      {threads.length === 0 ? (
        <p className="px-3 py-8 text-center text-xs text-muted">No conversations yet</p>
      ) : (
        <>
          {query && titleMatches.length === 0 && hits.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-muted">No results for &ldquo;{query}&rdquo;</p>
          )}

          {!query && pinned.length > 0 && (
            <div className="mb-4">
              <p className="px-1 pb-2 pt-2 text-2xs font-semibold uppercase tracking-wider text-muted">Pinned</p>
              <div className="space-y-0.5">{pinned.map(row)}</div>
            </div>
          )}

          {query ? (
            titleMatches.length > 0 && <div className="mb-4 space-y-0.5">{titleMatches.map(row)}</div>
          ) : (
            groupByDate(rest).map(([label, items]) => (
              <div key={label} className="mb-4">
                <p className="px-1 pb-2 pt-2 text-2xs font-semibold uppercase tracking-wider text-muted">{label}</p>
                <div className="space-y-0.5">{items.map(row)}</div>
              </div>
            ))
          )}

          {hits.length > 0 && (
            <div className="mb-4">
              <p className="px-1 pb-2 pt-2 text-2xs font-semibold uppercase tracking-wider text-muted">In messages</p>
              <ul className="space-y-0.5">
                {hits.map((hit, i) => (
                  <li key={`${hit.thread_id}-${i}`}>
                    <button
                      type="button"
                      onClick={() => onSelect(hit.thread_id)}
                      className="w-full cursor-pointer rounded-lg px-3 py-2 text-left transition-colors hover:bg-card-hover"
                    >
                      <p className="truncate text-xs font-medium text-foreground">{hit.thread_name ?? "Conversation"}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted">
                        {hit.role === "user" ? "You: " : ""}
                        {hit.snippet}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {hasMore && !query && (
            <NavItem onClick={onLoadMore}>Show more</NavItem>
          )}
        </>
      )}


      <ShareDialog thread={sharing} onClose={() => setSharing(null)} />
    </div>
  );
}

function ThreadRow(props: {
  thread: Thread;
  active: boolean;
  editing: string | null;
  archivedView: boolean;
  onSelect: () => void;
  onEditChange: (name: string) => void;
  onStartEdit: () => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onPin: () => void;
  onArchive: () => void;
  onShare: () => void;
  onExport: (format: "md" | "json") => void;
  onDelete: () => void;
}) {
  const { thread, active, editing, archivedView } = props;

  if (editing !== null) {
    return (
      <div className="flex items-center gap-1 rounded-xl bg-card px-3 py-1.5" style={{ boxShadow: "var(--shadow-sm)" }}>
        <input
          value={editing}
          onChange={(e) => props.onEditChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") props.onSaveEdit();
            if (e.key === "Escape") props.onCancelEdit();
          }}
          aria-label="Conversation name"
          className="flex-1 bg-transparent text-xs outline-none"
          autoFocus
        />
        <Button size="icon-sm" variant="ghost" aria-label="Save name" onClick={props.onSaveEdit}>
          <Check />
        </Button>
        <Button size="icon-sm" variant="ghost" aria-label="Cancel rename" onClick={props.onCancelEdit}>
          <X />
        </Button>
      </div>
    );
  }

  return (
    <div
      className={`group relative flex h-control-lg items-center rounded-lg transition-colors ${active ? "bg-accent/12" : "hover:bg-card-hover"}`}
    >
      <button type="button" onClick={props.onSelect} className="flex h-full min-w-0 flex-1 cursor-pointer items-center gap-2 px-3 text-left" aria-current={active ? "page" : undefined}>
        {thread.pinned_at && <Pin className="size-3 shrink-0 text-muted" aria-label="Pinned" />}
        <span className="truncate text-sm font-medium" title={thread.name}>
          {thread.name}
        </span>
      </button>
      <Menu>
        <MenuTrigger asChild>
          <Button size="icon-sm" variant="ghost" aria-label={`Actions for ${thread.name}`} className="mr-1 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100">
            <MoreHorizontal />
          </Button>
        </MenuTrigger>
        <MenuContent align="end">
          {!archivedView && (
            <MenuItem onSelect={props.onPin}>
              {thread.pinned_at ? <PinOff /> : <Pin />}
              {thread.pinned_at ? "Unpin" : "Pin"}
            </MenuItem>
          )}
          <MenuItem onSelect={props.onStartEdit}>
            <Pencil /> Rename
          </MenuItem>
          <MenuItem onSelect={props.onShare}>
            <Share2 /> Share
          </MenuItem>
          <MenuSub>
            <MenuSubTrigger>
              <Download /> Export
            </MenuSubTrigger>
            <MenuSubContent>
              <MenuItem onSelect={() => props.onExport("md")}>Markdown</MenuItem>
              <MenuItem onSelect={() => props.onExport("json")}>JSON</MenuItem>
            </MenuSubContent>
          </MenuSub>
          <MenuItem onSelect={props.onArchive}>
            {archivedView ? <ArchiveRestore /> : <Archive />}
            {archivedView ? "Restore" : "Archive"}
          </MenuItem>
          <MenuSeparator />
          <MenuItem tone="danger" onSelect={props.onDelete}>
            <Trash2 /> Delete
          </MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );
}
