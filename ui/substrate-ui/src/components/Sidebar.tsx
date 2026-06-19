"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import type { Thread } from "@/types";
import {
  ArrowLeft,
  Check,
  ChevronsUpDown,
  LogOut,
  Moon, MoreHorizontal,
  Pencil,
  Search,
  Settings2,
  ShieldCheck,
  SquarePen,
  Sun,
  Trash2,
  User,
  X,
  type LucideIcon,
} from "lucide-react";
import { RaviMark } from "@/components/RaviMark";
import { RateLimitBar } from "@/components/RateLimitBar";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { useTheme } from "@/contexts/ThemeContext";
import { useAuth } from "@/contexts/AuthContext";
import { getVisibleSettingsTabGroups, type SettingsTab } from "./settings/SettingsPanel";

type SidebarMode = "chat" | "settings";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  onNewChat: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => Promise<void> | void;
  onRenameThread: (threadId: string, newName: string) => void;
  onCollapse?: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
  mode?: SidebarMode;
  settingsTab?: SettingsTab;
  onSelectSettingsTab?: (tab: SettingsTab) => void;
  onBackToChat?: () => void;
};

function groupByDate(threads: Thread[]): Record<string, Thread[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const week = new Date(today);
  week.setDate(today.getDate() - 7);
  const month = new Date(today);
  month.setDate(today.getDate() - 30);

  const groups: Record<string, Thread[]> = {
    Today: [],
    Yesterday: [],
    "Last 7 days": [],
    "Last 30 days": [],
    Older: [],
  };

  for (const thread of threads) {
    const updatedAt = new Date(thread.updated_at || thread.created_at);
    if (updatedAt >= today) groups.Today.push(thread);
    else if (updatedAt >= yesterday) groups.Yesterday.push(thread);
    else if (updatedAt >= week) groups["Last 7 days"].push(thread);
    else if (updatedAt >= month) groups["Last 30 days"].push(thread);
    else groups.Older.push(thread);
  }

  return groups;
}

interface QuickActionButtonProps {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  isActive?: boolean;
}

function QuickActionButton({ icon: Icon, label, onClick, isActive = false }: QuickActionButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`ravi-press flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-3 text-left text-sm transition-colors ${isActive ? "bg-foreground text-background" : "text-foreground hover:bg-(--card-hover)"}`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="truncate font-medium">{label}</span>
    </button>
  );
}

export function Sidebar({
  threads,
  currentThreadId,
  onNewChat,
  onSelectThread,
  onDeleteThread,
  onRenameThread,
  onCollapse,
  onOpenSettings,
  mode = "chat",
  settingsTab,
  onSelectSettingsTab,
  onBackToChat,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [threadPendingDelete, setThreadPendingDelete] = useState<Thread | null>(null);
  const [isDeletingThread, setIsDeletingThread] = useState(false);
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, isAdmin, loginWithGoogle, logout } = useAuth();
  const menuRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const isSettingsMode = mode === "settings";

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    if (menuOpen) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  useEffect(() => {
    if (!threadPendingDelete || isDeletingThread) return;

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setThreadPendingDelete(null);
    }

    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isDeletingThread, threadPendingDelete]);

  const filteredThreads = useMemo(
    () =>
      search.trim() === ""
        ? threads
        : threads.filter((thread) => thread.name.toLowerCase().includes(search.toLowerCase())),
    [search, threads],
  );

  const threadGroups = useMemo(() => groupByDate(filteredThreads), [filteredThreads]);
  const settingsGroups = useMemo(() => getVisibleSettingsTabGroups(isAdmin), [isAdmin]);

  const startEdit = (thread: Thread) => {
    setEditingId(thread.id);
    setEditName(thread.name);
  };

  const saveEdit = (threadId: string) => {
    if (editName.trim()) onRenameThread(threadId, editName.trim());
    setEditingId(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
  };

  const handleConfirmDelete = async () => {
    if (!threadPendingDelete) return;

    setIsDeletingThread(true);
    try {
      await onDeleteThread(threadPendingDelete.id);
      setThreadPendingDelete(null);
    } finally {
      setIsDeletingThread(false);
    }
  };

  const chatQuickActions = [{ label: "New chat", icon: SquarePen, onClick: onNewChat }];

  const handleTouchStart = (e: React.TouchEvent) => {
    setTouchEnd(null);
    setTouchStart(e.targetTouches[0].clientX);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    setTouchEnd(e.targetTouches[0].clientX);
  };

  const handleTouchEnd = () => {
    if (!touchStart || !touchEnd) return;
    const distance = touchStart - touchEnd;
    const isLeftSwipe = distance > 50;

    if (isLeftSwipe && onCollapse) {
      onCollapse();
    }
  };

  return (
    <>
      <aside
        className="flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-(--border) bg-(--card) shadow-2xl lg:shadow-none"
        suppressHydrationWarning
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <div className="flex items-center justify-between px-2 py-2">
          <div className="flex items-center gap-3 ml-2">
            <RaviMark className="h-6 w-6 text-foreground" />
          </div>
          {onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              title="Collapse sidebar"
              className="btn-icon flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-transparent text-(--muted) transition-colors hover:border-(--border) hover:bg-background hover:text-foreground"
            >
              <SidebarToggleIcon direction="close" className="h-5 w-5" />
            </button>
          )}
        </div>

        <div className="px-2 pt-2">
          <div className="space-y-1">
            {isSettingsMode ? (
              <QuickActionButton icon={ArrowLeft} label="Back to chats" onClick={() => onBackToChat?.()} />
            ) : (
              chatQuickActions.map((action) => (
                <QuickActionButton
                  key={action.label}
                  icon={action.icon}
                  label={action.label}
                  onClick={action.onClick}
                />
              ))
            )}
          </div>
        </div>

        {isSettingsMode ? (
          <div className="flex-1 overflow-y-auto px-2 py-4">
            <div className="space-y-5">
              {settingsGroups.map((group) => (
                <div key={group.title}>
                  <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-[0.16em] text-(--muted)">
                    {group.title}
                  </p>
                  <div className="space-y-1">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const active = item.id === settingsTab;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => onSelectSettingsTab?.(item.id)}
                          className={`flex w-full items-start gap-3 rounded-2xl px-3 py-3 text-left transition-colors cursor-pointer ${active ? "bg-background text-foreground" : "text-foreground hover:bg-background"}`}
                        >
                          <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl ${active ? "bg-foreground text-background" : "bg-(--badge-bg) text-(--muted)"}`}>
                            <Icon className="h-4 w-4" />
                          </div>
                          <div className="min-w-0">
                            <div className="text-sm font-medium">{item.label}</div>
                            <div className="mt-1 text-xs leading-5 text-(--muted)">{item.description}</div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="px-3 pb-2 pt-3">
              <div className="flex items-center gap-2 rounded-xl border border-(--border) bg-background px-3 py-2.5 text-sm text-(--muted)">
                <Search className="h-4 w-4 shrink-0" />
                <input
                  ref={searchInputRef}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search threads"
                  className="flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-(--muted)"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    className="btn-icon cursor-pointer rounded-lg p-1 hover:bg-(--card-hover)"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-3 pt-4">
              <p className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-wider text-(--muted)">
                Recents
              </p>
              {threads.length === 0 ? (
                <p className="px-3 py-8 text-center text-xs text-(--muted)">No conversations yet</p>
              ) : filteredThreads.length === 0 ? (
                <p className="px-3 py-8 text-center text-xs text-(--muted)">
                  No results for &ldquo;{search}&rdquo;
                </p>
              ) : (
                Object.entries(threadGroups).map(([label, items]) => {
                  if (items.length === 0) return null;

                  return (
                    <div key={label} className="mb-4">
                      <p className="px-1 pb-2 pt-2 text-[11px] font-semibold uppercase tracking-wider text-(--muted)">
                        {label}
                      </p>
                      <div className="space-y-0.5">
                        {items.map((thread) => (
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
                            onDelete={() => setThreadPendingDelete(thread)}
                            onEditNameChange={setEditName}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </>
        )}

        <RateLimitBar />

        <div className="mt-auto px-2 pb-3 pt-2" ref={menuRef}>
          {menuOpen && (
            <div className="ravi-pop-in mb-1.5 overflow-hidden rounded-2xl bg-(--card)" style={{ boxShadow: "var(--shadow-lg)", transformOrigin: "bottom center" }}>
              {isAuthenticated && user && (
                <div className="border-b border-(--border) px-2.5 py-3">
                  <div className="truncate text-sm font-medium">{user.name ?? "User"}</div>
                  <div className="mt-0.5 truncate text-xs text-(--muted)">{user.email ?? ""}</div>
                  {isAdmin && (
                    <span className="mt-1.5 inline-flex items-center gap-1 text-xs text-emerald-500">
                      <ShieldCheck className="h-3 w-3" /> Admin
                    </span>
                  )}
                </div>
              )}

              <div className="flex items-center justify-between border-b border-(--border) px-4 py-2.5">
                <span className="text-sm">{theme === "dark" ? "Dark mode" : "Light mode"}</span>
                <button
                  type="button"
                  onClick={toggleTheme}
                  className="flex items-center justify-center cursor-pointer rounded-xl p-1.5 transition-colors hover:bg-(--card-hover)"
                  aria-label="Toggle theme"
                >
                  {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                </button>
              </div>

              <div className="py-1">
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onOpenSettings("general");
                  }}
                  className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-(--card-hover)"
                >
                  <Settings2 className="h-4 w-4 text-(--muted)" />
                  Settings
                </button>
              </div>

              <div className="border-t border-(--border) py-1">
                {isAuthenticated ? (
                  <button
                    type="button"
                    onClick={async () => {
                      setMenuOpen(false);
                      const confirmed = window.confirm(
                        "Sign out of Google? Spotify will stay connected until you disconnect it from Apps.",
                      );
                      if (!confirmed) return;
                      await logout();
                    }}
                    className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm text-red-400 transition-colors hover:bg-(--card-hover)"
                  >
                    <LogOut className="h-4 w-4" />
                    Sign out
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      loginWithGoogle();
                    }}
                    className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-(--card-hover)"
                  >
                    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                    </svg>
                    Sign in with Google
                  </button>
                )}
              </div>
            </div>
          )}

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            className="flex w-full cursor-pointer items-center gap-3 rounded-2xl px-3 py-3 transition-colors hover:bg-(--card-hover)"
            aria-expanded={menuOpen}
            aria-haspopup="true"
          >
            {isAuthenticated && user?.picture ? (
              <img
                src={user.picture}
                alt={user.name ?? "User"}
                className="h-8 w-8 shrink-0 rounded-full object-cover"
              />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-(--badge-bg) text-xs font-bold text-foreground">
                {isAuthenticated && user ? (user.name ?? user.email ?? "?")[0].toUpperCase() : <User className="h-3.5 w-3.5" />}
              </div>
            )}
            <div className="min-w-0 flex-1 text-left">
              <p className="truncate text-sm font-medium">
                {isAuthenticated && user ? user.name ?? user.email ?? "My Account" : "My Account"}
              </p>
              <p className="truncate text-xs text-(--muted)">
                {isAuthenticated && user?.email ? user.email : "Open account menu"}
              </p>
            </div>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
          </button>
        </div>
      </aside>

      {threadPendingDelete && (
        <div className="ravi-fade-in fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 cursor-pointer bg-black/50 backdrop-blur-sm"
            onClick={() => !isDeletingThread && setThreadPendingDelete(null)}
            aria-label="Close delete confirmation"
          />
          <div
            className="ravi-scale-in relative w-full max-w-md rounded-2xl bg-(--card) p-6"
            role="dialog"
            aria-modal="true"
            aria-label="Delete chat thread"
            style={{ boxShadow: "var(--shadow-lg)" }}
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
                className="cursor-pointer rounded-xl border border-(--border) px-4 py-2 text-sm text-foreground transition-colors hover:bg-(--card-hover) disabled:cursor-not-allowed disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmDelete()}
                disabled={isDeletingThread}
                className="cursor-pointer rounded-xl bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
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
  onEditNameChange: (value: string) => void;
}

function ThreadItem({
  thread,
  isActive,
  isEditing,
  editName,
  onSelect,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onDelete,
  onEditNameChange,
}: ThreadItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showMenu) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showMenu]);

  if (isEditing) {
    return (
      <div className="flex items-center gap-1 rounded-xl bg-(--card) px-3 py-2" style={{ boxShadow: "var(--shadow-sm)" }}>
        <input
          value={editName}
          onChange={(event) => onEditNameChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSaveEdit();
            if (event.key === "Escape") onCancelEdit();
          }}
          className="flex-1 bg-transparent text-xs outline-none"
          autoFocus
        />
        <button
          type="button"
          onClick={onSaveEdit}
          className="cursor-pointer rounded-lg p-1 text-emerald-500 hover:bg-(--card-hover)"
        >
          <Check className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={onCancelEdit}
          className="cursor-pointer rounded-lg p-1 opacity-50 hover:bg-(--card-hover)"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    );
  }

  return (
    <div
      className={`group relative w-full cursor-pointer rounded-xl transition-colors ${isActive ? "bg-(--card-hover) text-foreground" : "text-foreground hover:bg-(--card-hover)"}`}
      onClick={onSelect}
      style={isActive ? { boxShadow: "var(--shadow-sm)" } : undefined}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); } }}
    >
      {/* Text — full width */}
      <div className="w-full py-3 px-3">
        <p
          className={`truncate text-[14px] leading-6 ${isActive ? "font-medium" : ""}`}
          title={thread.name}
        >
          {thread.name}
        </p>
      </div>

      {/* Three-dot button — absolutely overlaid on the right, visible only on hover */}
      <div
        className="absolute right-0 top-0 flex h-full items-center pr-1"
        ref={menuRef}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Gradient fade behind the button */}
        <div
          className={`pointer-events-none absolute right-0 top-0 h-full w-14 rounded-r-xl bg-linear-to-l to-transparent opacity-0 group-hover:opacity-100 transition-opacity from-(--card-hover)`}
        />
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setShowMenu(!showMenu);
          }}
          className={`btn-icon relative z-10 flex cursor-pointer items-center justify-center rounded-lg p-1.5 sm:p-1 transition-all hover:bg-foreground/10 ${showMenu ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>

        {showMenu && (
          <div className="ravi-scale-in absolute right-0 top-full z-[100] mt-1 w-32 overflow-hidden rounded-xl border border-(--border) bg-(--card) shadow-2xl" style={{ transformOrigin: "top right" }}>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowMenu(false);
                onStartEdit();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-(--card-hover)"
            >
              <Pencil className="h-3 w-3" />
              Rename
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowMenu(false);
                onDelete();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-red-400 transition-colors hover:bg-red-500/10"
            >
              <Trash2 className="h-3 w-3" />
              Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
