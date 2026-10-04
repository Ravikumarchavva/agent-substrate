"use client";

import { ArrowLeft, Bell, CalendarClock, FileText, MessageSquare, LogOut, Moon, Search, Settings2, ShieldQuestion, SquarePen, Sun, User, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Text, Tooltip, TooltipProvider, confirmAction } from "@/design";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { SubstrateMark } from "@/components/SubstrateMark";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { fetchDocQuotaStatus } from "@/lib/api/doc_quota";
import { fetchRateLimitStatus } from "@/lib/api/rate_limit";
import { getVisibleSettingsTabGroups, type SettingsTab } from "./settings/SettingsPanel";

/** Every item in the rail sits on the same 44px square, so icons, meters and the avatar share one grid and one hover. */
const SLOT = "relative flex size-11 shrink-0 items-center justify-center rounded-lg text-muted transition-colors";
/** The same slot for the interactive ones: a design `Button`, restyled to the rail's square so focus, disabled and press behave like every button. */
const BUTTON = "size-11 rounded-xl p-0 text-muted [&_svg]:size-6";

/** One icon in the rail: the slot, a tooltip for its name, a violet tint when its page is open, and a dot when something is waiting. */
function RailButton({ icon: Icon, label, onClick, active, count }: { icon: LucideIcon; label: string; onClick: () => void; active?: boolean; count?: number }) {
  return (
    <Tooltip label={label} side="right">
      <Button
        variant="ghost"
        size="icon"
        onClick={onClick}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={`${BUTTON} relative ${active ? "bg-accent/12 text-accent hover:bg-accent/12" : ""}`}
      >
        <Icon aria-hidden />
        {count !== undefined && count > 0 && <span className="absolute right-2.5 top-2.5 size-2 rounded-full bg-accent-2 ring-2 ring-card" aria-label={`${count} waiting`} />}
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
  onOpenApprovals,
  onOpenNotifications,
  isScheduledOpen,
  isApprovalsOpen,
  isNotificationsOpen,
  approvalsCount,
  unreadCount,
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
  onOpenApprovals: () => void;
  onOpenNotifications: () => void;
  isScheduledOpen?: boolean;
  isApprovalsOpen?: boolean;
  isNotificationsOpen?: boolean;
  approvalsCount?: number;
  unreadCount?: number;
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

  const name = isAuthenticated && user ? (user.name ?? user.email ?? "My Account") : "My Account";

  return (
    <TooltipProvider>
      <div className="hidden w-16 shrink-0 flex-col items-center gap-1 border-r border-border bg-card py-3 lg:flex">
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
            {hoverLogo ? <SidebarToggleIcon direction="open" /> : <SubstrateMark className="size-6" />}
          </Button>
        </Tooltip>

        <div className="mt-3 flex flex-col items-center gap-1">
          {mode === "settings" ? (
            <>
              <RailButton icon={ArrowLeft} label="Back to chats" onClick={() => onBackToChat?.()} />
              {getVisibleSettingsTabGroups(isAdmin).map((group, i) => (
                <div key={group.title} className={i === 0 ? "flex flex-col items-center gap-1" : "mt-1 flex flex-col items-center gap-1 border-t border-border pt-2"}>
                  {group.items.map((item) => (
                    <RailButton key={item.id} icon={item.icon} label={item.label} onClick={() => onSelectSettingsTab?.(item.id)} active={item.id === settingsTab} />
                  ))}
                </div>
              ))}
            </>
          ) : (
            <>
          <RailButton icon={SquarePen} label="New chat" onClick={onNewChat} />
          <RailButton icon={Search} label="Search" onClick={onSearch} />
          <RailButton icon={CalendarClock} label="Scheduled" onClick={onOpenScheduled} active={isScheduledOpen} />
          <RailButton icon={ShieldQuestion} label="Approvals" onClick={onOpenApprovals} active={isApprovalsOpen} count={approvalsCount} />
          <RailButton icon={Bell} label="Notifications" onClick={onOpenNotifications} active={isNotificationsOpen} count={unreadCount} />
            </>
          )}
        </div>

        <div className="mt-auto flex flex-col items-center gap-1">
          {messages && <RailMeter icon={MessageSquare} label="Daily messages" used={messages.used} limit={messages.limit} />}
          {documents && <RailMeter icon={FileText} label="Daily documents" used={documents.used} limit={documents.limit} />}
          <RailButton icon={theme === "dark" ? Sun : Moon} label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} onClick={toggleTheme} />

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
