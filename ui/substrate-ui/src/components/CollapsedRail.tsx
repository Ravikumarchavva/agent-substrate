"use client";

import { FileText, LogOut, MessageSquare, Moon, Settings2, SquarePen, Sun, User } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Meter, Text, Tooltip, TooltipProvider } from "@/design";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { fetchDocQuotaStatus } from "@/lib/api/doc_quota";
import { fetchRateLimitStatus } from "@/lib/api/rate_limit";
import type { SettingsTab } from "./settings/SettingsPanel";
import { confirmAction } from "@/design";

interface Quota {
  used: number;
  limit: number;
}

/** The sidebar collapsed to a strip one control wide: expand and new chat on top; limits, theme and account below. */
export function CollapsedRail({
  onExpand,
  onNewChat,
  onOpenSettings,
}: {
  onExpand: () => void;
  onNewChat: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
}) {
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, loginWithGoogle, logout } = useAuth();
  const [messages, setMessages] = useState<Quota | null>(null);
  const [documents, setDocuments] = useState<Quota | null>(null);

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
      <div className="hidden w-12 shrink-0 flex-col items-center gap-1 border-r border-border bg-card py-3 lg:flex">
        <Tooltip label="Open sidebar" side="right">
          <Button variant="ghost" size="icon" onClick={onExpand} aria-label="Open sidebar">
            <SidebarToggleIcon direction="open" className="size-4" />
          </Button>
        </Tooltip>
        <Tooltip label="New chat" side="right">
          <Button variant="ghost" size="icon" onClick={onNewChat} aria-label="New chat">
            <SquarePen />
          </Button>
        </Tooltip>

        <div className="mt-auto flex flex-col items-center gap-2">
          {messages && <Meter icon={MessageSquare} label="Daily messages" used={messages.used} limit={messages.limit} />}
          {documents && <Meter icon={FileText} label="Daily documents" used={documents.used} limit={documents.limit} />}
          <Tooltip label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} side="right">
            <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">
              {theme === "dark" ? <Sun /> : <Moon />}
            </Button>
          </Tooltip>

          <Menu>
            <MenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Account menu" title={name} className="overflow-hidden rounded-full bg-badge p-0 font-bold text-foreground">
                {isAuthenticated && user?.picture ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={user.picture} alt={name} className="size-full object-cover" />
                ) : isAuthenticated && user ? (
                  name[0].toUpperCase()
                ) : (
                  <User />
                )}
              </Button>
            </MenuTrigger>
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
