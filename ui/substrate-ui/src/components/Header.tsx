"use client";

import { Menu, Settings, HelpCircle, LogOut, Sun, Moon, User, Music, Mail, Check } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { useTheme } from "@/contexts/ThemeContext";
import { useAuth } from "@/contexts/AuthContext";
import { AuthModal } from "./AuthModal";

type Props = {
  onToggleSidebar?: () => void;
  threadName?: string;
};

export function Header({ onToggleSidebar, threadName }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const { theme, toggleTheme } = useTheme();
  const { user, isAuthenticated, googleAuth, spotifyAuth, logout } = useAuth();

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
          {/* Auth Status Badges */}
          {isAuthenticated && (
            <div className="hidden sm:flex items-center gap-2 mr-2">
              {googleAuth && (
                <div className="flex items-center gap-1 px-2 py-1 bg-blue-500/10 border border-blue-500/20 rounded-full text-xs text-blue-400">
                  <Mail className="w-3 h-3" />
                  <span>Google</span>
                </div>
              )}
              {spotifyAuth && (
                <div className="flex items-center gap-1 px-2 py-1 bg-green-500/10 border border-green-500/20 rounded-full text-xs text-green-400">
                  <Music className="w-3 h-3" />
                  <span>Spotify</span>
                </div>
              )}
            </div>
          )}

          {/* Login/Profile Button */}
          <button
            onClick={() => setShowAuthModal(true)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors ${
              isAuthenticated
                ? "bg-emerald-500/10 border border-emerald-500/20 hover:bg-emerald-500/20"
                : "bg-zinc-800 hover:bg-zinc-700 border border-zinc-700"
            }`}
            aria-label={isAuthenticated ? "Profile" : "Login"}
          >
            {user?.picture ? (
              <img
                src={user.picture}
                alt={user.name || "User"}
                className="w-6 h-6 rounded-full"
              />
            ) : (
              <User className="w-4 h-4" />
            )}
            <span className="text-sm font-medium hidden sm:inline">
              {user?.name?.split(" ")[0] || "Login"}
            </span>
          </button>

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
                <button 
                  onClick={() => {
                    setShowAuthModal(true);
                    setShowMenu(false);
                  }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-[var(--card-hover)] transition-colors"
                >
                  <User className="w-4 h-4" />
                  {isAuthenticated ? "Account" : "Login"}
                </button>
                <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-[var(--card-hover)] transition-colors">
                  <Settings className="w-4 h-4" />
                  Settings
                </button>
                <button className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-[var(--card-hover)] transition-colors">
                  <HelpCircle className="w-4 h-4" />
                  Help & FAQ
                </button>
                {isAuthenticated && (
                  <>
                    <div className="border-t border-[var(--border)] my-1" />
                    <button 
                      onClick={async () => {
                        await logout();
                        setShowMenu(false);
                      }}
                      className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-500 hover:bg-[var(--card-hover)] transition-colors"
                    >
                      <LogOut className="w-4 h-4" />
                      Log out
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Auth Modal */}
      <AuthModal isOpen={showAuthModal} onClose={() => setShowAuthModal(false)} />
    </header>
  );
}
