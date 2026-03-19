/* ── PipelineAppPanel — right-side iframe panel for MCP apps in the builder
 *
 * Mirrors the chatbot AppPanel: shows iframe-based MCP App UIs triggered by
 * tool_result SSE events with has_app=true.  Tabs at the top for switching
 * between multiple open apps.  Collapsible with an icon-only rail.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X, PanelRightClose, PanelRightOpen } from "lucide-react";

export type AppItem = {
  id: string;
  httpUrl: string;
  toolName: string;
  toolArguments: Record<string, unknown>;
  timestamp: number;
};

interface Props {
  items: AppItem[];
  activeItemId: string | null;
  onSetActive: (id: string) => void;
  onClose: (id: string) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  variant?: "sidebar" | "embedded";
}

/* ── Pretty label from tool name ────────────────────────────────────── */
function toolLabel(name: string) {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function PipelineAppPanel({
  items,
  activeItemId,
  onSetActive,
  onClose,
  isCollapsed,
  onToggleCollapse,
  variant = "sidebar",
}: Props) {
  const iframeRefs = useRef<Map<string, HTMLIFrameElement | null>>(new Map());
  const [readyMap, setReadyMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string | null>>({});

  const activeItem = items.find((i) => i.id === activeItemId) ?? items[items.length - 1];

  /* ── Send initial data into iframe once it's ready ──────────────── */
  const pushData = useCallback(
    (itemId: string, args: Record<string, unknown>) => {
      const iframe = iframeRefs.current.get(itemId);
      if (!iframe?.contentWindow) return;
      iframe.contentWindow.postMessage(
        { jsonrpc: "2.0", method: "update_context", params: { data: args } },
        "*"
      );
    },
    []
  );

  /* ── Handle postMessage from iframes ────────────────────────────── */
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (!e.data || typeof e.data !== "object") return;
      const { method, id } = e.data as { method?: string; id?: string };
      if (method === "ready" && id) {
        setReadyMap((r) => ({ ...r, [id]: true }));
        const item = items.find((i) => i.id === id);
        if (item) pushData(id, item.toolArguments);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [items, pushData]);

  /* ── Push updated args whenever toolArguments change ─────────────── */
  const prevArgsRef = useRef<Record<string, string>>({});
  useEffect(() => {
    for (const item of items) {
      const ser = JSON.stringify(item.toolArguments);
      if (prevArgsRef.current[item.id] !== ser && readyMap[item.id]) {
        pushData(item.id, item.toolArguments);
        prevArgsRef.current[item.id] = ser;
      }
    }
  }, [items, readyMap, pushData]);

  /* ── Load timeout fallback (5 s) ─────────────────────────────────── */
  const timerRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const item of items) {
      if (!timerRef.current.has(item.id) && !readyMap[item.id]) {
        timerRef.current.add(item.id);
        setTimeout(() => {
          setReadyMap((r) => {
            if (r[item.id]) return r;
            setErrorMap((m) => ({ ...m, [item.id]: "App did not respond in time." }));
            return r;
          });
        }, 5000);
      }
    }
  }, [items, readyMap]);

  /* ─────────────────────── collapsed rail ────────────────────────── */
  if (isCollapsed) {
    return (
      <div
        className={`shrink-0 flex flex-col items-center py-3 gap-3 ${variant === "embedded" ? "" : "border-l"}`}
        style={{
          width: 44,
          height: "100%",
          background: "var(--bg-surface)",
          borderColor: "var(--border)",
        }}
      >
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg transition-opacity hover:opacity-80"
          style={{ color: "var(--text-muted)" }}
          title="Expand panel"
        >
          <PanelRightOpen size={16} />
        </button>
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() => { onSetActive(item.id); onToggleCollapse(); }}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-[10px] font-bold text-white"
            style={{ background: "var(--accent)" }}
            title={toolLabel(item.toolName)}
          >
            {toolLabel(item.toolName).charAt(0)}
          </button>
        ))}
      </div>
    );
  }

  /* ─────────────────────── full panel ────────────────────────────── */
  return (
    <div
      className={`shrink-0 flex flex-col ${variant === "embedded" ? "" : "border-l"}`}
      style={{
        width: variant === "embedded" ? "100%" : "clamp(320px, 35vw, 520px)",
        minWidth: 0,
        height: "100%",
        background: "var(--bg-surface)",
        borderColor: "var(--border)",
      }}
    >
      {/* header */}
      <div
        className="shrink-0 flex items-center gap-1.5 px-3 border-b"
        style={{
          height: 44,
          borderColor: "var(--border)",
          background: "var(--bg-elevated)",
        }}
      >
        <button
          onClick={onToggleCollapse}
          className="p-1 rounded transition-opacity hover:opacity-70 mr-1"
          style={{ color: "var(--text-muted)" }}
          title="Collapse"
        >
          <PanelRightClose size={15} />
        </button>

        {/* app tabs */}
        <div className="flex-1 flex items-center gap-1 overflow-x-auto">
          {items.map((item) => {
            const isActive = item.id === activeItem?.id;
            return (
              <div key={item.id} className="flex items-center gap-0.5 shrink-0">
                <button
                  onClick={() => onSetActive(item.id)}
                  className="px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors"
                  style={{
                    background: isActive ? "var(--accent)" : "transparent",
                    color: isActive ? "#fff" : "var(--text-muted)",
                  }}
                >
                  {toolLabel(item.toolName)}
                </button>
                <button
                  onClick={() => onClose(item.id)}
                  className="p-0.5 rounded transition-opacity hover:opacity-70"
                  style={{ color: "var(--text-dim)" }}
                >
                  <X size={11} />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* iframe panes — all mounted, only active one visible */}
      <div className="flex-1 relative overflow-hidden">
        {items.map((item) => {
          const isActive = item.id === activeItem?.id;
          const hasError = !!errorMap[item.id];
          return (
            <div
              key={item.id}
              className="absolute inset-0 flex flex-col"
              style={{ display: isActive ? "flex" : "none" }}
            >
              {hasError ? (
                <div className="flex-1 flex items-center justify-center">
                  <p className="text-xs" style={{ color: "var(--text-dim)" }}>
                    {errorMap[item.id]}
                  </p>
                </div>
              ) : (
                <>
                  {!readyMap[item.id] && (
                    <div className="absolute inset-0 flex items-center justify-center z-10"
                         style={{ background: "var(--bg-surface)" }}>
                      <div className="flex flex-col items-center gap-2">
                        <div
                          className="w-5 h-5 rounded-lg border-2 border-t-transparent animate-spin"
                          style={{ borderColor: "var(--accent)" }}
                        />
                        <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>
                          Loading app…
                        </p>
                      </div>
                    </div>
                  )}
                  <iframe
                    ref={(el) => {
                      iframeRefs.current.set(item.id, el);
                    }}
                    src={item.httpUrl}
                    className="flex-1 border-none"
                    title={toolLabel(item.toolName)}
                    sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                    onLoad={() => {
                      /* fallback: push data after load even without ready handshake */
                      setTimeout(() => {
                        if (!readyMap[item.id]) {
                          setReadyMap((r) => ({ ...r, [item.id]: true }));
                          pushData(item.id, item.toolArguments);
                        }
                      }, 800);
                    }}
                  />
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
