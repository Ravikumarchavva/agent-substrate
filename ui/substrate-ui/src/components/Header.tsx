"use client";

import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";

interface HeaderProps {
  onOpenMobileSidebar?: () => void;
  onOpenDesktopSidebar?: () => void;
  threadName?: string;
}

export function Header({
  onOpenMobileSidebar,
  onOpenDesktopSidebar,
  threadName,
}: HeaderProps) {

  return (
    <header
      className="flex items-center bg-background px-3 py-2 sm:px-4"
      suppressHydrationWarning
    >
      {/* Left: sidebar toggle + thread name */}
      <div className="flex items-center gap-2 min-w-0">
        {onOpenMobileSidebar && (
          <button
            onClick={onOpenMobileSidebar}
            className="shrink-0 rounded-xl p-1.5 hover:bg-(--card-hover) cursor-pointer lg:hidden"
            aria-label="Open sidebar"
          >
            <SidebarToggleIcon direction="open" className="h-4 w-4 text-(--muted)" />
          </button>
        )}
        {onOpenDesktopSidebar && (
          <button
            onClick={onOpenDesktopSidebar}
            className="hidden shrink-0 rounded-xl p-1.5 hover:bg-(--card-hover) cursor-pointer lg:inline-flex"
            aria-label="Restore sidebar"
          >
            <SidebarToggleIcon direction="open" className="h-4 w-4 text-(--muted)" />
          </button>
        )}
        <span className="text-[13px] font-medium truncate text-(--muted)">
          {threadName || "New Chat"}
        </span>
      </div>
    </header>
  );
}