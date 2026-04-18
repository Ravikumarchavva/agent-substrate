"use client";

import { ChevronRight, ShieldCheck, User as UserIcon } from "lucide-react";
import { GoogleIcon } from "./icons";

interface AuthUser {
  email?: string;
  name?: string;
  picture?: string;
  isAdmin?: boolean;
}

interface ProfileTabProps {
  isAuthenticated: boolean;
  user: AuthUser | null;
  isAdmin: boolean;
  loginWithGoogle: () => void;
  handleSettingsTabChange: (tab: "apps") => void;
}

export function ProfileTab({
  isAuthenticated,
  user,
  isAdmin,
  loginWithGoogle,
  handleSettingsTabChange,
}: ProfileTabProps) {
  if (!isAuthenticated || !user) {
    return (
      <div className="flex flex-col items-center rounded-xl border border-(--border) bg-(--card) px-6 py-10 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-(--card-hover) text-(--muted)">
          <UserIcon className="h-6 w-6" />
        </div>
        <h4 className="mt-4 text-lg font-semibold text-foreground">Sign in</h4>
        <p className="mt-1 text-sm text-(--muted)">Sign in to sync preferences and connect apps.</p>
        <button
          onClick={loginWithGoogle}
          className="mt-5 inline-flex cursor-pointer items-center gap-2.5 rounded-xl bg-white px-5 py-2.5 text-sm font-medium text-gray-900 shadow-sm transition-colors hover:bg-gray-100"
        >
          <GoogleIcon />
          Continue with Google
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 rounded-xl border border-(--border) bg-(--card) p-5">
        {user.picture ? (
          <img
            src={user.picture}
            alt={user.name ?? "User"}
            className="h-12 w-12 shrink-0 rounded-full object-cover"
          />
        ) : (
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-(--card-hover) text-sm font-bold text-foreground">
            {(user.name ?? user.email ?? "U")[0].toUpperCase()}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold text-foreground">{user.name ?? "User"}</p>
            {isAdmin && (
              <span className="inline-flex items-center gap-1 rounded-md bg-(--card-hover) px-2 py-0.5 text-xs font-medium text-(--muted)">
                <ShieldCheck className="h-3 w-3" />
                Admin
              </span>
            )}
          </div>
          <p className="truncate text-sm text-(--muted)">{user.email ?? ""}</p>
        </div>
      </div>

      <button
        onClick={() => handleSettingsTabChange("apps")}
        className="flex w-full cursor-pointer items-center justify-between rounded-xl border border-(--border) bg-(--card) px-5 py-3.5 text-sm font-medium text-foreground transition-colors hover:bg-(--card-hover)"
      >
        Manage connected apps
        <ChevronRight className="h-4 w-4 text-(--muted)" />
      </button>
    </div>
  );
}
