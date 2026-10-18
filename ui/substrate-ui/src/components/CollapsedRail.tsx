"use client";

import { ArrowLeft, Bell, CalendarClock, FileText, MessageSquare, MessagesSquare, LogOut, Moon, Plus, Search, Settings2, Sun, User, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, cn, Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Text, Tooltip, TooltipProvider, confirmAction } from "@/design";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { SubstrateMark } from "@/components/SubstrateMark";
import { Avatar } from "@/components/groups/Avatar";
import { buildChatItems, isOpenChat } from "@/components/chats/items";
import { useAuth } from "@/contexts/AuthContext";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { useTheme } from "@/contexts/ThemeContext";
import { fetchDocQuotaStatus } from "@/lib/api/doc_quota";
import { fetchRateLimitStatus } from "@/lib/api/rate_limit";
import { getVisibleSettingsTabGroups, type SettingsTab } from "./settings/SettingsPanel";

/** Every item in the rail sits on the same 44px square, so icons, meters and the avatar share one grid and one hover. */
const SLOT = "relative flex size-11 shrink-0 items-center justify-center rounded-lg text-muted transition-colors";
/** The same slot for the interactive ones: a design `Button`, restyled to the rail's square so focus, disabled and press behave like every button. */
const BUTTON = "size-11 h-11! w-11! rounded-xl p-0 text-muted";

/** A count as a pill on the corner of an icon, "9+" past nine. */
function Count({ n }: { n: number }) {
  return <span className="absolute -right-1.5 -top-1 min-w-4 rounded-full bg-accent px-1 text-center text-2xs font-semibold leading-4 text-accent-foreground ring-2 ring-card">{n > 9 ? "9+" : n}</span>;
}

/**
 * One destination in the rail: its icon with its name under it, a tinted pill when its page is open, and a count when something is waiting.
 * `compact` drops the name (the settings pages, whose names are long) and keeps it as a tooltip.
 */
function RailButton({ icon: Icon, label, onClick, active, count, compact, highlight }: { icon: LucideIcon; label: string; onClick: () => void; active?: boolean; count?: number; compact?: boolean; highlight?: boolean }) {
  return (
    <Tooltip label={label} side="right">
      <Button
        variant="ghost"
        onClick={onClick}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={cn("relative h-auto! w-full flex-col gap-1 rounded-xl px-1 py-2 text-muted", compact && "size-11 h-11! w-11 py-0", active ? "bg-accent/12 text-accent hover:bg-accent/12" : highlight ? "text-accent-2 hover:bg-accent-2/12" : "hover:text-foreground")}
      >
        <span className="relative">
          <Icon aria-hidden />
          {count !== undefined && count > 0 && <Count n={count} />}
        </span>
        {!compact && <span className="w-full truncate text-center text-2xs font-medium leading-none">{label}</span>}
      </Button>
    </Tooltip>
  );
}

/** A chat you pinned, as its avatar: a ring when it is the one open, a count for what you have not read, a green dot while it is typing. */
function RailChat({ name, avatar, active, unread, typing, onClick }: { name: string; avatar: string | null; active: boolean; unread: number; typing: boolean; onClick: () => void }) {
  return (
    <Tooltip label={typing ? `${name} is typing…` : name} side="right">
      <Button variant="ghost" onClick={onClick} aria-label={name} aria-current={active ? "page" : undefined} className={cn("relative size-11 h-11! rounded-full p-0", active && "ring-2 ring-accent")}>
        <Avatar name={name} src={avatar} className="size-9 text-sm" />
        {unread > 0 && !active && <Count n={unread} />}
        {typing && <span className="absolute bottom-0.5 right-0.5 size-2.5 rounded-full bg-success ring-2 ring-card" aria-hidden />}
      </Button>
    </Tooltip>
  );
}

/** A daily limit as an icon with a thin ring that fills as it is used (calm until 75%, then amber, then red). Same slot as the buttons. */
function RailMeter({ icon: Icon, label, used, limit }: { icon: LucideIcon; label: string; used: number; limit: number }) {
  const spent = limit > 0 ? Math.min(1, used / limit) : 0;
  const tone = spent >= 1 ? "text-danger" : spent >= 0.75 ? "text-warning" : "text-accent";
  const circumference = 2 * Math.PI * 19;
  return (
    <Tooltip label={`${label}: ${Math.max(0, limit - used)} of ${limit} left`} side="right">
      <div role="img" aria-label={`${label}: ${used} of ${limit} used`} className={SLOT}>
        <svg className="absolute inset-0 size-11 -rotate-90" viewBox="0 0 44 44" aria-hidden>
          <circle cx="22" cy="22" r="19" fill="none" strokeWidth="2" className="stroke-border" />
          <circle cx="22" cy="22" r="19" fill="none" strokeWidth="2" strokeLinecap="round" className={`stroke-current ${tone}`} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - spent)} />
        </svg>
        <Icon className="size-5" aria-hidden />
      </div>
    </Tooltip>
  );
}

/**
 * The sidebar collapsed to an icon rail, the way ChatGPT does it: the logo on top (it turns into "open sidebar" when you point at it),
 * then new chat, search and the pages, with theme and your account at the bottom. Everything else is in the expanded sidebar.
 */
export function CollapsedRail({
  onExpand,
  onNewChat,
  onSearch,
  onOpenSettings,
  onOpenScheduled,
  onOpenAgents,
  onOpenNotifications,
  isAgentsOpen,
  isScheduledOpen,
  isNotificationsOpen,
  unreadCount,
  agents = [],
  groups = [],
  openGroupId,
  openAgentId = null,
  onOpenAgent,
  onOpenGroup,
  mode = "chat",
  settingsTab,
  onSelectSettingsTab,
  onBackToChat,
}: {
  onExpand: () => void;
  onNewChat: () => void;
  onSearch: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
  onOpenScheduled: () => void;
  onOpenAgents: () => void;
  isAgentsOpen?: boolean;
  onOpenNotifications: () => void;
  isScheduledOpen?: boolean;
  isNotificationsOpen?: boolean;
  unreadCount?: number;
  /** The chats you pin appear here as avatars. */
  agents?: Agent[];
  groups?: Group[];
  openGroupId?: string | null;
  /** The agent whose conversation is open, to mark its row. */
  openAgentId?: string | null;
  onOpenAgent?: (agent: Agent) => void;
  onOpenGroup?: (groupId: string) => void;
  /** In settings the rail lists the settings pages (as the open sidebar does), not the chat pages. */
  mode?: "chat" | "settings";
  settingsTab?: SettingsTab;
  onSelectSettingsTab?: (tab: SettingsTab) => void;
  onBackToChat?: () => void;
}) {
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, isAdmin, loginWithGoogle, logout } = useAuth();
  const [hoverLogo, setHoverLogo] = useState(false);
  const [messages, setMessages] = useState<{ used: number; limit: number } | null>(null);
  const [documents, setDocuments] = useState<{ used: number; limit: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [rate, docs] = await Promise.all([fetchRateLimitStatus(), fetchDocQuotaStatus()]);
      if (cancelled) return;
      setMessages(rate?.enabled ? { used: rate.used, limit: rate.limit } : null);
      setDocuments(docs?.enabled ? { used: docs.used, limit: docs.limit } : null);
    }
    void load();
    const id = setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const pinnedChats = useMemo(() => buildChatItems(agents, groups).filter((i) => i.pinned), [agents, groups]);
  const chatUnread = groups.reduce((n, g) => n + g.unread, 0);

  const name = isAuthenticated && user ? (user.name ?? user.email ?? "My Account") : "My Account";

  return (
    <TooltipProvider>
      <div className="hidden w-18 shrink-0 flex-col items-center gap-1 border-r border-border bg-card px-1 py-3 lg:flex">
        <Tooltip label="Open sidebar" side="right">
          <Button
            variant="ghost"
            size="icon"
            onClick={onExpand}
            onMouseEnter={() => setHoverLogo(true)}
            onMouseLeave={() => setHoverLogo(false)}
            onFocus={() => setHoverLogo(true)}
            onBlur={() => setHoverLogo(false)}
            aria-label="Open sidebar"
            className={BUTTON}
          >
            {hoverLogo ? <SidebarToggleIcon direction="open" /> : <SubstrateMark className="size-5" />}
          </Button>
        </Tooltip>

        <div className="mt-3 flex w-full flex-col items-center gap-1">
          {mode === "settings" ? (
            <>
              <RailButton compact icon={ArrowLeft} label="Back to chats" onClick={() => onBackToChat?.()} />
              {getVisibleSettingsTabGroups(isAdmin).map((group, i) => (
                <div key={group.title} className={i === 0 ? "flex flex-col items-center gap-1" : "mt-1 flex flex-col items-center gap-1 border-t border-border pt-2"}>
                  {group.items.map((item) => (
                    <RailButton compact key={item.id} icon={item.icon} label={item.label} onClick={() => onSelectSettingsTab?.(item.id)} active={item.id === settingsTab} />
                  ))}
                </div>
              ))}
            </>
          ) : (
            <>
              <RailButton highlight icon={Plus} label="New" onClick={onNewChat} />
              <RailButton icon={Search} label="Search" onClick={onSearch} />
              <RailButton icon={MessagesSquare} label="Agents" onClick={onOpenAgents} active={isAgentsOpen || !!openGroupId} count={chatUnread} />
              <RailButton icon={CalendarClock} label="Schedule" onClick={onOpenScheduled} active={isScheduledOpen} />
              <RailButton icon={Bell} label="Alerts" onClick={onOpenNotifications} active={isNotificationsOpen} count={unreadCount} />
              {pinnedChats.length > 0 && (
                <div className="mt-1 flex flex-col items-center gap-1.5 border-t border-border pt-2.5">
                  {pinnedChats.map((chat) => {
                    const agent = chat.kind === "agent" ? agents.find((a) => a.id === chat.id) : undefined;
                    return (
                      <RailChat
                        key={chat.key}
                        name={chat.name}
                        avatar={chat.avatar}
                        unread={chat.unread}
                        typing={chat.typing.length > 0}
                        active={isOpenChat(chat, { agentId: openAgentId, groupId: openGroupId })}
                        onClick={() => (chat.kind === "group" ? onOpenGroup?.(chat.id) : agent && onOpenAgent?.(agent))}
                      />
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        <div className="mt-auto flex flex-col items-center gap-1">
          {messages && <RailMeter icon={MessageSquare} label="Daily messages" used={messages.used} limit={messages.limit} />}
          {documents && <RailMeter icon={FileText} label="Daily documents" used={documents.used} limit={documents.limit} />}
          <RailButton compact icon={theme === "dark" ? Sun : Moon} label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} onClick={toggleTheme} />

          {/* a gap, and a hairline, between the settings of the app above and who you are below */}
          <div className="my-1 h-px w-6 bg-border" aria-hidden />
          <Menu>
            <Tooltip label={name} side="right">
              <MenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="Account menu" className={BUTTON}>
                  <span className="flex size-7 items-center justify-center overflow-hidden rounded-full bg-badge text-sm font-bold text-foreground">
                    {isAuthenticated && user?.picture ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={user.picture} alt="" className="size-full object-cover" />
                    ) : isAuthenticated && user ? (
                      name[0].toUpperCase()
                    ) : (
                      <User className="size-4" />
                    )}
                  </span>
                </Button>
              </MenuTrigger>
            </Tooltip>
            <MenuContent side="right" align="end" className="w-56">
              {isAuthenticated && user && (
                <>
                  <MenuLabel>
                    <Text className="truncate font-medium">{user.name ?? "User"}</Text>
                    <Text size="small" tone="muted" className="truncate">{user.email ?? ""}</Text>
                  </MenuLabel>
                  <MenuSeparator />
                </>
              )}
              <MenuItem onSelect={() => onOpenSettings("general")}>
                <Settings2 /> Settings
              </MenuItem>
              <MenuSeparator />
              {isAuthenticated ? (
                <MenuItem
                  tone="danger"
                  onSelect={async () => {
                    if (!(await confirmAction({ title: "Sign out?", description: "Spotify stays connected until you disconnect it in Apps.", confirmLabel: "Sign out" }))) return;
                    await logout();
                  }}
                >
                  <LogOut /> Sign out
                </MenuItem>
              ) : (
                <MenuItem onSelect={loginWithGoogle}>
                  <User /> Sign in with Google
                </MenuItem>
              )}
            </MenuContent>
          </Menu>
        </div>
      </div>
    </TooltipProvider>
  );
}
