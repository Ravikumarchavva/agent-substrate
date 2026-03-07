"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X, PanelRightClose, PanelRightOpen } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type AppPanelItem = {
  /** Unique ID for this panel instance */
  id: string;
  /** HTTP URL to the MCP App HTML */
  httpUrl: string;
  /** Tool name */
  toolName: string;
  /** Arguments the LLM passed to the tool */
  toolArguments: Record<string, unknown>;
  /** Timestamp for ordering */
  timestamp: number;
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
}: Props) {
  // ── Per-item iframe refs (never cleared — iframes stay mounted) ──
  const iframeRefs = useRef<Map<string, HTMLIFrameElement | null>>(new Map());

  // ── Per-item ready / error state ────────────────────────────────
  const [readyMap, setReadyMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});

  // Ref mirror of readyMap so timeout callbacks read the latest value without
  // stale closures.
  const readyMapRef = useRef<Record<string, boolean>>({});
  useEffect(() => { readyMapRef.current = readyMap; }, [readyMap]);

  // ── Per-item last-pushed args snapshot (prevents duplicate pushes) ─
  const prevArgsRef = useRef<Record<string, string>>({});

  // ── Track which items already have a load timeout running ────────
  const timerStartedRef = useRef<Set<string>>(new Set());

  const activeItem = items.find((i) => i.id === activeItemId) ?? items[items.length - 1];

  // ── Low-level send helper ────────────────────────────────────────
  const sendToItem = useCallback((itemId: string, message: unknown) => {
    iframeRefs.current.get(itemId)?.contentWindow?.postMessage(message, "*");
  }, []);

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
        const res = await fetch("/api/spotify/token");
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

  // ── Push updated context to ready iframes when toolArguments change ─
  useEffect(() => {
    for (const item of items) {
      if (!readyMap[item.id]) continue;
      const snapshot = JSON.stringify(item.toolArguments);
      if (snapshot === (prevArgsRef.current[item.id] ?? "")) continue;
      prevArgsRef.current[item.id] = snapshot;
      sendToItem(item.id, {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-input",
        params: { arguments: item.toolArguments },
      });
    }
  }, [items, readyMap, sendToItem]);

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
            .join("/") + "/api/spotify/login";
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
                  theme: "dark",
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
          sendToItem(itemId, {
            jsonrpc: "2.0",
            method: "ui/notifications/tool-input",
            params: { arguments: senderItem.toolArguments },
          });

          // Forward Spotify token if applicable
          if (senderItem.toolName?.includes("spotify")) {
            fetchAndSendSpotifyToken();
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
  }, [items, onResult, onClose, sendToItem, fetchAndSendSpotifyToken]);

  if (items.length === 0) return null;

  // ── Collapsed pill ───────────────────────────────────────────────
  if (isCollapsed) {
    return (
      <div className="fixed right-0 top-1/2 -translate-y-1/2 z-30">
        <button
          onClick={onToggleCollapse}
          className="flex items-center gap-2 px-3 py-3 bg-zinc-800 border border-zinc-700 border-r-0 rounded-l-lg shadow-xl hover:bg-zinc-700 transition-colors"
          title="Open app panel"
        >
          <PanelRightOpen className="w-4 h-4 text-zinc-300" />
          <span className="text-xs text-zinc-400 font-medium">
            {items.length} app{items.length !== 1 ? "s" : ""}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="w-120 xl:w-140 2xl:w-160 flex flex-col border-l border-(--border) bg-background h-full shrink-0">
      {/* Panel Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-(--border) bg-zinc-800/50">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">Apps</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 border border-emerald-800 font-medium">
            {items.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onToggleCollapse}
            className="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors"
            title="Collapse panel"
          >
            <PanelRightClose className="w-4 h-4" />
          </button>
          <button
            onClick={onClosePanel}
            className="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors"
            title="Close all apps"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tabs — visible when more than one app is open.
          Each tab is a <div role="tab"> rather than <button> to avoid
          the React hydration error caused by nesting a close <button>
          inside a tab <button>. */}
      {items.length > 1 && (
        <div
          className="flex border-b border-(--border) bg-zinc-900/50 overflow-x-auto scrollbar-thin"
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
                  ? "border-emerald-500 text-emerald-400 bg-zinc-800/50"
                  : "border-transparent text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/30"
              }`}
            >
              <span className="max-w-30 truncate">
                {item.toolName.replace(/_/g, " ")}
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
                className="ml-1 p-0.5 rounded hover:bg-zinc-700 text-zinc-500 hover:text-zinc-300 cursor-pointer"
              >
                <X className="w-3 h-3" />
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Active app name bar */}
      {activeItem && (
        <div className="flex items-center px-3 py-1.5 bg-zinc-800/30 border-b border-zinc-800 text-xs shrink-0">
          <span
            className="inline-block w-2 h-2 rounded-full mr-1.5"
            style={{ backgroundColor: readyMap[activeItem.id] ? "#22c55e" : "#eab308" }}
          />
          <span className="font-medium text-zinc-300">
            {activeItem.toolName.replace(/_/g, " ")}
          </span>
          <span className="text-zinc-500 ml-1">MCP App</span>
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

          return (
            <div
              key={item.id}
              className="absolute inset-0 flex flex-col"
              style={{ visibility: isActive ? "visible" : "hidden" }}
            >
              {itemError ? (
                <div className="flex-1 flex items-center justify-center p-4 text-center text-sm text-zinc-500">
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
  );
}
