"use client";

import { useState, useRef, useEffect } from "react";
import { LogOut, Settings, User, ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import type { SettingsTab } from "./SettingsPanel";

interface AccountMenuProps {
  onOpenSettings: (tab?: SettingsTab) => void;
}

export function AccountMenu({ onOpenSettings }: AccountMenuProps) {
  const { user, isAuthenticated, isAdmin, loginWithGoogle, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  return (
    <div className="relative" ref={menuRef}>
      {/* Avatar trigger */}
      <button
        onClick={() => setIsOpen((o) => !o)}
        className="flex items-center justify-center w-7 h-7 rounded-full overflow-hidden hover:ring-2 hover:ring-(--accent)/60 transition-all cursor-pointer"
        aria-label="Account menu"
        aria-expanded={isOpen}
        aria-haspopup="true"
      >
        {isAuthenticated && user?.picture ? (
          <img
            src={user.picture}
            alt={user.name ?? "User"}
            className="w-7 h-7 rounded-full object-cover"
          />
        ) : (
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white"
            style={{ background: "var(--accent)" }}
          >
            {isAuthenticated && user
              ? (user.name ?? user.email ?? "U")[0].toUpperCase()
              : <User className="w-3.5 h-3.5" />}
          </div>
        )}
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div
          className="absolute right-0 top-full mt-2 w-56 bg-(--card) border border-(--border) rounded-xl shadow-xl overflow-hidden z-50"
          role="menu"
        >
          {isAuthenticated && user ? (
            <>
              {/* User info header */}
              <div className="px-3 py-2.5 border-b border-(--border)">
                <div className="text-sm font-medium truncate">
                  {user.name ?? "User"}
                </div>
                <div className="text-xs text-(--muted) truncate">{user.email ?? ""}</div>
                {isAdmin && (
                  <span className="inline-flex items-center gap-1 mt-1.5 text-xs text-purple-300">
                    <ShieldCheck className="w-3 h-3" />
                    Admin
                  </span>
                )}
              </div>

              {/* Menu items */}
              <div className="py-1" role="none">
                <button
                  onClick={() => { setIsOpen(false); onOpenSettings("general"); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                  role="menuitem"
                >
                  <Settings className="w-3.5 h-3.5 text-(--muted)" />
                  Settings
                </button>
                <button
                  onClick={() => { setIsOpen(false); onOpenSettings("profile"); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                  role="menuitem"
                >
                  <User className="w-3.5 h-3.5 text-(--muted)" />
                  Profile
                </button>
                {isAdmin && (
                  <button
                    onClick={() => { setIsOpen(false); onOpenSettings("admin"); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                    role="menuitem"
                  >
                    <ShieldCheck className="w-3.5 h-3.5 text-purple-400" />
                    <span className="text-purple-300">Admin Panel</span>
                  </button>
                )}
              </div>

              {/* Sign out */}
              <div className="border-t border-(--border) py-1" role="none">
                <button
                  onClick={async () => {
                    setIsOpen(false);
                    await logout();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-red-400 hover:bg-(--card-hover) transition-colors cursor-pointer"
                  role="menuitem"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  Sign out
                </button>
              </div>
            </>
          ) : (
            /* Not signed in */
            <div className="py-1.5">
              <div className="px-3 py-1.5 text-xs text-(--muted) border-b border-(--border) mb-1">
                Not signed in
              </div>
              <button
                onClick={() => { setIsOpen(false); loginWithGoogle(); }}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                role="menuitem"
              >
                {/* Google G */}
                <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Sign in with Google
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
