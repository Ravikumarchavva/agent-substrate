"use client";

import { Menu, Settings, HelpCircle, LogOut } from "lucide-react";
import { useState, useRef, useEffect } from "react";

type Props = {
  onToggleSidebar?: () => void;
  threadName?: string;
};

export function Header({ onToggleSidebar, threadName }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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
    <header className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm sticky top-0 z-10">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          {onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              className="p-2 hover:bg-zinc-800 rounded-lg lg:hidden"
              aria-label="Toggle sidebar"
            >
              <Menu className="w-5 h-5" />
            </button>
          )}
          <h1 className="text-lg font-semibold">
            {threadName || "Agent Chat"}
          </h1>
        </div>

        {/* User Menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowMenu(!showMenu)}
            className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
            aria-label="User menu"
          >
            <Settings className="w-5 h-5" />
          </button>

          {showMenu && (
            <div className="absolute right-0 mt-2 w-56 bg-zinc-900 border border-zinc-800 rounded-lg shadow-lg py-1">
              <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-zinc-800 transition-colors">
                <Settings className="w-4 h-4" />
                Settings
              </button>
              <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-zinc-800 transition-colors">
                <HelpCircle className="w-4 h-4" />
                Help & FAQ
              </button>
              <div className="border-t border-zinc-800 my-1" />
              <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-400 hover:bg-zinc-800 transition-colors">
                <LogOut className="w-4 h-4" />
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
