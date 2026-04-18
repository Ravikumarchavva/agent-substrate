"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { Thread } from "@/types";
import {
  SquarePen, Pencil, Trash2,
  Check, X, Search, PanelLeftClose, Settings, Sun, Moon,
  User, LogOut, ShieldCheck, ChevronsUpDown,
} from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";
import { useAuth } from "@/contexts/AuthContext";
import type { SettingsTab } from "./SettingsPanel";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  onNewChat: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => Promise<void> | void;
  onRenameThread: (threadId: string, newName: string) => void;
  onCollapse?: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
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
  onSelectThread, onDeleteThread, onRenameThread, onCollapse, onOpenSettings,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [threadPendingDelete, setThreadPendingDelete] = useState<Thread | null>(null);
  const [isDeletingThread, setIsDeletingThread] = useState(false);
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, isAdmin, loginWithGoogle, logout } = useAuth();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    if (menuOpen) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  useEffect(() => {
    if (!threadPendingDelete || isDeletingThread) {
      return;
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setThreadPendingDelete(null);
      }
    }

    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isDeletingThread, threadPendingDelete]);

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
  const requestDelete = (thread: Thread) => {
    setThreadPendingDelete(thread);
  };
  const handleConfirmDelete = async () => {
    if (!threadPendingDelete) {
      return;
    }

    setIsDeletingThread(true);
    try {
      await onDeleteThread(threadPendingDelete.id);
      setThreadPendingDelete(null);
    } finally {
      setIsDeletingThread(false);
    }
  };

  return (
    <>
      <aside
        className="flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-(--border) bg-(--card) shadow-2xl lg:shadow-none"
        suppressHydrationWarning
      >
      {/*  Top: Logo + actions  */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-(--border)">
        <div className="flex items-center gap-1">
          {onCollapse && (
            <button
              onClick={onCollapse}
              title="Collapse sidebar"
              className="p-1.5 rounded-lg hover:bg-background transition-colors cursor-pointer"
            >
              <PanelLeftClose className="w-4 h-4" style={{ color: "var(--muted)" }} />
            </button>
          )}
          <span className="text-sm font-semibold tracking-tight px-1">Workspace</span>
        </div>
        <button
          onClick={onNewChat}
          title="New chat"
          className="p-2 rounded-lg hover:bg-background transition-colors cursor-pointer"
        >
          <SquarePen className="w-4 h-4" />
        </button>
      </div>

      {/*  Search  */}
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-(--border) bg-background text-sm text-(--muted)">
          <Search className="w-3.5 h-3.5 shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search chats"
            className="flex-1 bg-transparent outline-none text-foreground placeholder:text-(--muted) text-xs"
          />
          {search && (
            <button onClick={() => setSearch("")} className="hover:opacity-70 cursor-pointer">
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
                <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-(--muted) px-3 pb-1 pt-2">
                  {label}
                </p>
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
                      onDelete={() => requestDelete(thread)}
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
      <div className="p-2 border-t border-(--border)" ref={menuRef}>
        {/* Dropdown — anchored above trigger */}
        {menuOpen && (
          <div className="mb-1 bg-(--card) border border-(--border) rounded-xl shadow-xl overflow-hidden">
            {/* Signed-in: user info header */}
            {isAuthenticated && user && (
              <div className="px-3 py-2.5 border-b border-(--border)">
                <div className="text-sm font-medium truncate">{user.name ?? "User"}</div>
                <div className="text-xs text-(--muted) truncate">{user.email ?? ""}</div>
                {isAdmin && (
                  <span className="inline-flex items-center gap-1 mt-1 text-xs" style={{ color: "var(--accent)" }}>
                    <ShieldCheck className="w-3 h-3" /> Admin
                  </span>
                )}
              </div>
            )}

            {/* Theme toggle */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-(--border)">
              <span className="text-sm">
                {theme === "dark" ? "Dark mode" : "Light mode"}
              </span>
              <button
                onClick={toggleTheme}
                className="p-1.5 hover:bg-(--card-hover) rounded-lg transition-colors cursor-pointer"
                aria-label="Toggle theme"
              >
                {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              </button>
            </div>

            {/* Settings & Profile */}
            <div className="py-1">
              <button
                onClick={() => { setMenuOpen(false); onOpenSettings("general"); }}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
              >
                <Settings className="w-3.5 h-3.5 text-(--muted)" />
                Settings
              </button>
              {isAuthenticated && (
                <>
                  {isAdmin && (
                    <button
                      onClick={() => { setMenuOpen(false); onOpenSettings("admin"); }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                    >
                      <ShieldCheck className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
                      <span style={{ color: "var(--accent)" }}>Admin Panel</span>
                    </button>
                  )}
                </>
              )}
            </div>

            {/* Sign in / Sign out */}
            <div className="border-t border-(--border) py-1">
              {isAuthenticated ? (
                <button
                  onClick={async () => {
                    setMenuOpen(false);
                    const confirmed = window.confirm(
                      "Sign out of Google? Spotify will stay connected until you disconnect it from Apps."
                    );
                    if (!confirmed) {
                      return;
                    }
                    await logout();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-red-400 hover:bg-(--card-hover) transition-colors cursor-pointer"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  Sign out
                </button>
              ) : (
                <button
                  onClick={() => { setMenuOpen(false); loginWithGoogle(); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                >
                  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  Sign in with Google
                </button>
              )}
            </div>
          </div>
        )}

        {/* Trigger row */}
        <button
          onClick={() => setMenuOpen((o) => !o)}
          className="w-full flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer"
          aria-expanded={menuOpen}
          aria-haspopup="true"
        >
          {/* Avatar */}
          {isAuthenticated && user?.picture ? (
            <img
              src={user.picture}
              alt={user.name ?? "User"}
              className="w-7 h-7 rounded-full object-cover shrink-0"
            />
          ) : (
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0"
              style={{ background: "var(--accent)" }}
            >
              {isAuthenticated && user
                ? (user.name ?? user.email ?? "?")[0].toUpperCase()
                : <User className="w-3.5 h-3.5" />}
            </div>
          )}
          {/* Name */}
          <div className="flex-1 min-w-0 text-left">
            <p className="text-xs font-medium truncate">
              {isAuthenticated && user ? (user.name ?? user.email ?? "My Account") : "My Account"}
            </p>
          </div>
          <ChevronsUpDown className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--muted)" }} />
        </button>
      </div>
      </aside>

      {threadPendingDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 bg-black/60 cursor-pointer"
            onClick={() => !isDeletingThread && setThreadPendingDelete(null)}
            aria-label="Close delete confirmation"
          />
          <div
            className="relative w-full max-w-md rounded-2xl border border-(--border) bg-background p-6 shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-label="Delete chat thread"
          >
            <div className="flex items-start gap-4">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-red-500/12 text-red-400">
                <Trash2 className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="text-lg font-semibold text-foreground">Delete chat thread?</h2>
                <p className="mt-2 text-sm leading-6 text-(--muted)">
                  This removes
                  <span className="font-medium text-foreground"> {threadPendingDelete.name}</span>
                  and its message history permanently.
                </p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setThreadPendingDelete(null)}
                disabled={isDeletingThread}
                className="rounded-xl border border-(--border) px-4 py-2 text-sm text-foreground transition-colors hover:bg-(--card-hover) disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmDelete()}
                disabled={isDeletingThread}
                className="rounded-xl bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer"
              >
                {isDeletingThread ? "Deleting..." : "Delete thread"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
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
      <div className="flex items-center gap-1 px-2 py-1.5 rounded-lg border border-(--border) bg-background">
        <input
          value={editName}
          onChange={e => onEditNameChange(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") onSaveEdit(); if (e.key === "Escape") onCancelEdit(); }}
          className="flex-1 bg-transparent outline-none text-xs border-b border-(--border) pb-0.5"
          autoFocus
        />
        <button onClick={onSaveEdit}  className="p-1 hover:bg-(--card-hover) rounded text-green-400 cursor-pointer"><Check className="w-3 h-3" /></button>
        <button onClick={onCancelEdit} className="p-1 hover:bg-(--card-hover) rounded opacity-50 cursor-pointer"><X className="w-3 h-3" /></button>
      </div>
    );
  }

  return (
    <div
      className={"group relative flex items-center rounded-lg transition-colors cursor-pointer " +
        (isActive
          ? "bg-background text-foreground"
          : "text-(--muted) hover:bg-background hover:text-foreground")}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0 px-3 py-2">
        <p className={"text-sm truncate leading-snug " + (isActive ? "font-medium" : "")}>{thread.name}</p>
      </div>

      {/* Hover actions */}
      <div className="pr-1 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0">
        <button
          onClick={e => { e.stopPropagation(); onStartEdit(); }}
          className="p-1 rounded hover:bg-(--card-hover) cursor-pointer"
          title="Rename"
        >
          <Pencil className="w-3 h-3" />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          className="p-1 rounded hover:bg-red-500/20 text-red-400 cursor-pointer"
          title="Delete"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}