"use client";

import { PanelLeftOpen } from "lucide-react";
import type { SettingsTab } from "./SettingsPanel";

interface HeaderProps {
  onToggleSidebar?: () => void;
  sidebarOpen?: boolean;
  threadName?: string;
  onOpenSettings: (tab?: SettingsTab) => void;
}

export function Header({
  onToggleSidebar,
  threadName,
}: HeaderProps) {

  return (
    <header
      className="flex items-center justify-between px-3 py-2 bg-background border-b border-(--border)"
      suppressHydrationWarning
    >
      {/* Left: sidebar toggle + thread name */}
      <div className="flex items-center gap-2 min-w-0">
        {onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            className="p-1.5 hover:bg-(--card-hover) rounded-lg shrink-0 cursor-pointer"
            aria-label="Open sidebar"
          >
            <PanelLeftOpen
              className="w-4 h-4"
              style={{ color: "var(--muted)" }}
            />
          </button>
        )}
        <span className="text-sm font-medium truncate text-(--muted)">
          {threadName || "New Chat"}
        </span>
      </div>
    </header>
  );
}