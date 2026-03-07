"use client";

import { PanelLeftOpen, Settings, Sun, Moon } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { useTheme } from "@/contexts/ThemeContext";

type Props = {
  onToggleSidebar?: () => void;
  sidebarOpen?: boolean;
  threadName?: string;
};

export function Header({ onToggleSidebar, sidebarOpen, threadName }: Props) {
  const { theme, toggleTheme } = useTheme();

  return (
    <header
      className="flex items-center justify-between px-3 py-2 bg-background border-b border-(--border)"
      suppressHydrationWarning
    >
      {/* Left: sidebar toggle + title */}
      <div className="flex items-center gap-2 min-w-0">
        {onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            className="p-1.5 hover:bg-(--card-hover) rounded-lg shrink-0"
            aria-label="Open sidebar"
          >
            <PanelLeftOpen className="w-4 h-4" style={{ color: "var(--muted)" }} />
          </button>
        )}
        <span className="text-sm font-medium truncate text-(--muted)">
          {threadName || "New Chat"}
        </span>
      </div>

      {/* Right: controls */}
      <div className="flex items-center gap-1">
        <button
          onClick={toggleTheme}
          className="p-1.5 hover:bg-(--card-hover) rounded-lg transition-colors"
          aria-label="Toggle theme"
        >
          {theme === "dark" ? (
            <Sun className="w-4 h-4" />
          ) : (
            <Moon className="w-4 h-4" />
          )}
        </button>
        <button
          className="p-1.5 hover:bg-(--card-hover) rounded-lg transition-colors"
          aria-label="Settings"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}