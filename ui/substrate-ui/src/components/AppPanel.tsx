"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X, PanelRightClose, PanelRightOpen, Maximize2, Minimize2, Download, Pencil, Eye } from "lucide-react";
import { AppIcon } from "@/components/AppIcon";
import { artifactKind, FileArtifactViewer } from "@/components/FileArtifactViewer";
import { VersionHistoryDropdown } from "@/components/VersionHistoryDropdown";
import { getDocumentBadge } from "@/lib/file-utils";
import { useTheme } from "@/contexts/ThemeContext";

// Strip a `#page=N` viewer fragment for the download link's href. The
// fragment steers FileArtifactViewer to a citation's page — it does nothing
// for an actual file download (browsers never send the hash, so it can't
// affect the bytes received), but leaving it in means hovering "Download"
// shows a URL ending `#page=3` in the status bar, which reads as broken even
// though the download itself was always correct.
function stripPageFragment(fileUrl: string): string {
  const hashIndex = fileUrl.indexOf("#");
  return hashIndex === -1 ? fileUrl : fileUrl.slice(0, hashIndex);
}

// Recover the thread id + session-relative path from a workspace file URL.
function parseFileRef(fileUrl?: string): { threadId: string; path: string } | null {
  if (!fileUrl) return null;
  try {
    const u = new URL(fileUrl, window.location.origin);
    const threadId = u.searchParams.get("thread_id");
    const path = u.searchParams.get("path");
    return threadId && path ? { threadId, path } : null;
  } catch {
    return null;
  }
}

const API_BASE = "/api/backend";

export type AppPanelItem = {
  /** Unique ID for this panel instance */
  id: string;
  /** "app" = MCP App iframe (default). "file" = a generated file artifact
   *  (code_interpreter HTML/PDF/xlsx/docx/…) rendered by FileArtifactViewer. */
  kind?: "app" | "file";
  /** HTTP URL to the MCP App HTML */
  httpUrl: string;
  /** Tool name (also the dedup key + tab label) */
  toolName: string;
  /** Arguments the LLM passed to the tool */
  toolArguments: Record<string, unknown>;
  /** Timestamp for ordering */
  timestamp: number;
  /** File artifacts (kind === "file"): served workspace URL + display name + mime. */
  fileUrl?: string;
  fileName?: string;
  mime?: string;
  /**
   * True for a user's own uploaded source file — the panel's Edit toggle
   * (WYSIWYG office editing) is hidden entirely, unlike an assistant-
   * generated file, which stays editable since code_interpreter itself
   * reads/writes it. Undefined/false = editable (the default, generated-
   * file behavior).
   */
  readOnly?: boolean;
};

type JsonRpcRequest = {
  jsonrpc: "2.0";
  id?: string | number;
  method: string;
  params?: Record<string, unknown>;
};

type Props = {
  items: AppPanelItem[];
  activeItemId: string | null;
  onSetActive: (id: string) => void;
  onClose: (id: string) => void;
  onClosePanel: () => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onResult?: (toolName: string, result: unknown) => void;
  /** Remount a file item's viewer (e.g. after a version restore). */
  onReloadFile?: (id: string) => void;
};

export function AppPanel({
  items,
  activeItemId,
  onSetActive,
  onClose,
  onClosePanel,
  isCollapsed,
  onToggleCollapse,
  onResult,
  onReloadFile,
}: Props) {
  // ── Per-item iframe refs (never cleared — iframes stay mounted) ──
  const iframeRefs = useRef<Map<string, HTMLIFrameElement | null>>(new Map());
  const { theme } = useTheme();
  // Full-screen the panel (for reading/editing file artifacts).
  const [maximized, setMaximized] = useState(false);
  // Office files open read-only; the header Edit toggle flips the active file
  // into BetterOffice edit mode. Reset whenever the active file (or its
  // version) changes so a reload always lands back in read-only.
  const [editingFile, setEditingFile] = useState(false);

  // ── Per-item ready / error / flash state ────────────────────────
  const [readyMap, setReadyMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});
  const [flashMap, setFlashMap] = useState<Record<string, boolean>>({});

  // Ref mirror of readyMap so timeout callbacks read the latest value without
  // stale closures.
  const readyMapRef = useRef<Record<string, boolean>>({});
  useEffect(() => { readyMapRef.current = readyMap; }, [readyMap]);

  // ── Per-item last-pushed args snapshot (prevents duplicate pushes) ─
  const prevArgsRef = useRef<Record<string, string>>({});

  // ── Track which items already have a load timeout running ────────
  const timerStartedRef = useRef<Set<string>>(new Set());

  const activeItem = items.find((i) => i.id === activeItemId) ?? items[items.length - 1];

  // Only Office docs are editable in-panel (BetterOffice). Reset edit mode
  // when the active file or its version changes.
  const activeIsOffice = /\.(docx?|xlsx?|pptx?)$/i.test(activeItem?.fileName ?? "");
  useEffect(() => {
    setEditingFile(false);
  }, [activeItem?.id, activeItem?.fileUrl]);

  // ── Low-level send helper ────────────────────────────────────────
  const sendToItem = useCallback((itemId: string, message: unknown) => {
    iframeRefs.current.get(itemId)?.contentWindow?.postMessage(message, "*");
  }, []);

  // ── Feed an app its data (MCP Apps spec) ─────────────────────────
  // `toolArguments` carries the tool result's structured_content. Send it as
  // both tool-input (apps that key off arguments) and tool-result (apps that
  // key off the CallToolResult.structuredContent) so any app shape works.
  const sendToolData = useCallback(
    (itemId: string, args: Record<string, unknown>) => {
      sendToItem(itemId, {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-input",
        params: { arguments: args },
      });
      sendToItem(itemId, {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-result",
        params: { content: [], structuredContent: args },
      });
    },
    [sendToItem]
  );

  // ── Spotify: broadcast token to every loaded iframe ─────────────
  // Only the Spotify player HTML handles `spotify_token_from_parent`;
  // all other iframes silently ignore it.
  const fetchAndSendSpotifyToken = useCallback(async (knownToken?: string) => {
    try {
      let token = knownToken ?? null;

      if (!token) {
        try { token = sessionStorage.getItem("spotify_access_token"); } catch { /* no sessionStorage */ }
      }
      if (!token) {
        const res = await fetch("/chat/api/spotify/token");
        if (res.ok) {
          const data = await res.json() as { access_token?: string };
          token = data.access_token ?? null;
          if (token) {
            try { sessionStorage.setItem("spotify_access_token", token); } catch { /* ignore */ }
          }
        }
      }

      if (token) {
        iframeRefs.current.forEach((iframe) => {
          iframe?.contentWindow?.postMessage(
            { type: "spotify_token_from_parent", access_token: token },
            "*"
          );
        });
      }
    } catch {
      // Not connected — silent
    }
  }, []);

  // ── Google Workspace: broadcast token to every loaded iframe ─────
  const fetchAndSendWorkspaceToken = useCallback(async () => {
    try {
      const res = await fetch("/chat/api/workspace/token");
      if (!res.ok) return;
      const data = await res.json() as { access_token?: string; connected?: boolean };
      if (!data.access_token) return;

      // Also get email from cookie for the workspace panel
      let email = "";
      try {
        const cookie = document.cookie.split("; ").find(r => r.startsWith("google_user="));
        if (cookie) {
          const parsed = JSON.parse(decodeURIComponent(cookie.split("=")[1])) as { email?: string };
          email = parsed.email ?? "";
        }
      } catch { /* ignore */ }

      iframeRefs.current.forEach((iframe) => {
        iframe?.contentWindow?.postMessage(
          { type: "workspace_token_from_parent", access_token: data.access_token, email },
          "*"
        );
      });
    } catch {
      // Not connected — silent
    }
  }, []);

  // ── Notify iframes when theme changes ─────────────────────────────
  useEffect(() => {
    iframeRefs.current.forEach((iframe, id) => {
      if (!readyMap[id] || !iframe?.contentWindow) return;
      iframe.contentWindow.postMessage(
        { jsonrpc: "2.0", method: "ui/notifications/theme-changed", params: { theme } },
        "*",
      );
    });
  }, [theme, readyMap]);

  // ── Re-send token when switching to a Spotify tab ────────────────
  // With persistent iframes the `ready` event only fires once on first load,
  // so we push the token explicitly whenever the active tab is Spotify.
  useEffect(() => {
    if (activeItem?.toolName?.includes("spotify") && readyMap[activeItem.id]) {
      fetchAndSendSpotifyToken();
    }
    // Only re-run when the active item changes, not on every readyMap update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeItem?.id]);

  // ── Re-send workspace token when switching to a Google Workspace tab ─
  useEffect(() => {
    if (activeItem?.toolName?.includes("google_workspace") && readyMap[activeItem.id]) {
      fetchAndSendWorkspaceToken();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeItem?.id]);

  // ── Push updated context to ready iframes when toolArguments change ─
  useEffect(() => {
    for (const item of items) {
      if (!readyMap[item.id]) continue;
      const snapshot = JSON.stringify(item.toolArguments);
      if (snapshot === (prevArgsRef.current[item.id] ?? "")) continue;
      prevArgsRef.current[item.id] = snapshot;
      sendToolData(item.id, item.toolArguments);
      // Flash the tab so the user notices new data arrived
      setFlashMap((f) => ({ ...f, [item.id]: true }));
      setTimeout(() => setFlashMap((f) => ({ ...f, [item.id]: false })), 700);
    }
  }, [items, readyMap, sendToolData]);

  // ── Clean up state when items are removed ───────────────────────
  useEffect(() => {
    const ids = new Set(items.map((i) => i.id));

    setReadyMap((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const id of Object.keys(next)) {
        if (!ids.has(id)) { delete next[id]; changed = true; }
      }
      return changed ? next : prev;
    });

    setErrorMap((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const id of Object.keys(next)) {
        if (!ids.has(id)) { delete next[id]; changed = true; }
      }
      return changed ? next : prev;
    });

    for (const id of Object.keys(prevArgsRef.current)) {
      if (!ids.has(id)) delete prevArgsRef.current[id];
    }
    iframeRefs.current.forEach((_, id) => {
      if (!ids.has(id)) iframeRefs.current.delete(id);
    });
    timerStartedRef.current.forEach((id) => {
      if (!ids.has(id)) timerStartedRef.current.delete(id);
    });
  }, [items]);

  // ── Load timeout: mark error if iframe doesn't signal ready in 10s ─
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const item of items) {
      // File artifacts don't do the MCP ready handshake — skip the timeout.
      if (item.kind === "file") continue;
      if (timerStartedRef.current.has(item.id) || readyMap[item.id]) continue;
      timerStartedRef.current.add(item.id);
      const t = setTimeout(() => {
        if (!readyMapRef.current[item.id]) {
          setErrorMap((prev) => ({ ...prev, [item.id]: "MCP App took too long to load" }));
        }
      }, 10000);
      timers.push(t);
    }
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  // ── Main postMessage handler ─────────────────────────────────────
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;

      // ── Spotify popup / token messages ─────────────────────────
      if (msg.type === "spotify_login_request") {
        const w = 500, h = 700;
        const left = Math.round(window.screen.width / 2 - w / 2);
        const top = Math.round(window.screen.height / 2 - h / 2);
        const loginUrl =
          window.location.href
            .replace("localhost", "127.0.0.1")
            .split("/")
            .slice(0, 3)
            .join("/") + "/chat/api/spotify/login";
        window.open(loginUrl, "spotify-auth", `width=${w},height=${h},left=${left},top=${top}`);
        return;
      }
      if (msg.type === "spotify_auth_success") {
        const token: string | undefined = (msg as { access_token?: string }).access_token;
        if (token) {
          try { sessionStorage.setItem("spotify_access_token", token); } catch { /* ignore */ }
        }
        fetchAndSendSpotifyToken(token);
        return;
      }
      if (msg.type === "spotify_auth_changed" || msg.type === "spotify_token_refresh_request") {
        fetchAndSendSpotifyToken();
        return;
      }

      // ── Workspace messages ──────────────────────────────────────
      if (msg.type === "workspace_token_refresh_request") {
        fetchAndSendWorkspaceToken();
        return;
      }
      if (msg.type === "workspace_login_request") {
        const w = 500, h = 700;
        const left = Math.round(window.screen.width / 2 - w / 2);
        const top = Math.round(window.screen.height / 2 - h / 2);
        window.open("/chat/api/workspace/login", "workspace-auth", `width=${w},height=${h},left=${left},top=${top}`);
        return;
      }
      if (msg.type === "workspace_auth_success") {
        fetchAndSendWorkspaceToken();
        return;
      }

      // ── JSON-RPC messages from a known iframe ───────────────────
      const data = msg as JsonRpcRequest;
      if (data.jsonrpc !== "2.0") return;

      // Identify which item's iframe sent this message
      let senderItemId: string | null = null;
      iframeRefs.current.forEach((iframe, id) => {
        if (iframe?.contentWindow === event.source) senderItemId = id;
      });
      if (!senderItemId) return;

      const itemId = senderItemId;
      const senderItem = items.find((i) => i.id === itemId);
      if (!senderItem) return;

      switch (data.method) {
        case "ready":
        case "ui/initialize": {
          setReadyMap((prev) => ({ ...prev, [itemId]: true }));

          if (data.method === "ui/initialize") {
            sendToItem(itemId, {
              jsonrpc: "2.0",
              id: data.id,
              result: {
                protocolVersion: "2025-06-18",
                hostInfo: { name: "agent-framework-ui", version: "1.0.0" },
                hostCapabilities: {},
                hostContext: {
                  theme,
                  toolInfo: { tool: { name: senderItem.toolName } },
                },
              },
            });
          } else {
            sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: { status: "ok" } });
          }

          // Push initial context immediately and record snapshot
          const snapshot = JSON.stringify(senderItem.toolArguments);
          prevArgsRef.current[itemId] = snapshot;
          sendToolData(itemId, senderItem.toolArguments);

          // Forward Spotify token if applicable
          if (senderItem.toolName?.includes("spotify")) {
            fetchAndSendSpotifyToken();
          }
          // Forward workspace token if applicable
          if (senderItem.toolName?.includes("google_workspace")) {
            fetchAndSendWorkspaceToken();
          }
          break;
        }

        case "getContext":
          sendToItem(itemId, {
            jsonrpc: "2.0",
            id: data.id,
            result: { toolName: senderItem.toolName, arguments: senderItem.toolArguments },
          });
          break;

        case "submitResult":
        case "ui/update-model-context":
          onResult?.(
            senderItem.toolName,
            data.params?.result ?? data.params?.content ?? data.params
          );
          sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: { status: "received" } });
          break;

        case "resize":
        case "ui/notifications/size-changed":
          if (data.id !== undefined) {
            sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: { status: "ok" } });
          }
          break;

        case "ui/open-link":
          if (data.params?.url) {
            window.open(data.params.url as string, "_blank", "noopener");
          }
          sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: {} });
          break;

        case "ui/request-display-mode": {
          // Spec: app asks to render inline | fullscreen | pip. The panel is a
          // fixed side surface; grant the request and make sure it's expanded
          // when the app wants more room.
          const mode = (data.params?.mode as string) || "inline";
          if (mode !== "inline" && isCollapsed) onToggleCollapse();
          sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: { mode } });
          break;
        }

        case "ui/message":
          onResult?.(senderItem.toolName, {
            type: "message",
            role: data.params?.role || "user",
            content: data.params?.content,
          });
          sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: {} });
          break;

        case "close":
          onClose(senderItem.id);
          sendToItem(itemId, { jsonrpc: "2.0", id: data.id, result: { status: "ok" } });
          break;

        default:
          sendToItem(itemId, {
            jsonrpc: "2.0",
            id: data.id,
            error: { code: -32601, message: `Method not found: ${data.method}` },
          });
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [items, onResult, onClose, sendToItem, sendToolData, isCollapsed, onToggleCollapse, fetchAndSendSpotifyToken, fetchAndSendWorkspaceToken, theme]);

  if (items.length === 0) return null;

  // ── Collapsed pill ───────────────────────────────────────────────
  if (isCollapsed) {
    return (
      <div className="fixed bottom-20 right-3 z-30 xl:right-0 xl:top-1/2 xl:bottom-auto xl:-translate-y-1/2">
        <button
          onClick={onToggleCollapse}
          className="flex items-center gap-2 rounded-full border border-(--border) bg-(--card) px-3 py-3 shadow-xl transition-colors hover:bg-background cursor-pointer xl:rounded-l-lg xl:rounded-r-none xl:border-r-0"
          title="Open app panel"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-background">
            <AppIcon toolName={activeItem?.toolName} className="h-4 w-4 text-foreground" />
          </span>
          <span className="max-w-32 truncate text-xs font-medium text-(--muted)">
            {items.length === 1 && activeItem
              ? activeItem.toolName.replace(/_/g, " ")
              : `${items.length} apps`}
          </span>
          <PanelRightOpen className="w-4 h-4 text-foreground" />
        </button>
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-30 bg-black/45 xl:hidden"
        onClick={onToggleCollapse}
        aria-label="Dismiss app panel"
      />

      <div
        className={
          maximized
            ? "fixed inset-0 z-50 flex flex-col bg-background"
            : `fixed inset-0 z-40 flex flex-col bg-background xl:static xl:z-auto xl:h-full xl:shrink-0 xl:border-l xl:border-(--border) ${
                // File artifacts (reports, spreadsheets, docs) need room to be
                // readable — give them >50% of the viewport; MCP apps stay compact.
                activeItem?.kind === "file" ? "xl:w-[58vw]" : "xl:w-120 2xl:w-140"
              }`
        }
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between border-b border-(--border) bg-background px-3 py-3 xl:px-3 xl:py-2">
          <div className="flex items-center gap-2 min-w-0">
            {activeItem?.kind === "file" ? (
              <span className="truncate text-sm font-medium text-foreground">
                {activeItem.fileName ?? activeItem.toolName}
              </span>
            ) : (
              <>
                <span className="text-xs font-semibold text-foreground uppercase tracking-wider">Apps</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-(--border) bg-background font-medium" style={{ color: "var(--accent)" }}>
                  {items.length}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-0.5">
            {activeItem?.kind === "file" && activeIsOffice && !activeItem.readOnly && (
              <button
                onClick={() => setEditingFile((v) => !v)}
                className={`flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium transition-colors ${
                  editingFile
                    ? "text-(--accent) hover:bg-(--card-hover)"
                    : "text-(--muted) hover:bg-(--card-hover) hover:text-foreground"
                }`}
                title={editingFile ? "Switch to read-only" : "Edit this file"}
              >
                {editingFile ? (
                  <>
                    <Eye className="h-3.5 w-3.5" /> View
                  </>
                ) : (
                  <>
                    <Pencil className="h-3.5 w-3.5" /> Edit
                  </>
                )}
              </button>
            )}
            {activeItem?.kind === "file" &&
              (() => {
                const fref = parseFileRef(activeItem.fileUrl);
                return fref ? (
                  <VersionHistoryDropdown
                    threadId={fref.threadId}
                    path={fref.path}
                    onRestored={() => onReloadFile?.(activeItem.id)}
                  />
                ) : null;
              })()}
            {activeItem?.kind === "file" &&
              activeItem.fileUrl &&
              // A PDF renders via a plain <iframe> (FileArtifactViewer), so
              // the browser's own native PDF viewer already shows a
              // download control inside it — ours would just duplicate it.
              artifactKind(activeItem.fileName ?? "", activeItem.mime) !== "pdf" && (
                <a
                  href={stripPageFragment(activeItem.fileUrl)}
                  download={activeItem.fileName ?? true}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground"
                  title="Download"
                >
                  <Download className="h-4 w-4" />
                </a>
              )}
            <button
              onClick={() => setMaximized((v) => !v)}
              className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
              title={maximized ? "Exit full screen" : "Full screen"}
            >
              {maximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
            {!maximized && (
              <button
                onClick={onToggleCollapse}
                className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
                title="Collapse panel"
              >
                <PanelRightClose className="h-4 w-4" />
              </button>
            )}
            <button
              onClick={() => { setMaximized(false); onClosePanel(); }}
              className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
              title="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Tabs — visible when more than one app is open.
            Each tab is a <div role="tab"> rather than <button> to avoid
            the React hydration error caused by nesting a close <button>
            inside a tab <button>. */}
        {items.length > 1 && (
          <div
            className="flex border-b border-(--border) bg-background overflow-x-auto scrollbar-thin"
            role="tablist"
          >
            {items.map((item) => (
              <div
                key={item.id}
                role="tab"
                tabIndex={0}
                aria-selected={item.id === activeItem?.id}
                onClick={() => onSetActive(item.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onSetActive(item.id);
                }}
                className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors shrink-0 cursor-pointer select-none ${
                  item.id === activeItem?.id
                    ? "bg-(--card)"
                    : "border-transparent text-(--muted) hover:text-foreground hover:bg-(--card)"
                } ${flashMap[item.id] ? "bg-blue-500/10" : ""}`}
                style={item.id === activeItem?.id ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}
              >
                {item.kind === "file" ? (
                  (() => {
                    const { Icon, badgeClass } = getDocumentBadge(item.fileName ?? item.toolName);
                    return (
                      <span className={`flex h-6 w-6 items-center justify-center rounded-lg ${badgeClass}`}>
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                    );
                  })()
                ) : (
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-background text-foreground">
                    <AppIcon toolName={item.toolName} className="h-3.5 w-3.5 text-foreground" />
                  </span>
                )}
                <span className="max-w-30 truncate">
                  {item.kind === "file"
                    ? (item.fileName ?? item.toolName)
                    : item.toolName.replace(/_/g, " ")}
                </span>
                {/* Close uses <span role="button"> — no nested <button> */}
                <span
                  role="button"
                  tabIndex={0}
                  aria-label={`Close ${item.toolName}`}
                  onClick={(e) => { e.stopPropagation(); onClose(item.id); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.stopPropagation();
                      onClose(item.id);
                    }
                  }}
                  className="ml-1 flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-background text-(--muted) hover:text-foreground cursor-pointer"
                >
                  <X className="w-3 h-3" />
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Active app name bar (MCP apps only — files show their name in the header) */}
        {activeItem && activeItem.kind !== "file" && (
          <div className="flex items-center px-3 py-1.5 bg-background border-b border-(--border) text-xs shrink-0">
            <span
              className="inline-block w-2 h-2 rounded-full mr-1.5"
              style={{ backgroundColor: readyMap[activeItem.id] ? "#22c55e" : "#eab308" }}
            />
            <span className="mr-2 flex h-6 w-6 items-center justify-center rounded-lg bg-(--card)">
              <AppIcon toolName={activeItem.toolName} className="h-3.5 w-3.5 text-foreground" />
            </span>
            <span className="font-medium text-foreground">
              {activeItem.toolName.replace(/_/g, " ")}
            </span>
            <span className="text-(--muted) ml-1">MCP App</span>
          </div>
        )}

        {/* All iframes rendered simultaneously.
            Inactive ones are hidden with CSS `visibility: hidden` so they
            stay alive (preserving Spotify auth, Kanban state, etc.) without
            reloading when the user switches tabs. */}
        <div className="flex-1 min-h-0 relative">
          {items.map((item) => {
            const isActive = item.id === activeItem?.id;
            const itemError = errorMap[item.id];
            const itemUrl = item.httpUrl.startsWith("http")
              ? item.httpUrl
              : `${API_BASE}${item.httpUrl}`;

            // File artifacts (code_interpreter output) render via
            // FileArtifactViewer instead of the MCP-app iframe. Keyed on
            // id+fileUrl so a changed server file (e.g. the agent rewrote it)
            // remounts the viewer and re-fetches.
            if (item.kind === "file") {
              return (
                <div
                  key={`${item.id}:${item.fileUrl ?? ""}`}
                  className="absolute inset-0 flex flex-col"
                  style={{ visibility: isActive ? "visible" : "hidden" }}
                >
                  <FileArtifactViewer
                    fileUrl={item.fileUrl ?? ""}
                    fileName={item.fileName ?? item.toolName}
                    mime={item.mime}
                    editMode={isActive && editingFile}
                  />
                </div>
              );
            }

            return (
              <div
                key={item.id}
                className="absolute inset-0 flex flex-col"
                style={{ visibility: isActive ? "visible" : "hidden" }}
              >
                {itemError ? (
                  <div className="flex-1 flex items-center justify-center p-4 text-center text-sm text-(--muted)">
                    <div>
                      <p>⚠️ {itemError}</p>
                      <p className="text-xs mt-1">
                        The interactive UI for <strong>{item.toolName}</strong> could not be loaded.
                      </p>
                    </div>
                  </div>
                ) : (
                  <iframe
                    ref={(el) => {
                      if (el) iframeRefs.current.set(item.id, el);
                      else iframeRefs.current.delete(item.id);
                    }}
                    src={itemUrl}
                    sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                    allow="autoplay; encrypted-media"
                    style={{ width: "100%", flex: 1, border: "none", display: "block" }}
                    title={`${item.toolName} MCP App`}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
