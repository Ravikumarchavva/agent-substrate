"use client";

import { Menu, Settings, HelpCircle, LogOut, Sun, Moon } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { useTheme } from "@/contexts/ThemeContext";

type Props = {
  onToggleSidebar?: () => void;
  threadName?: string;
};

export function Header({ onToggleSidebar, threadName }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const { theme, toggleTheme } = useTheme();

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };

    if (showMenu) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [showMenu]);

  return (
    <header className="border-b border-[var(--border)] bg-[var(--background)] sticky top-0 z-10" suppressHydrationWarning>
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          {onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              className="p-2 hover:bg-[var(--card-hover)] rounded-lg lg:hidden"
              aria-label="Toggle sidebar"
            >
              <Menu className="w-5 h-5" />
            </button>
          )}
          <h1 className="text-lg font-semibold">
            {threadName || "Agent Chat"}
          </h1>
        </div>

        <div className="flex items-center gap-2">
          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            className="p-2 hover:bg-[var(--card-hover)] rounded-lg transition-colors"
            aria-label="Toggle theme"
          >
            {theme === "dark" ? (
              <Sun className="w-5 h-5" />
            ) : (
              <Moon className="w-5 h-5" />
            )}
          </button>

          {/* User Menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setShowMenu(!showMenu)}
              className="p-2 hover:bg-[var(--card-hover)] rounded-lg transition-colors"
              aria-label="User menu"
            >
              <Settings className="w-5 h-5" />
            </button>

            {showMenu && (
              <div className="absolute right-0 mt-2 w-56 bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-lg py-1">
                <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-[var(--card-hover)] transition-colors">
                  <Settings className="w-4 h-4" />
                  Settings
                </button>
                <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-[var(--card-hover)] transition-colors">
                  <HelpCircle className="w-4 h-4" />
                  Help & FAQ
                </button>
                <div className="border-t border-[var(--border)] my-1" />
                <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-500 hover:bg-[var(--card-hover)] transition-colors">
                  <LogOut className="w-4 h-4" />
                  Log out
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
