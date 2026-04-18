"use client";

import { createElement, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  Copy,
  Check,
  RotateCw,
  ChevronRight,
  Loader2,
  PanelRightOpen,
  WrenchIcon,
  ArrowUpRight,
  X,
} from "lucide-react";
import { ToolCall, UploadedFile } from "@/types";
import { AudioPlayer } from "@/components/AudioPlayer";
import {
  getAttachmentKind,
  getAttachmentIcon,
  formatFileSize,
} from "@/lib/file-utils";

function isPersistentToolCall(toolCall: ToolCall): boolean {
  return Boolean(toolCall._meta?.ui?.httpUrl);
}

interface AttachmentDocumentCardProps {
  attachment: UploadedFile;
}

function AttachmentDocumentCard({ attachment }: AttachmentDocumentCardProps) {
  const kind = getAttachmentKind(attachment.mime, attachment.name);
  const attachmentIcon = getAttachmentIcon(kind);
  const extension = attachment.name.split(".").pop()?.toUpperCase() || "FILE";
  const content = (
    <div className="attachment-card group/file p-3">
      <div className="attachment-card__icon text-(--accent)">
        {createElement(attachmentIcon, { className: "h-5 w-5" })}
      </div>
      <div className="min-w-0 flex-1 pr-1">
        <div className="truncate text-sm font-semibold text-foreground">{attachment.name}</div>
        <div className="mt-1 flex items-center gap-2 text-[11px] text-(--muted)">
          <span>{extension}</span>
          {attachment.size > 0 && (
            <>
              <span>·</span>
              <span>{formatFileSize(attachment.size)}</span>
            </>
          )}
        </div>
      </div>
      {attachment.url && <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-(--muted)" />}
    </div>
  );

  if (!attachment.url) {
    return content;
  }

  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noreferrer"
      className="block cursor-pointer"
    >
      {content}
    </a>
  );
}

type Props = {
  role: "user" | "assistant";
  content: string;
  attachments?: UploadedFile[];
  reasoning?: string;
  timestamp?: Date;
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  isContinuation?: boolean;
  onRegenerate?: () => void;
  /** Called when an MCP App should open in the side panel */
  onOpenInPanel?: (toolCall: ToolCall) => void;
};

export function MessageBubble({ 
  role, 
  content, 
  attachments,
  reasoning, 
  timestamp, 
  toolCalls,
  isToolExecuting,
  isContinuation,
  onRegenerate,
  onOpenInPanel
}: Props) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  const [activeImageAttachment, setActiveImageAttachment] = useState<UploadedFile | null>(null);
  
  // Ensure content is always a valid string
  const safeContent = typeof content === 'string' ? content : String(content || "");
  const safeReasoning = typeof reasoning === 'string' ? reasoning : String(reasoning || "");
  const visibleToolCalls = toolCalls?.filter((tool) => isToolExecuting || isPersistentToolCall(tool)) ?? [];
  const visibleAttachments = attachments ?? [];
  const imageAttachments = visibleAttachments.filter((attachment) => {
    const kind = getAttachmentKind(attachment.mime, attachment.name);
    return kind === "image" && Boolean(attachment.url);
  });
  const documentAttachments = visibleAttachments.filter((attachment) => {
    const kind = getAttachmentKind(attachment.mime, attachment.name);
    return kind !== "image" || !attachment.url;
  });

  useEffect(() => {
    if (!activeImageAttachment) {
      return undefined;
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveImageAttachment(null);
      }
    };

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [activeImageAttachment]);

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

  const imageLightbox = activeImageAttachment?.url ? (
    <div className="fixed inset-0 z-70 flex items-center justify-center p-4 sm:p-6">
      <button
        type="button"
        className="absolute inset-0 bg-black/88 backdrop-blur-md"
        onClick={() => setActiveImageAttachment(null)}
        aria-label="Close image preview"
      />
      <div className="relative w-full max-w-5xl">
        <div className="mb-3 flex items-center justify-between gap-4 text-white">
          <div className="min-w-0">
            <div className="truncate text-base font-semibold">{activeImageAttachment.name}</div>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={activeImageAttachment.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-2xl border border-white/12 bg-white/8 px-4 py-2 text-sm text-white/85 transition-colors hover:bg-white/12"
            >
              <ArrowUpRight className="h-4 w-4" />
              Open original
            </a>
            <button
              type="button"
              onClick={() => setActiveImageAttachment(null)}
              className="rounded-2xl border border-white/12 bg-white/8 p-2 text-white/85 transition-colors hover:bg-white/12"
              aria-label="Close image preview"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="relative h-[min(78vh,720px)] w-full overflow-hidden rounded-[28px] border border-white/10 bg-black/85 shadow-2xl">
          <img
            src={activeImageAttachment.url}
            alt={activeImageAttachment.name}
            className="absolute inset-0 h-full w-full object-contain"
          />
        </div>
      </div>
    </div>
  ) : null;

  /* ── User message: right-aligned pill bubble ── */
  if (isUser) {
    return (
      <>
        <div className="group flex justify-end px-3 py-2 sm:px-4">
          <div className="flex max-w-[88%] flex-col items-end gap-2 sm:max-w-[75%]">
            {imageAttachments.length > 0 && (
              <div className="flex flex-wrap justify-end gap-2">
                {imageAttachments.map((attachment) => (
                  <button
                    key={attachment.id}
                    type="button"
                    onClick={() => setActiveImageAttachment(attachment)}
                    className="group/image overflow-hidden rounded-2xl border border-(--border) shadow-sm cursor-pointer bg-(--card-hover)"
                    style={{ maxWidth: imageAttachments.length === 1 ? "360px" : "180px" }}
                  >
                    <img
                      src={attachment.url ?? ""}
                      alt={attachment.name}
                      className="block max-h-72 w-auto object-contain transition-transform duration-200 group-hover/image:scale-[1.02]"
                      style={{ maxWidth: imageAttachments.length === 1 ? "360px" : "180px" }}
                    />
                  </button>
                ))}
              </div>
            )}
            {documentAttachments.length > 0 && (
              <div className="grid w-full max-w-xl gap-2.5 sm:grid-cols-2">
                {documentAttachments.map((attachment) => (
                  <AttachmentDocumentCard key={attachment.id} attachment={attachment} />
                ))}
              </div>
            )}
            {safeContent && (
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
            )}
            {timestamp && (
              <span className="text-[11px]" style={{ color: "var(--muted)" }}>
                {formatTime(timestamp)}
              </span>
            )}
          </div>
        </div>
        {imageLightbox}
      </>
    );
  }

  /* ── Assistant message: left-aligned with avatar ── */
  return (
    <div className="group relative px-3 py-4 sm:px-4">
      <div className="mx-auto flex max-w-3xl gap-2.5 sm:gap-3">
        {/* AI avatar — hidden for continuation bubbles to avoid duplicate icons */}
        <div className="shrink-0 mt-0.5">
          {isContinuation ? (
            <div className="h-6 w-6 sm:h-7 sm:w-7" />
          ) : (
            <div
              className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold text-white sm:h-7 sm:w-7 sm:text-[11px]"
              style={{ background: "linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 70%, #000))" }}
            >
              AI
            </div>
          )}
        </div>

        {/* Content column */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Tool Calls — grouped collapsible */}
          {visibleToolCalls.length > 0 && (
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
                    ? `Running ${visibleToolCalls.length} tool${visibleToolCalls.length > 1 ? 's' : ''}…`
                    : `Used ${visibleToolCalls.length} tool${visibleToolCalls.length > 1 ? 's' : ''}`}
                </span>
                <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open/tools:rotate-90" />
              </summary>

              <div
                className="mt-1.5 rounded-lg overflow-hidden"
                style={{ border: "1px solid var(--border)", background: "var(--card)" }}
              >
                {visibleToolCalls.map((tool, idx) => {
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
            <div className="prose-chat">
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
            <div className="flex items-center gap-1 pt-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
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
