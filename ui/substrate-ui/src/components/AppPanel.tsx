"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X, Maximize2, Minimize2, PanelRightClose, PanelRightOpen } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

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
  /** Currently active panel items */
  items: AppPanelItem[];
  /** Active panel item ID */
  activeItemId: string | null;
  /** Set active panel item */
  onSetActive: (id: string) => void;
  /** Close a specific panel item */
  onClose: (id: string) => void;
  /** Close the entire panel */
  onClosePanel: () => void;
  /** Whether panel is collapsed */
  isCollapsed: boolean;
  /** Toggle collapsed state */
  onToggleCollapse: () => void;
  /** Called when an MCP App sends a context update */
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
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [isReady, setIsReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevArgsRef = useRef<string>("");

  const activeItem = items.find((i) => i.id === activeItemId) || items[items.length - 1];

  // Build the full URL
  const fullUrl = activeItem
    ? activeItem.httpUrl.startsWith("http")
      ? activeItem.httpUrl
      : `${API_BASE}${activeItem.httpUrl}`
    : "";

  // Reset state when active item changes
  useEffect(() => {
    setIsReady(false);
    setError(null);
    prevArgsRef.current = "";
  }, [activeItem?.id]);

  // Push updated context to iframe when toolArguments change
  useEffect(() => {
    if (!isReady || !iframeRef.current?.contentWindow || !activeItem) return;
    const snapshot = JSON.stringify(activeItem.toolArguments);
    if (snapshot === prevArgsRef.current) return;
    prevArgsRef.current = snapshot;
    iframeRef.current.contentWindow.postMessage(
      {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-input",
        params: { arguments: activeItem.toolArguments },
      },
      "*"
    );
  }, [isReady, activeItem?.toolArguments]);

  const sendResponse = useCallback(
    (id: string | number | undefined, result: unknown) => {
      if (!iframeRef.current?.contentWindow) return;
      iframeRef.current.contentWindow.postMessage(
        { jsonrpc: "2.0", id, result },
        "*"
      );
    },
    []
  );

  const sendError = useCallback(
    (id: string | number | undefined, code: number, message: string) => {
      if (!iframeRef.current?.contentWindow) return;
      iframeRef.current.contentWindow.postMessage(
        { jsonrpc: "2.0", id, error: { code, message } },
        "*"
      );
    },
    []
  );

  // Handle incoming postMessage from the iframe
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const data = event.data as JsonRpcRequest;
      if (!data || data.jsonrpc !== "2.0") return;
      if (iframeRef.current && event.source !== iframeRef.current.contentWindow) return;

      switch (data.method) {
        case "ready":
        case "ui/initialize":
          setIsReady(true);
          if (data.method === "ui/initialize") {
            sendResponse(data.id, {
              protocolVersion: "2025-06-18",
              hostInfo: { name: "agent-framework-ui", version: "1.0.0" },
              hostCapabilities: {},
              hostContext: {
                theme: "dark",
                toolInfo: { tool: { name: activeItem?.toolName || "" } },
              },
            });
            if (iframeRef.current?.contentWindow && activeItem) {
              iframeRef.current.contentWindow.postMessage(
                {
                  jsonrpc: "2.0",
                  method: "ui/notifications/tool-input",
                  params: { arguments: activeItem.toolArguments },
                },
                "*"
              );
            }
          } else {
            sendResponse(data.id, { status: "ok" });
          }
          break;

        case "getContext":
          sendResponse(data.id, {
            toolName: activeItem?.toolName,
            arguments: activeItem?.toolArguments,
          });
          break;

        case "submitResult":
        case "ui/update-model-context":
          onResult?.(
            activeItem?.toolName || "",
            data.params?.result ?? data.params?.content ?? data.params
          );
          sendResponse(data.id, { status: "received" });
          break;

        case "resize":
        case "ui/notifications/size-changed":
          // In panel mode, we ignore resize — the panel controls size
          if (data.id) sendResponse(data.id, { status: "ok" });
          break;

        case "ui/open-link":
          if (data.params?.url) {
            window.open(data.params.url as string, "_blank", "noopener");
          }
          sendResponse(data.id, {});
          break;

        case "ui/message":
          onResult?.(activeItem?.toolName || "", {
            type: "message",
            role: data.params?.role || "user",
            content: data.params?.content,
          });
          sendResponse(data.id, {});
          break;

        case "close":
          if (activeItem) onClose(activeItem.id);
          sendResponse(data.id, { status: "ok" });
          break;

        default:
          sendError(data.id, -32601, `Method not found: ${data.method}`);
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [activeItem, onResult, onClose, sendResponse, sendError]);

  // Timeout for app loading
  useEffect(() => {
    if (!activeItem) return;
    const timeout = setTimeout(() => {
      if (!isReady) setError("MCP App took too long to load");
    }, 10000);
    return () => clearTimeout(timeout);
  }, [isReady, activeItem?.id]);

  if (items.length === 0) return null;

  // Collapsed: just show a small pill/tab
  if (isCollapsed) {
    return (
      <div className="fixed right-0 top-1/2 -translate-y-1/2 z-30">
        <button
          onClick={onToggleCollapse}
          className="flex items-center gap-2 px-3 py-3 bg-zinc-800 border border-zinc-700 border-r-0 rounded-l-lg shadow-xl hover:bg-zinc-700 transition-colors"
          title="Open app panel"
        >
          <PanelRightOpen className="w-4 h-4 text-zinc-300" />
          <span className="text-xs text-zinc-400 font-medium writing-mode-vertical">
            {items.length} app{items.length !== 1 ? "s" : ""}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="w-[480px] xl:w-[560px] 2xl:w-[640px] flex flex-col border-l border-[var(--border)] bg-[var(--background)] h-full shrink-0">
      {/* Panel Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border)] bg-zinc-800/50">
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

      {/* Tabs - when multiple apps are open */}
      {items.length > 1 && (
        <div className="flex border-b border-[var(--border)] bg-zinc-900/50 overflow-x-auto scrollbar-thin">
          {items.map((item) => (
            <button
              key={item.id}
              onClick={() => onSetActive(item.id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors shrink-0 ${
                item.id === activeItem?.id
                  ? "border-emerald-500 text-emerald-400 bg-zinc-800/50"
                  : "border-transparent text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/30"
              }`}
            >
              <span className="max-w-[120px] truncate">
                {item.toolName.replace(/_/g, " ")}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(item.id);
                }}
                className="ml-1 p-0.5 rounded hover:bg-zinc-700 text-zinc-500 hover:text-zinc-300"
              >
                <X className="w-3 h-3" />
              </button>
            </button>
          ))}
        </div>
      )}

      {/* App Content */}
      <div className="flex-1 min-h-0">
        {activeItem && (
          <div className="h-full flex flex-col">
            {/* App Header */}
            <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/30 border-b border-zinc-800 text-xs">
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ backgroundColor: isReady ? "#22c55e" : "#eab308" }}
                />
                <span className="font-medium text-zinc-300">
                  {activeItem.toolName.replace(/_/g, " ")}
                </span>
                <span className="text-zinc-500">MCP App</span>
              </span>
            </div>

            {/* Iframe */}
            {error ? (
              <div className="flex-1 flex items-center justify-center p-4 text-center text-sm text-zinc-500">
                <div>
                  <p>⚠️ {error}</p>
                  <p className="text-xs mt-1">
                    The interactive UI for <strong>{activeItem.toolName}</strong> could not be loaded.
                  </p>
                </div>
              </div>
            ) : (
              <iframe
                key={activeItem.id}
                ref={iframeRef}
                src={fullUrl}
                sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                allow="autoplay; encrypted-media"
                style={{
                  width: "100%",
                  flex: 1,
                  border: "none",
                  display: "block",
                }}
                title={`${activeItem.toolName} MCP App`}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
