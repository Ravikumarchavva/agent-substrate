"use client";

import { useState } from "react";
import { Thread } from "@/types";
import { PlusIcon, MessageSquare, Pencil, Trash2, Check, X } from "lucide-react";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  onNewChat: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  onRenameThread: (threadId: string, newName: string) => void;
};

export function Sidebar({
  threads,
  currentThreadId,
  onNewChat,
  onSelectThread,
  onDeleteThread,
  onRenameThread,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const startEdit = (thread: Thread) => {
    setEditingId(thread.id);
    setEditName(thread.name);
  };

  const saveEdit = (threadId: string) => {
    if (editName.trim()) {
      onRenameThread(threadId, editName.trim());
    }
    setEditingId(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
  };

  return (
    <aside className="w-64 bg-[var(--sidebar-bg)] border-r border-[var(--border)] flex flex-col" suppressHydrationWarning>
      {/* New Chat Button */}
      <div className="p-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[var(--border)] hover:bg-[var(--card-hover)] transition-colors text-sm font-medium"
        >
          <PlusIcon className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* Threads List */}
      <div className="flex-1 overflow-y-auto px-2">
        <div className="text-xs font-semibold text-zinc-500 px-3 py-2">
          Recent Chats
        </div>
        <div className="space-y-1">
          {threads.map((thread) => (
            <div
              key={thread.id}
              className={`group relative rounded-lg transition-colors ${
                currentThreadId === thread.id
                  ? "bg-[var(--card)]"
                  : "hover:bg-[var(--card-hover)]"
              }`}
            >
              {editingId === thread.id ? (
                <div className="flex items-center gap-1 px-3 py-2">
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveEdit(thread.id);
                      if (e.key === "Escape") cancelEdit();
                    }}
                    className="flex-1 bg-[var(--input-bg)] border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-zinc-600"
                    autoFocus
                  />
                  <button
                    onClick={() => saveEdit(thread.id)}
                    className="p-1 hover:bg-[var(--card-hover)] rounded"
                  >
                    <Check className="w-3 h-3" />
                  </button>
                  <button
                    onClick={cancelEdit}
                    className="p-1 hover:bg-[var(--card-hover)] rounded"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => onSelectThread(thread.id)}
                  className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
                >
                  <MessageSquare className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                  <span className="flex-1 text-sm truncate">
                    {thread.name}
                  </span>
                </button>
              )}

              {/* Actions (show on hover) */}
              {editingId !== thread.id && (
                <div className="absolute right-2 top-1/2 -translate-y-1/2 hidden group-hover:flex items-center gap-1">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      startEdit(thread);
                    }}
                    className="p-1 hover:bg-[var(--card-hover)] rounded"
                    title="Rename"
                  >
                    <Pencil className="w-3 h-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteThread(thread.id);
                    }}
                    className="p-1 hover:bg-red-900/50 rounded"
                    title="Delete"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Bottom User Section */}
      <div className="p-3 border-t border-[var(--border)]">
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-[var(--card-hover)] cursor-pointer">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-sm font-semibold">
            U
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate">User</div>
            <div className="text-xs text-zinc-500 truncate">user@example.com</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
