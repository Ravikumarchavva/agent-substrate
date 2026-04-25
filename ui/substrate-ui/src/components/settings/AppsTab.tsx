"use client";

import type { ReactNode } from "react";
import { CheckCircle2, Link2, Loader2 } from "lucide-react";
import { GoogleIcon, SpotifyIcon } from "./icons";

interface AppsTabProps {
  googleAuth: boolean;
  spotifyAuth: boolean;
  workspaceAuth: boolean;
  loginWithGoogle: () => void;
  loginWithSpotify: () => void;
  loginWithWorkspace: () => void;
  handleDisconnectSpotify: () => void;
  handleDisconnectWorkspace: () => void;
  disconnectingApp: "google" | "spotify" | "workspace" | null;
}

interface IntegrationCardProps {
  icon: ReactNode;
  iconBg: string;
  title: string;
  description: string;
  connected: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  disconnecting: boolean;
  connectLabel?: string;
  disabled?: boolean;
  helperText?: string;
}

function IntegrationCard({
  icon,
  iconBg,
  title,
  description,
  connected,
  onConnect,
  onDisconnect,
  disconnecting,
  connectLabel,
  disabled,
  helperText,
}: IntegrationCardProps) {
  return (
    <div className="rounded-xl border border-(--border) bg-(--card) p-5">
      <div className="flex items-center gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${iconBg}`}>
          {icon}
        </div>
        <h4 className="text-sm font-semibold">{title}</h4>
        {connected && (
          <span className="ml-auto rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-400">
            Connected
          </span>
        )}
      </div>

      <p className="mt-3 text-sm leading-relaxed text-(--muted)">{description}</p>

      <div className="mt-4">
        {connected ? (
          <button
            onClick={onDisconnect}
            disabled={disconnecting}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-rose-500/20 px-3.5 py-1.5 text-sm font-medium text-rose-300 transition-colors hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {disconnecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
            {disconnecting ? "Disconnecting…" : `Disconnect ${title}`}
          </button>
        ) : (
          <button
            onClick={onConnect}
            disabled={disabled}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-(--accent) px-3.5 py-1.5 text-sm font-medium text-(--accent-foreground) transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            {connectLabel ?? `Connect ${title}`}
          </button>
        )}
      </div>

      {helperText && (
        <p className="mt-2 text-xs leading-relaxed text-(--muted)">{helperText}</p>
      )}
    </div>
  );
}

export function AppsTab({
  googleAuth,
  spotifyAuth,
  workspaceAuth,
  loginWithGoogle,
  loginWithSpotify,
  loginWithWorkspace,
  handleDisconnectSpotify,
  handleDisconnectWorkspace,
  disconnectingApp,
}: AppsTabProps) {
  if (!googleAuth) {
    return (
      <div className="rounded-xl border border-(--border) bg-(--card) p-6">
        <div className="flex items-start gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white text-gray-900">
            <GoogleIcon />
          </div>
          <div className="min-w-0 flex-1">
            <h4 className="text-sm font-semibold text-foreground">Sign in first</h4>
            <p className="mt-2 text-sm leading-relaxed text-(--muted)">
              Google is your sign-in identity. Connected apps like Spotify attach to that account,
              but they do not replace who you are in the app.
            </p>
            <button
              onClick={loginWithGoogle}
              className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-lg bg-(--accent) px-3.5 py-1.5 text-sm font-medium text-(--accent-foreground) transition-opacity hover:opacity-90"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              Sign in with Google
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <IntegrationCard
        icon={<SpotifyIcon />}
        iconBg="bg-[#1DB954] text-white"
        title="Spotify"
        description="Optional music access for the signed-in Google account. Connecting Spotify does not change your user identity."
        connected={spotifyAuth}
        onConnect={loginWithSpotify}
        onDisconnect={handleDisconnectSpotify}
        disconnecting={disconnectingApp === "spotify"}
        connectLabel="Connect Spotify"
      />
      <IntegrationCard
        icon={
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none">
            <path d="M21.35 11.1h-9.17v2.73h6.51c-.33 3.81-3.5 5.44-6.5 5.44C8.36 19.27 5 16.25 5 12c0-4.1 3.2-7.27 7.2-7.27 3.09 0 4.9 1.97 4.9 1.97L19 4.72S16.56 2 12.1 2C6.42 2 2.03 6.8 2.03 12c0 5.05 4.13 10 10.22 10 5.35 0 9.25-3.67 9.25-9.09 0-1.15-.15-1.81-.15-1.81z" fill="currentColor" />
          </svg>
        }
        iconBg="bg-white text-[#4285F4]"
        title="Google Workspace"
        description="Access Google Drive files, Calendar events, and Gmail inbox. Requires additional permission grant beyond basic sign-in."
        connected={workspaceAuth}
        onConnect={loginWithWorkspace}
        onDisconnect={handleDisconnectWorkspace}
        disconnecting={disconnectingApp === "workspace"}
        connectLabel="Connect Google Workspace"
        helperText="Grants read-only access to Drive, Calendar and Gmail."
      />
    </div>
  );
}
