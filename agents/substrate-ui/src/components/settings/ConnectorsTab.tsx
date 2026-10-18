"use client";

import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Badge, Button, Page, SettingGroup } from "@/design";
import { GitHubIcon, GoogleIcon, SpotifyIcon } from "./icons";

interface ConnectorsTabProps {
  spotifyAuth: boolean;
  workspaceAuth: boolean;
  loginWithSpotify: () => void;
  loginWithWorkspace: () => void;
  handleDisconnectSpotify: () => void;
  handleDisconnectWorkspace: () => void;
  disconnectingApp: "spotify" | "workspace" | null;
}

/** One account the assistant can use: what it is, what it gives, whether it is connected, and the one thing you can do about it. */
function ConnectorRow({ icon, name, description, status, action }: { icon: ReactNode; name: string; description: string; status: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3.5">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-badge text-foreground">{icon}</div>
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-foreground">{name}</p>
          {status}
        </div>
        <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
      </div>
      {action && <div className="ml-auto flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}

export function ConnectorsTab({
  spotifyAuth,
  workspaceAuth,
  handleDisconnectSpotify,
  loginWithWorkspace,
  handleDisconnectWorkspace,
  disconnectingApp,
}: ConnectorsTabProps) {
  const connected = <Badge tone="success">Connected</Badge>;
  const disconnect = (busy: boolean, onClick: () => void) => (
    <Button variant="danger" disabled={busy} onClick={onClick}>
      {busy && <Loader2 className="animate-spin" />}
      {busy ? "Disconnecting…" : "Disconnect"}
    </Button>
  );

  return (
    <Page title="Connectors" subtitle="Accounts the assistant can use for you. It only reaches what you connect here, and you can disconnect at any time.">
      <div className="w-full max-w-3xl space-y-6">
        <SettingGroup title="Your accounts">
          <ConnectorRow
            icon={<GoogleIcon className="size-5" />}
            name="Google Workspace"
            description="Lets the assistant read and write your Drive files, Calendar events and Gmail."
            status={workspaceAuth ? connected : <Badge>Not connected</Badge>}
            action={
              workspaceAuth ? disconnect(disconnectingApp === "workspace", handleDisconnectWorkspace) : <Button variant="primary" onClick={loginWithWorkspace}>Connect</Button>
            }
          />
          <ConnectorRow
            icon={<SpotifyIcon className="size-5 text-success" />}
            name="Spotify"
            description="Plays and controls your music. Needs Spotify Premium."
            status={spotifyAuth ? connected : <Badge tone="warning">Unavailable</Badge>}
            action={spotifyAuth && disconnect(disconnectingApp === "spotify", handleDisconnectSpotify)}
          />
        </SettingGroup>

        <SettingGroup title="Coming soon">
          <ConnectorRow icon={<GitHubIcon className="size-5" />} name="GitHub" description="Search your repositories’ docs and markdown from a chat." status={<Badge>Soon</Badge>} />
        </SettingGroup>
      </div>
    </Page>
  );
}
