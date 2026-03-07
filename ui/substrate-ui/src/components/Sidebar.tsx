"use client";

import { useState, useMemo } from "react";
import { Thread } from "@/types";
import {
  SquarePen, Pencil, Trash2,
  Check, X, Search, PanelLeftClose
} from "lucide-react";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  onNewChat: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  onRenameThread: (threadId: string, newName: string) => void;
  onCollapse?: () => void;
};

function groupByDate(threads: Thread[]): Record<string, Thread[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  const week = new Date(today); week.setDate(today.getDate() - 7);
  const month = new Date(today); month.setDate(today.getDate() - 30);

  const groups: Record<string, Thread[]> = {
    "Today": [], "Yesterday": [], "Last 7 days": [], "Last 30 days": [], "Older": []
  };

  for (const t of threads) {
    const d = new Date(t.updated_at || t.created_at);
    if (d >= today)     groups["Today"].push(t);
    else if (d >= yesterday) groups["Yesterday"].push(t);
    else if (d >= week)      groups["Last 7 days"].push(t);
    else if (d >= month)     groups["Last 30 days"].push(t);
    else                     groups["Older"].push(t);
  }

  return groups;
}

export function Sidebar({
  threads, currentThreadId, onNewChat,
  onSelectThread, onDeleteThread, onRenameThread, onCollapse,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() =>
    search.trim() === ""
      ? threads
      : threads.filter(t => t.name.toLowerCase().includes(search.toLowerCase())),
    [threads, search]
  );

  const groups = useMemo(() => groupByDate(filtered), [filtered]);

  const startEdit = (t: Thread) => { setEditingId(t.id); setEditName(t.name); };
  const saveEdit  = (id: string) => { if (editName.trim()) onRenameThread(id, editName.trim()); setEditingId(null); };
  const cancelEdit = () => { setEditingId(null); setEditName(""); };

  return (
    <aside className="w-64 h-full bg-(--sidebar-bg) flex flex-col overflow-hidden" suppressHydrationWarning>
      {/*  Top: Logo + actions  */}
      <div className="flex items-center justify-between px-3 py-3">
        <div className="flex items-center gap-1">
          {onCollapse && (
            <button
              onClick={onCollapse}
              title="Collapse sidebar"
              className="p-1.5 rounded-lg hover:bg-(--card-hover) transition-colors"
            >
              <PanelLeftClose className="w-4 h-4" style={{ color: "var(--muted)" }} />
            </button>
          )}
          <span className="text-sm font-semibold tracking-tight px-1 opacity-80">Agent Chat</span>
        </div>
        <button
          onClick={onNewChat}
          title="New chat"
          className="p-2 rounded-lg hover:bg-(--card-hover) transition-colors"
        >
          <SquarePen className="w-4 h-4" />
        </button>
      </div>

      {/*  Search  */}
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-(--card) text-sm text-(--muted)">
          <Search className="w-3.5 h-3.5 shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search chats"
            className="flex-1 bg-transparent outline-none text-foreground placeholder:text-(--muted) text-xs"
          />
          {search && (
            <button onClick={() => setSearch("")} className="hover:opacity-70">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/*  Thread list  */}
      <div className="flex-1 overflow-y-auto px-2">
        {threads.length === 0 ? (
          <p className="text-xs text-(--muted) px-3 py-4 text-center">No conversations yet</p>
        ) : filtered.length === 0 ? (
          <p className="text-xs text-(--muted) px-3 py-4 text-center">No results for &ldquo;{search}&rdquo;</p>
        ) : (
          Object.entries(groups).map(([label, items]) => {
            if (items.length === 0) return null;
            return (
              <div key={label} className="mb-3">
                <p className="text-xs font-semibold text-(--muted) px-3 pb-1 pt-1">{label}</p>
                <div className="space-y-0.5">
                  {items.map(thread => (
                    <ThreadItem
                      key={thread.id}
                      thread={thread}
                      isActive={thread.id === currentThreadId}
                      isEditing={editingId === thread.id}
                      editName={editName}
                      onSelect={() => onSelectThread(thread.id)}
                      onStartEdit={() => startEdit(thread)}
                      onSaveEdit={() => saveEdit(thread.id)}
                      onCancelEdit={cancelEdit}
                      onDelete={() => onDeleteThread(thread.id)}
                      onEditNameChange={setEditName}
                    />
                  ))}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/*  User Section  */}
      <div className="p-2 border-t border-(--border)">
        <div className="flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-(--card-hover) cursor-pointer transition-colors">
          <div className="w-7 h-7 rounded-full bg-linear-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-xs font-bold text-white shrink-0">
            U
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium truncate">My Account</p>
          </div>
        </div>
      </div>
    </aside>
  );
}

/*  Thread item sub-component  */
interface ThreadItemProps {
  thread: Thread;
  isActive: boolean;
  isEditing: boolean;
  editName: string;
  onSelect: () => void;
  onStartEdit: () => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onDelete: () => void;
  onEditNameChange: (v: string) => void;
}

function ThreadItem({
  thread, isActive, isEditing, editName,
  onSelect, onStartEdit, onSaveEdit, onCancelEdit, onDelete, onEditNameChange,
}: ThreadItemProps) {
  if (isEditing) {
    return (
      <div className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-(--card)">
        <input
          value={editName}
          onChange={e => onEditNameChange(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") onSaveEdit(); if (e.key === "Escape") onCancelEdit(); }}
          className="flex-1 bg-transparent outline-none text-xs border-b border-(--border) pb-0.5"
          autoFocus
        />
        <button onClick={onSaveEdit}  className="p-1 hover:bg-(--card-hover) rounded text-green-400"><Check className="w-3 h-3" /></button>
        <button onClick={onCancelEdit} className="p-1 hover:bg-(--card-hover) rounded opacity-50"><X className="w-3 h-3" /></button>
      </div>
    );
  }

  return (
    <div
      className={"group relative flex items-center rounded-lg transition-colors cursor-pointer " +
        (isActive ? "bg-(--card)" : "hover:bg-(--card-hover)")}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0 px-3 py-2">
        <p className="text-sm truncate leading-snug">{thread.name}</p>
      </div>

      {/* Hover actions */}
      <div className="pr-1 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0">
        <button
          onClick={e => { e.stopPropagation(); onStartEdit(); }}
          className="p-1 rounded hover:bg-(--card-hover)"
          title="Rename"
        >
          <Pencil className="w-3 h-3" />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          className="p-1 rounded hover:bg-red-500/20 text-red-400"
          title="Delete"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}