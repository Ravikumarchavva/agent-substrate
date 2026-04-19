"use client";

import { useMemo, useState } from "react";
import { CalendarDays, CheckCircle2, FolderKanban, Link2, Loader2, Mail, Search, Sparkles } from "lucide-react";
import { DocsIcon, GitHubIcon, GoogleIcon, SpotifyIcon } from "./icons";

interface ConnectorsTabProps {
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

type ConnectorMode = "existing" | "catalog";

function ModeButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-xl px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${active ? "bg-foreground text-background" : "bg-(--card) text-(--muted) hover:text-foreground"}`}
      style={active ? { boxShadow: "var(--shadow-sm)" } : undefined}
    >
      {label}
    </button>
  );
}

function StatusPill({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "success" | "soon" }) {
  const className = tone === "success" ? "bg-emerald-500/10 text-emerald-500" : "bg-(--badge-bg) text-(--badge-fg)";
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${className}`}>{label}</span>;
}

function ConnectorGlyph({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground"
      style={{ boxShadow: "var(--shadow-sm)" }}
    >
      {children}
    </div>
  );
}

export function ConnectorsTab({
  googleAuth,
  spotifyAuth,
  workspaceAuth,
  loginWithGoogle,
  loginWithSpotify,
  loginWithWorkspace,
  handleDisconnectSpotify,
  handleDisconnectWorkspace,
  disconnectingApp,
}: ConnectorsTabProps) {
  const [mode, setMode] = useState<ConnectorMode>(workspaceAuth || spotifyAuth ? "existing" : "catalog");
  const [query, setQuery] = useState("");

  const connectorCatalog = useMemo(
    () => [
      {
        id: "google-workspace",
        title: "Google Workspace",
        summary: "Drive, Calendar, and Gmail in one connector.",
        icon: <GoogleIcon className="h-5 w-5" />,
        active: true,
        connected: workspaceAuth,
        onAction: workspaceAuth ? handleDisconnectWorkspace : loginWithWorkspace,
        actionLabel: workspaceAuth ? "Disconnect" : "Connect",
      },
      {
        id: "spotify",
        title: "Spotify",
        summary: "Music playback and search for the signed-in user.",
        icon: <SpotifyIcon className="h-5 w-5 text-[#1DB954]" />,
        active: true,
        connected: spotifyAuth,
        onAction: spotifyAuth ? handleDisconnectSpotify : loginWithSpotify,
        actionLabel: spotifyAuth ? "Disconnect" : "Connect",
      },
      {
        id: "gmail",
        title: "Gmail",
        summary: "Included inside Google Workspace.",
        icon: <Mail className="h-5 w-5" />,
        active: false,
        connected: workspaceAuth,
        onAction: () => undefined,
        actionLabel: "Bundled",
      },
      {
        id: "calendar",
        title: "Calendar",
        summary: "Upcoming events, creation, and cancellations.",
        icon: <CalendarDays className="h-5 w-5" />,
        active: false,
        connected: workspaceAuth,
        onAction: () => undefined,
        actionLabel: "Bundled",
      },
      {
        id: "github-docs",
        title: "GitHub Docs",
        summary: "Planned connector for syncing GitHub markdown and docs into retrieval.",
        icon: <GitHubIcon className="h-5 w-5" />,
        active: false,
        connected: false,
        onAction: () => undefined,
        actionLabel: "Soon",
      },
      {
        id: "knowledge-docs",
        title: "Knowledge Docs",
        summary: "Reference docs and API content styled like the builder/dashboard integrations.",
        icon: <DocsIcon className="h-5 w-5" />,
        active: false,
        connected: false,
        onAction: () => undefined,
        actionLabel: "Soon",
      },
      {
        id: "slack",
        title: "Slack",
        summary: "Placeholder connector tile from the reference gallery.",
        icon: <Sparkles className="h-5 w-5" />,
        active: false,
        connected: false,
        onAction: () => undefined,
        actionLabel: "Soon",
      },
    ],
    [handleDisconnectSpotify, handleDisconnectWorkspace, loginWithSpotify, loginWithWorkspace, spotifyAuth, workspaceAuth],
  );

  const filteredCatalog = connectorCatalog.filter((connector) =>
    connector.title.toLowerCase().includes(query.trim().toLowerCase()),
  );

  const connectedRows = [
    {
      id: "google-account",
      title: "Google identity",
      icon: <GoogleIcon className="h-5 w-5" />,
      subtitle: googleAuth ? "Signed in and available as the base account." : "Required before any Google connector can attach.",
      metric: googleAuth ? "Ready" : "Not connected",
      action: googleAuth ? null : loginWithGoogle,
      actionLabel: "Sign in with Google",
      connected: googleAuth,
      services: ["Auth"],
      disconnecting: false,
    },
    {
      id: "workspace",
      title: "Google Workspace",
      icon: <FolderKanban className="h-5 w-5" />,
      subtitle: workspaceAuth ? "Drive, Calendar, and Gmail are active in the MCP panel." : "Grant the extra workspace scopes beyond basic sign-in.",
      metric: workspaceAuth ? "3 services" : "Not connected",
      action: workspaceAuth ? handleDisconnectWorkspace : loginWithWorkspace,
      actionLabel: workspaceAuth ? "Disconnect" : "Connect",
      connected: workspaceAuth,
      services: ["Drive", "Calendar", "Gmail"],
      disconnecting: disconnectingApp === "workspace",
    },
    {
      id: "spotify",
      title: "Spotify",
      icon: <SpotifyIcon className="h-5 w-5 text-[#1DB954]" />,
      subtitle: spotifyAuth ? "Music tools are active for the signed-in user." : "Optional media connector for playback and search.",
      metric: spotifyAuth ? "1 connector" : "Not connected",
      action: spotifyAuth ? handleDisconnectSpotify : loginWithSpotify,
      actionLabel: spotifyAuth ? "Disconnect" : "Connect",
      connected: spotifyAuth,
      services: ["Playback"],
      disconnecting: disconnectingApp === "spotify",
    },
  ];

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">Connectors</h2>
        <p className="max-w-3xl text-sm leading-6 text-(--muted)">
          A workspace inspired by the reference connector admin screens, adapted to the integrations this app actually has today.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 rounded-2xl bg-(--panel-muted) p-1">
          <ModeButton active={mode === "existing"} label="Existing connectors" onClick={() => setMode("existing")} />
          <ModeButton active={mode === "catalog"} label="Add connector" onClick={() => setMode("catalog")} />
        </div>
        <label className="flex min-w-65 items-center gap-2 rounded-2xl bg-(--card) px-4 py-3" style={{ boxShadow: "var(--shadow-sm)" }}>
          <Search className="h-4 w-4 text-(--muted)" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search connectors..." className="w-full bg-transparent text-sm outline-none placeholder:text-(--muted)" />
        </label>
      </div>

      {mode === "existing" ? (
        <div className="space-y-4">
          {connectedRows.map((row) => (
            <div key={row.id} className="rounded-3xl p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
              <div className="grid gap-5 xl:grid-cols-[1.5fr_0.7fr_0.8fr_1fr] xl:items-center">
                <div>
                  <div className="flex items-center gap-3">
                    <ConnectorGlyph>{row.icon}</ConnectorGlyph>
                    <div>
                      <h3 className="text-xl font-semibold text-foreground">{row.title}</h3>
                      <p className="mt-1 text-sm leading-6 text-(--muted)">{row.subtitle}</p>
                    </div>
                    <StatusPill label={row.connected ? "Connected" : "Disconnected"} tone={row.connected ? "success" : "neutral"} />
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Status</div>
                  <div className="mt-2 text-lg font-semibold text-foreground">{row.metric}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Services</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {row.services.map((service) => (
                      <StatusPill key={service} label={service} />
                    ))}
                  </div>
                </div>
                <div className="xl:justify-self-end">
                  {row.action && (
                    <button
                      type="button"
                      onClick={row.action}
                      className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${row.connected ? "border border-rose-500/20 text-rose-400 hover:bg-rose-500/10" : "bg-foreground text-background hover:opacity-90"}`}
                      disabled={row.disconnecting}
                    >
                      {row.disconnecting ? <Loader2 className="h-4 w-4 animate-spin" /> : row.connected ? <Link2 className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
                      {row.disconnecting ? "Disconnecting..." : row.actionLabel}
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Popular</div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredCatalog.map((connector) => (
              <div key={connector.id} className="rounded-3xl p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
                <div className="flex items-center justify-between gap-3">
                  <ConnectorGlyph>{connector.icon}</ConnectorGlyph>
                  <StatusPill label={connector.connected ? "Connected" : connector.active ? "Available" : connector.actionLabel} tone={connector.connected ? "success" : connector.active ? "neutral" : "soon"} />
                </div>
                <h3 className="mt-5 text-lg font-semibold text-foreground">{connector.title}</h3>
                <p className="mt-2 text-sm leading-6 text-(--muted)">{connector.summary}</p>
                <button
                  type="button"
                  onClick={connector.onAction}
                  disabled={!connector.active || connector.actionLabel === "Bundled"}
                  className={`mt-5 rounded-xl px-4 py-2 text-sm font-medium transition-colors ${connector.active && connector.actionLabel !== "Bundled" ? "bg-foreground text-background hover:opacity-90 cursor-pointer" : "bg-(--badge-bg) text-(--badge-fg) cursor-default"}`}
                >
                  {connector.actionLabel}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}