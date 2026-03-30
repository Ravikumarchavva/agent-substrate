"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Copy, Check, RotateCw, ChevronDown, ChevronRight, Loader2, PanelRightOpen, WrenchIcon } from "lucide-react";
import { ToolCall } from "@/types";
import { AudioPlayer } from "@/components/AudioPlayer";

type Props = {
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  timestamp?: Date;
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  isContinuation?: boolean;
  onRegenerate?: () => void;
  /** Called when an MCP App sends a context update (submitResult / ui/update-model-context) */
  onMcpAppResult?: (toolName: string, result: unknown) => void;
  /** Called when an MCP App should open in the side panel */
  onOpenInPanel?: (toolCall: ToolCall) => void;
};

export function MessageBubble({ 
  role, 
  content, 
  reasoning, 
  timestamp, 
  toolCalls,
  isToolExecuting,
  isContinuation,
  onRegenerate,
  onMcpAppResult,
  onOpenInPanel
}: Props) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  
  // Ensure content is always a valid string
  const safeContent = typeof content === 'string' ? content : String(content || "");
  const safeReasoning = typeof reasoning === 'string' ? reasoning : String(reasoning || "");

  const copyToClipboard = async () => {
    await navigator.clipboard.writeText(safeContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const formatTime = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).format(date);
  };

  /* ── User message: right-aligned pill bubble ── */
  if (isUser) {
    return (
      <div className="group px-4 py-2 flex justify-end">
        <div className="max-w-[75%] flex flex-col items-end gap-1">
          <div
            className="px-4 py-2.5 text-sm leading-relaxed"
            style={{
              background: "var(--user-bubble)",
              border: "1px solid var(--border)",
              borderRadius: "12px 12px 4px 12px",
            }}
          >
            {safeContent}
          </div>
          {timestamp && (
            <span className="text-[11px]" style={{ color: "var(--muted)" }}>
              {formatTime(timestamp)}
            </span>
          )}
        </div>
      </div>
    );
  }

  /* ── Assistant message: left-aligned with avatar ── */
  return (
    <div className="group relative px-4 py-4">
      <div className="max-w-3xl mx-auto flex gap-3">
        {/* AI avatar — hidden for continuation bubbles to avoid duplicate icons */}
        <div className="shrink-0 mt-0.5">
          {isContinuation ? (
            <div className="w-7 h-7" />
          ) : (
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold text-white"
              style={{ background: "linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 70%, #000))" }}
            >
              AI
            </div>
          )}
        </div>

        {/* Content column */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Tool Calls — grouped collapsible */}
          {toolCalls && toolCalls.length > 0 && (
            <details className="group/tools" open={false}>
              <summary
                className="flex items-center gap-2 cursor-pointer select-none list-none py-1 pr-2 rounded-lg w-fit"
                style={{ color: "var(--muted)" }}
              >
                {isToolExecuting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" style={{ color: "var(--accent)" }} />
                ) : (
                  <WrenchIcon className="w-3.5 h-3.5 shrink-0" />
                )}
                <span className="text-xs">
                  {isToolExecuting
                    ? `Running ${toolCalls.length} tool${toolCalls.length > 1 ? 's' : ''}…`
                    : `Used ${toolCalls.length} tool${toolCalls.length > 1 ? 's' : ''}`}
                </span>
                <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open/tools:rotate-90" />
              </summary>

              <div
                className="mt-1.5 rounded-lg overflow-hidden"
                style={{ border: "1px solid var(--border)", background: "var(--card)" }}
              >
                {toolCalls.map((tool, idx) => {
                  const hasApp = tool._meta?.ui?.httpUrl;
                  const isDone = tool.result !== undefined;
                  const isErr = tool.isError;
                  // Risk colour badge  (green=safe, yellow=sensitive, red=critical)
                  const riskColors: Record<string, string> = {
                    safe:      "#34d399", // emerald-400
                    sensitive: "#fbbf24", // amber-400
                    critical:  "#f87171", // red-400
                  };
                  const riskColor = riskColors[tool.color ?? tool.risk ?? "safe"] ?? "#34d399";
                  return (
                    <div key={tool.id}>
                      {idx > 0 && <div style={{ borderTop: "1px solid var(--border)" }} />}
                      <details className="group">
                        <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-(--card-hover) transition-colors list-none">
                          <span className="shrink-0">
                            {!isDone ? (
                              <Loader2 className="w-3 h-3 animate-spin" style={{ color: "var(--accent)" }} />
                            ) : isErr ? (
                              <span className="text-red-400 text-[11px] leading-none">✕</span>
                            ) : (
                              <span className="text-emerald-400 text-[11px] leading-none">✓</span>
                            )}
                          </span>
                          {/* Risk tier dot */}
                          <span
                            title={`Risk: ${tool.risk ?? "safe"}`}
                            className="shrink-0 w-2 h-2 rounded-full"
                            style={{ background: riskColor }}
                          />
                          <span className="text-xs font-medium flex-1" style={{ color: "var(--foreground)" }}>
                            {tool.name.replace(/_/g, " ")}
                          </span>
                          {hasApp && (
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={{
                                background: "color-mix(in srgb, var(--accent) 12%, transparent)",
                                color: "var(--accent)",
                              }}
                            >
                              App
                            </span>
                          )}
                          <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open:rotate-90" style={{ color: "var(--muted)" }} />
                        </summary>

                        <div className="px-3 pb-2 space-y-1.5" style={{ borderTop: "1px solid var(--border)" }}>
                          <div className="pt-2">
                            <div className="text-[10px] font-semibold uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Input</div>
                            <pre className="text-[11px] p-2 rounded overflow-x-auto" style={{ background: "var(--code-bg)", color: "var(--muted)" }}>
                              {JSON.stringify(
                                typeof tool.arguments === "string"
                                  ? (() => { try { return JSON.parse(tool.arguments); } catch { return tool.arguments; } })()
                                  : tool.arguments,
                                null, 2
                              )}
                            </pre>
                          </div>
                          {tool.result && tool.result !== "Completed" && (
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-wider mb-1" style={{ color: isErr ? "#f87171" : "var(--muted)" }}>Result</div>
                              <div
                                className="text-[11px] p-2 rounded max-h-32 overflow-y-auto whitespace-pre-wrap"
                                style={{
                                  background: isErr ? "color-mix(in srgb, #ef4444 8%, var(--code-bg))" : "var(--code-bg)",
                                  color: isErr ? "#fca5a5" : "var(--muted)",
                                }}
                              >
                                {tool.result}
                              </div>
                            </div>
                          )}
                          {hasApp && (
                            <button
                              onClick={() => onOpenInPanel?.(tool)}
                              className="flex items-center gap-1.5 text-xs py-1 transition-colors cursor-pointer"
                              style={{ color: "var(--accent)" }}
                            >
                              <PanelRightOpen className="w-3.5 h-3.5" />
                              Open {tool.name.replace(/_/g, " ")}
                            </button>
                          )}
                        </div>
                      </details>
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* Reasoning */}
          {safeReasoning && (
            <details className="text-xs border-l-2 pl-3 py-1" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
              <summary className="cursor-pointer hover:text-zinc-300 font-medium select-none">
                💭 Reasoning
              </summary>
              <div className="mt-1.5 whitespace-pre-wrap font-mono text-[11px] leading-relaxed opacity-80">
                {safeReasoning}
              </div>
            </details>
          )}

          {/* Main markdown content */}
          {safeContent && (
            <div className="prose prose-invert max-w-none text-sm leading-7">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
              >
                {safeContent}
              </ReactMarkdown>
            </div>
          )}

          {/* Action buttons — fade in on hover */}
          {safeContent && (
            <div className="flex items-center gap-1 pt-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                onClick={copyToClipboard}
                className="p-1.5 rounded hover:bg-(--card) transition-colors cursor-pointer"
                style={{ color: "var(--muted)" }}
                title="Copy"
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
              {/* TTS listen button */}
              <AudioPlayer text={safeContent} />
              {onRegenerate && (
                <button
                  onClick={onRegenerate}
                  className="p-1.5 rounded hover:bg-(--card) transition-colors cursor-pointer"
                  style={{ color: "var(--muted)" }}
                  title="Regenerate"
                >
                  <RotateCw className="w-3.5 h-3.5" />
                </button>
              )}
              {timestamp && (
                <span className="text-[11px] ml-1" style={{ color: "var(--muted)" }}>
                  {formatTime(timestamp)}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
