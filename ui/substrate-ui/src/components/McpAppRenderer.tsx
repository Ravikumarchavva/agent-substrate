"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTheme } from "@/contexts/ThemeContext";

const API_BASE = "/api/backend";

/**
 * Supported JSON-RPC methods the MCP App can call via postMessage.
 * Includes both legacy methods and spec-compliant MCP Apps methods.
 */
type McpAppMethod =
  | "ready"                     // App signals it's loaded (legacy)
  | "getContext"                // App requests tool call context (legacy)
  | "submitResult"             // App sends back a result value (legacy)
  | "resize"                    // App requests a height change (legacy)
  | "close"                     // App wants to dismiss itself (legacy)
  | "ui/initialize"            // MCP Apps spec: initialization handshake
  | "ui/update-model-context"  // MCP Apps spec: update model context
  | "ui/notifications/size-changed" // MCP Apps spec: size change notification
  | "ui/open-link"             // MCP Apps spec: open external URL
  | "ui/request-display-mode"  // MCP Apps spec: request inline | fullscreen | pip
  | "ui/message";              // MCP Apps spec: send message to chat

type JsonRpcRequest = {
  jsonrpc: "2.0";
  id?: string | number;
  method: McpAppMethod;
  params?: Record<string, unknown>;
};

type Props = {
  /** HTTP URL to the MCP App HTML (e.g., /ui/time_picker) */
  httpUrl: string;
  /** Arguments the LLM passed to the tool */
  toolArguments?: Record<string, unknown>;
  /** Tool name */
  toolName: string;
  /** Called when the app submits a result */
  onResult?: (result: unknown) => void;
  /** Called when the app requests to close */
  onClose?: () => void;
  /** Max height in px (default: 400) */
  maxHeight?: number;
};

export function McpAppRenderer({
  httpUrl,
  toolArguments = {},
  toolName,
  onResult,
  onClose,
  maxHeight = 400,
}: Props) {
  const { theme: currentTheme } = useTheme();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [isReady, setIsReady] = useState(false);
  const [height, setHeight] = useState(200);
  const [error, setError] = useState<string | null>(null);
  const prevArgsRef = useRef<string>("");

  // Build the full URL
  const fullUrl = httpUrl.startsWith("http") ? httpUrl : `${API_BASE}${httpUrl}`;

  // Push updated context to iframe when toolArguments change after ready
  useEffect(() => {
    if (!isReady || !iframeRef.current?.contentWindow) return;
    const snapshot = JSON.stringify(toolArguments);
    if (snapshot === prevArgsRef.current) return;
    prevArgsRef.current = snapshot;
    // Send updated tool data to the iframe as both tool-input and tool-result
    // (MCP Apps spec) so any app shape receives the structured_content.
    const win = iframeRef.current.contentWindow;
    win.postMessage(
      { jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: toolArguments } },
      "*"
    );
    win.postMessage(
      { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { content: [], structuredContent: toolArguments } },
      "*"
    );
  }, [isReady, toolArguments]);

  // Send a JSON-RPC response back to the iframe
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

  // Send a JSON-RPC error back to the iframe
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
      const data = event.data;
      
      // Validate JSON-RPC format
      if (!data || data.jsonrpc !== "2.0") return;

      // Validate source is our iframe
      if (iframeRef.current && event.source !== iframeRef.current.contentWindow) {
        return;
      }

      switch (data.method) {
        case "ready":
        case "ui/initialize":
          setIsReady(true);
          if (data.method === "ui/initialize") {
            // Respond with MCP Apps spec initialize result
            sendResponse(data.id, {
              protocolVersion: "2025-06-18",
              hostInfo: { name: "agent-framework-ui", version: "1.0.0" },
              hostCapabilities: {},
              hostContext: {
                theme: currentTheme,
                toolInfo: {
                  tool: { name: toolName },
                },
              },
            });
            // Send the tool data as both tool-input and tool-result (MCP Apps spec)
            const win = iframeRef.current?.contentWindow;
            if (win) {
              win.postMessage(
                { jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: toolArguments } },
                "*"
              );
              win.postMessage(
                { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { content: [], structuredContent: toolArguments } },
                "*"
              );
            }
          } else {
            sendResponse(data.id, { status: "ok" });
          }
          break;

        case "getContext":
          sendResponse(data.id, {
            toolName,
            arguments: toolArguments,
          });
          break;

        case "submitResult":
          onResult?.(data.params?.result ?? data.params?.content ?? data.params);
          sendResponse(data.id, { status: "received" });
          break;

        case "ui/update-model-context":
          // MCP Apps spec: params.context holds the app-specific state payload.
          // Extract it so the backend receives { tool_name, context: <state> }
          // not the double-nested { tool_name, context: { context: <state> } }.
          onResult?.(data.params?.context ?? data.params?.result ?? data.params?.content ?? data.params);
          sendResponse(data.id, { status: "received" });
          break;

        case "resize":
          if (typeof data.params?.height === "number") {
            setHeight(Math.min(data.params.height as number, maxHeight));
          }
          sendResponse(data.id, { status: "ok" });
          break;

        case "ui/notifications/size-changed":
          if (typeof data.params?.height === "number") {
            setHeight(Math.min(data.params.height as number, maxHeight));
          }
          // Notifications don't need responses
          break;

        case "ui/open-link":
          if (data.params?.url) {
            window.open(data.params.url as string, "_blank", "noopener");
          }
          sendResponse(data.id, {});
          break;

        case "ui/request-display-mode":
          // Inline renderer grants the requested mode without relayout.
          sendResponse(data.id, { mode: (data.params?.mode as string) || "inline" });
          break;

        case "ui/message":
          // Forward as a chat message — onResult handles this
          onResult?.({
            type: "message",
            role: data.params?.role || "user",
            content: data.params?.content,
          });
          sendResponse(data.id, {});
          break;

        case "close":
          onClose?.();
          sendResponse(data.id, { status: "ok" });
          break;

        default:
          sendError(data.id, -32601, `Method not found: ${data.method}`);
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [toolName, toolArguments, onResult, onClose, maxHeight, sendResponse, sendError]);

  // Timeout: if app doesn't signal ready within 10s, show fallback
  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!isReady) {
        setError("MCP App took too long to load");
      }
    }, 10000);
    return () => clearTimeout(timeout);
  }, [isReady, toolName]);

  const handleIframeLoad = () => {};

  return (
    <div className="rounded-lg border border-(--border) overflow-hidden bg-(--card) my-2 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-background border-b border-(--border) text-xs text-(--muted)">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full" 
                style={{ backgroundColor: isReady ? "#22c55e" : "#eab308" }} />
          <span className="font-medium text-foreground">{toolName}</span>
          <span className="text-(--muted)">MCP App</span>
        </span>
        {onClose && (
          <button
            onClick={onClose}
            className="text-(--muted) hover:text-foreground transition-colors px-1 cursor-pointer"
            aria-label="Close MCP App"
          >
            ✕
          </button>
        )}
      </div>

      {/* Iframe or error */}
      {error ? (
        <div className="p-4 text-center text-sm text-(--muted)">
          <p>⚠️ {error}</p>
          <p className="text-xs mt-1">
            The interactive UI for <strong>{toolName}</strong> could not be loaded.
          </p>
          <p className="text-xs mt-2 text-(--muted-foreground)">
            URL: {fullUrl}
          </p>
        </div>
      ) : (
        <iframe
          ref={iframeRef}
          src={fullUrl}
          onLoad={handleIframeLoad}
          sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox"
          allow="autoplay; encrypted-media"
          style={{
            width: "100%",
            height: `${height}px`,
            border: "none",
            display: "block",
          }}
          title={`${toolName} MCP App`}
        />
      )}
    </div>
  );
}
