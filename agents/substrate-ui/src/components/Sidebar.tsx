"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import type { Thread } from "@/types";
import {
  ArrowLeft,
  Bell,
  CalendarClock,
  Check,
  ChevronsUpDown,
  LogOut,
  Moon, MoreHorizontal,
  Pencil,
  Search,
  Settings2,
  Eye,
  ShieldCheck,
  ShieldQuestion,
  Bot,
  Plus,
  SquarePen,
  Sun,
  Trash2,
  User,
  X,
  type LucideIcon,
} from "lucide-react";
import { SubstrateMark } from "@/components/SubstrateMark";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { Avatar } from "@/components/groups/Avatar";
import { typingLine } from "@/components/groups/text";
import { buildChatItems, isOpenChat } from "@/components/chats/items";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { useTheme } from "@/contexts/ThemeContext";
import { useAuth } from "@/contexts/AuthContext";
import { useDisplayName } from "@/lib/display-name";
import { getVisibleSettingsTabGroups, type SettingsTab } from "./settings/SettingsPanel";
import { Button, NavItem, cn, confirmAction } from "@/design";
import { shortTime } from "@/lib/short-time";
import { ThreadList } from "@/components/ThreadList";

type SidebarMode = "chat" | "settings";

type Props = {
  threads: Thread[];
  currentThreadId?: string | null;
  onNewChat: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => Promise<void> | void;
  onRenameThread: (threadId: string, newName: string) => void;
  onPinThread: (threadId: string, pinned: boolean) => void;
  onArchiveThread: (threadId: string, archived: boolean) => void;
  onLoadMore: () => void;
  onToggleArchived: (archived: boolean) => void;
  hasMore: boolean;
  showArchived: boolean;
  onCollapse?: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
  onOpenScheduled?: () => void;
  isScheduledOpen?: boolean;
  scheduledCount?: number;
  onOpenAgents?: () => void;
  isAgentsOpen?: boolean;
  /** The user's agents: each is a conversation of its own, listed like a contact. */
  agents?: Agent[];
  onOpenAgent?: (agent: Agent) => void;
  /** The user's groups: agents and the user in one conversation. */
  groups?: Group[];
  openGroupId?: string | null;
  /** The agent whose conversation is open, to mark its row. */
  openAgentId?: string | null;
  onOpenGroup?: (groupId: string | null) => void;
  onOpenNotifications?: () => void;
  /** Open another agent's account, read only. */
  onViewAs?: () => void;
  isNotificationsOpen?: boolean;
  unreadCount?: number;
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

export function Sidebar({
  threads,
  currentThreadId,
  onNewChat,
  onSelectThread,
  onDeleteThread,
  onRenameThread,
  onPinThread,
  onArchiveThread,
  onLoadMore,
  onToggleArchived,
  hasMore,
  showArchived,
  onCollapse,
  onOpenSettings,
  onOpenScheduled,
  isScheduledOpen,
  scheduledCount,
  onOpenAgents,
  isAgentsOpen,
  agents = [],
  onOpenAgent,
  groups = [],
  openGroupId,
  openAgentId = null,
  onOpenGroup,
  onOpenNotifications,
  onViewAs,
  isNotificationsOpen,
  unreadCount,
  mode = "chat",
  settingsTab,
  onSelectSettingsTab,
  onBackToChat,
}: Props) {
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, isAdmin, loginWithGoogle, logout } = useAuth();
  const shownName = useDisplayName(user?.name);
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

  const settingsGroups = useMemo(() => getVisibleSettingsTabGroups(isAdmin), [isAdmin]);

  // The chats you pinned (at most five) are the only agents and groups the sidebar lists; the rest are found under Agents.
  const pinnedRows = useMemo(
    () =>
      buildChatItems(agents, groups)
        .filter((i) => i.pinned)
        .map((i) => ({
          key: i.key,
          name: i.name,
          avatar: i.avatar,
          title: i.name,
          preview: i.preview,
          time: i.time,
          unread: i.unread,
          typing: i.typing.length > 0 ? (i.kind === "agent" ? "typing…" : typingLine(i.typing)) : "",
          active: isOpenChat(i, { agentId: openAgentId, groupId: openGroupId }),
          open: () => (i.kind === "group" ? onOpenGroup?.(i.id) : onOpenAgent?.(agents.find((a) => a.id === i.id)!)),
        })),
    [agents, groups, openGroupId, openAgentId, onOpenGroup, onOpenAgent],
  );

  const chatQuickActions = [
    { label: "New chat", icon: SquarePen, onClick: onNewChat },
    {
      label: "Agents",
      icon: Bot,
      onClick: onOpenAgents || (() => {}),
      isActive: isAgentsOpen || !!openGroupId,
      badge: groups.reduce((n, g) => n + g.unread, 0),
    },
    {
      label: "Scheduled",
      icon: CalendarClock,
      onClick: onOpenScheduled || (() => {}),
      isActive: isScheduledOpen,
      badge: scheduledCount,
    },
    {
      label: "Notifications",
      icon: Bell,
      onClick: onOpenNotifications || (() => {}),
      isActive: isNotificationsOpen,
      badge: unreadCount,
    },
  ];

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
        className="flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-border bg-card shadow-2xl lg:shadow-none"
        suppressHydrationWarning
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <div className="flex items-center justify-between px-2 py-2">
          <div className="flex items-center gap-3 ml-2">
            <SubstrateMark className="h-6 w-6 text-foreground" />
          </div>
          {onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              title="Collapse sidebar"
              className="btn-icon flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-transparent text-muted transition-colors hover:border-border hover:bg-background hover:text-foreground"
             aria-label="Collapse sidebar">
              <SidebarToggleIcon direction="close" className="h-5 w-5" />
            </button>
          )}
        </div>

        <div className="px-2 pt-2">
          <div className="space-y-1">
            {isSettingsMode ? (
              <NavItem icon={ArrowLeft} onClick={() => onBackToChat?.()}>Back to chats</NavItem>
            ) : (
              chatQuickActions.map((action) => (
                <NavItem key={action.label} icon={action.icon} onClick={action.onClick} active={action.isActive} badge={action.badge}>{action.label}</NavItem>
              ))
            )}
          </div>
        </div>

        {isSettingsMode ? (
          <div className="flex-1 overflow-y-auto px-2 py-4">
            <div className="space-y-4">
              {settingsGroups.map((group) => (
                <div key={group.title}>
                  <p className="px-1 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-muted">
                    {group.title}
                  </p>
                  <div className="space-y-0.5">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const active = item.id === settingsTab;
                      return (
                        <NavItem key={item.id} icon={Icon} active={active} onClick={() => onSelectSettingsTab?.(item.id)}>
                          {item.label}
                        </NavItem>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="px-2 pb-2 pt-2">
              <div className="flex h-control-lg items-center gap-3 rounded-lg border border-border bg-background px-3 text-sm text-muted">
                <Search className="size-4 shrink-0" />
                <input
                  ref={searchInputRef}
                  data-thread-search
                  aria-label="Search threads"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search threads"
                  className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    aria-label="Clear search"
                    className="btn-icon cursor-pointer rounded-lg p-1 hover:bg-card-hover"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>

            {pinnedRows.length > 0 && (
              <div className="px-3 pb-2">
                <p className="px-1 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-muted">Pinned</p>
                <div className="space-y-0.5">
                  {pinnedRows.map((row) => (
                    <Button
                      key={row.key}
                      variant="ghost"
                      onClick={row.open}
                      aria-current={row.active ? "page" : undefined}
                      title={row.title}
                      className={cn("h-auto! min-h-0 w-full justify-start gap-3 whitespace-normal rounded-lg px-2.5 py-2 text-left font-normal", row.active ? "bg-accent/12" : "hover:bg-card-hover")}
                    >
                      <Avatar name={row.name} src={row.avatar} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-sm font-medium text-foreground">{row.name}</span>
                          <span className="shrink-0 text-2xs text-muted">{shortTime(row.time)}</span>
                        </span>
                        <span className="flex items-center justify-between gap-2">
                          {row.typing ? <span className="block truncate text-xs font-medium text-success">{row.typing}</span> : <span className="block truncate text-xs text-muted">{row.preview}</span>}
                          {row.unread > 0 && !row.active && <span className="shrink-0 rounded-full bg-accent px-1.5 text-2xs font-semibold text-accent-foreground">{row.unread}</span>}
                        </span>
                      </span>
                    </Button>
                  ))}
                </div>
              </div>
            )}

            <ThreadList
              threads={threads}
              currentThreadId={currentThreadId}
              search={search}
              showArchived={showArchived}
              hasMore={hasMore}
              onSelect={onSelectThread}
              onRename={onRenameThread}
              onDelete={onDeleteThread}
              onPin={onPinThread}
              onArchive={onArchiveThread}
              onLoadMore={onLoadMore}
              onToggleArchived={onToggleArchived}
            />
          </>
        )}

        <div className="mt-auto px-2 pb-3 pt-2" ref={menuRef}>
          {menuOpen && (
            <div className="substrate-pop-in mb-1.5 overflow-hidden rounded-2xl bg-card" style={{ boxShadow: "var(--shadow-lg)", transformOrigin: "bottom center" }}>
              {isAuthenticated && user && (
                <div className="border-b border-border px-2.5 py-3">
                  <div className="truncate text-sm font-medium">{shownName || "User"}</div>
                  <div className="mt-0.5 truncate text-xs text-muted">{user.email ?? ""}</div>
                  {isAdmin && (
                    <span className="mt-1.5 inline-flex items-center gap-1 text-xs text-emerald-500">
                      <ShieldCheck className="h-3 w-3" /> Admin
                    </span>
                  )}
                </div>
              )}

              <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                <span className="text-sm">{theme === "dark" ? "Dark mode" : "Light mode"}</span>
                <button
                  type="button"
                  onClick={toggleTheme}
                  className="flex items-center justify-center cursor-pointer rounded-xl p-1.5 transition-colors hover:bg-card-hover"
                  aria-label="Toggle theme"
                >
                  {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                </button>
              </div>

              {onViewAs && agents.length > 0 && (
                <div className="py-1">
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setMenuOpen(false);
                      onViewAs();
                    }}
                    className="h-auto! w-full justify-start gap-3 rounded-none px-4 py-2.5 text-sm font-normal"
                  >
                    <Eye className="text-muted" />
                    View an agent&apos;s account
                  </Button>
                </div>
              )}

              <div className="py-1">
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onOpenSettings("general");
                  }}
                  className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-card-hover"
                >
                  <Settings2 className="h-4 w-4 text-muted" />
                  Settings
                </button>
              </div>

              <div className="border-t border-border py-1">
                {isAuthenticated ? (
                  <button
                    type="button"
                    onClick={async () => {
                      setMenuOpen(false);
                      const confirmed = await confirmAction({
        title: "Sign out?",
        description: "Spotify stays connected until you disconnect it in Apps.",
        confirmLabel: "Sign out",
      });
                      if (!confirmed) return;
                      await logout();
                    }}
                    className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm text-red-400 transition-colors hover:bg-card-hover"
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
                    className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-card-hover"
                  >
                    <svg data-icon className="shrink-0" viewBox="0 0 24 24" aria-hidden="true">
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
            className="flex w-full cursor-pointer items-center gap-3 rounded-2xl px-3 py-3 transition-colors hover:bg-card-hover"
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
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-badge text-xs font-bold text-foreground">
                {isAuthenticated && user ? (shownName || user.email || "?")[0].toUpperCase() : <User className="h-3.5 w-3.5" />}
              </div>
            )}
            <div className="min-w-0 flex-1 text-left">
              <p className="truncate text-sm font-medium">
                {isAuthenticated && user ? shownName || user.email || "My Account" : "My Account"}
              </p>
              <p className="truncate text-xs text-muted">
                {isAuthenticated && user?.email ? user.email : "Open account menu"}
              </p>
            </div>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted" />
          </button>
        </div>
      </aside>

    </>
  );
}
