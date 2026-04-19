"use client";

import { createElement, useEffect, useRef, useState, type ComponentPropsWithoutRef } from "react";
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

function serializeTableToClipboard(table: HTMLTableElement): string {
  return Array.from(table.querySelectorAll("tr"))
    .map((row) =>
      Array.from(row.querySelectorAll("th, td"))
        .map((cell) => cell.textContent?.replace(/\s+/g, " ").trim() ?? "")
        .join("\t"),
    )
    .filter((line) => line.trim().length > 0)
    .join("\n");
}

function CopyableMarkdownTable({ children, className, ...props }: ComponentPropsWithoutRef<"table">) {
  const tableRef = useRef<HTMLTableElement | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopyTable = async () => {
    if (!tableRef.current) {
      return;
    }

    await navigator.clipboard.writeText(serializeTableToClipboard(tableRef.current));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div
      className="relative my-1 overflow-hidden rounded-[22px] border border-(--border) bg-(--card)"
      style={{ boxShadow: "var(--shadow-sm)" }}
    >
      <button
        type="button"
        onClick={handleCopyTable}
        className="absolute right-1.5 top-1.5 z-10 inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-md bg-background/90 text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground"
        aria-label={copied ? "Copied" : "Copy table"}
        title={copied ? "Copied" : "Copy table"}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <div className="overflow-x-auto">
        <table ref={tableRef} className={className} style={{ margin: 0 }} {...props}>
          {children}
        </table>
      </div>
    </div>
  );
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

  /* ── User message: right-aligned speech bubble ── */
  if (isUser) {
    return (
      <>
        <div className="group px-4 sm:px-6">
          <div className="mx-auto max-w-180 flex justify-end">
          <div className="flex max-w-[85%] flex-col items-end gap-2 sm:max-w-[75%]">
            {imageAttachments.length > 0 && (
              <div className="flex flex-wrap justify-end gap-2">
                {imageAttachments.map((attachment) => (
                  <button
                    key={attachment.id}
                    type="button"
                    onClick={() => setActiveImageAttachment(attachment)}
                    className="group/image overflow-hidden rounded-2xl cursor-pointer"
                    style={{ maxWidth: imageAttachments.length === 1 ? "360px" : "180px", boxShadow: "var(--shadow-sm)" }}
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
                className="px-4 py-3 text-[15px] leading-relaxed"
                style={{
                  background: "var(--user-bubble)",
                  borderRadius: "20px 20px 4px 20px",
                }}
              >
                {safeContent}
              </div>
            )}
            {timestamp && (
              <span className="text-[11px] text-(--muted) pr-1">
                {formatTime(timestamp)}
              </span>
            )}
          </div>
          </div>
        </div>
        {imageLightbox}
      </>
    );
  }

  /* ── Assistant message: left-aligned, clean layout ── */
  return (
    <div className="group relative px-4 sm:px-6">
      <div className="mx-auto max-w-180">
        {/* Content column */}
        <div className="space-y-3">
          {/* Tool Calls — pill-style inline display */}
          {visibleToolCalls.length > 0 && (
            <div className="space-y-1.5">
              {/* Summary pill */}
              <details className="group/tools" open={false}>
                <summary
                  className="inline-flex items-center gap-2 cursor-pointer select-none list-none rounded-xl px-3 py-1.5 transition-colors hover:bg-(--card-hover)"
                  style={{ background: "var(--badge-bg)" }}
                >
                  {isToolExecuting ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 text-(--muted)" />
                  ) : (
                    <WrenchIcon className="w-3.5 h-3.5 shrink-0 text-(--muted)" />
                  )}
                  <span className="text-xs font-medium text-(--badge-fg)">
                    {isToolExecuting
                      ? `Running ${visibleToolCalls.length} tool${visibleToolCalls.length > 1 ? 's' : ''}…`
                      : `Used ${visibleToolCalls.length} tool${visibleToolCalls.length > 1 ? 's' : ''}`}
                  </span>
                  <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open/tools:rotate-90 text-(--muted)" />
                </summary>

                <div
                  className="mt-2 rounded-2xl overflow-hidden"
                  style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
                >
                  {visibleToolCalls.map((tool, idx) => {
                    const hasApp = tool._meta?.ui?.httpUrl;
                    const isDone = tool.result !== undefined;
                    const isErr = tool.isError;
                    const riskColors: Record<string, string> = {
                      safe:      "#34d399",
                      sensitive: "#f59e0b",
                      critical:  "#ef4444",
                    };
                    const riskColor = riskColors[tool.color ?? tool.risk ?? "safe"] ?? "#34d399";
                    return (
                      <div key={tool.id}>
                        {idx > 0 && <div className="border-t border-(--border)" />}
                        <details className="group">
                          <summary className="flex items-center gap-2.5 px-4 py-2.5 cursor-pointer hover:bg-(--card-hover) transition-colors list-none">
                            <span className="shrink-0">
                              {!isDone ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin text-(--muted)" />
                              ) : isErr ? (
                                <span className="text-red-400 text-xs leading-none">✕</span>
                              ) : (
                                <span className="text-emerald-500 text-xs leading-none">✓</span>
                              )}
                            </span>
                            <span
                              title={`Risk: ${tool.risk ?? "safe"}`}
                              className="shrink-0 w-2 h-2 rounded-full"
                              style={{ background: riskColor }}
                            />
                            <span className="text-[13px] font-medium flex-1 text-foreground">
                              {tool.name.replace(/_/g, " ")}
                            </span>
                            {hasApp && (
                              <span className="text-[10px] px-2 py-0.5 rounded-lg bg-(--badge-bg) text-(--badge-fg) font-medium">
                                App
                              </span>
                            )}
                            <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open:rotate-90 text-(--muted)" />
                          </summary>

                          <div className="px-4 pb-3 space-y-2 border-t border-(--border)">
                            <div className="pt-2.5">
                              <div className="text-[10px] font-semibold uppercase tracking-wider mb-1.5 text-(--muted)">Input</div>
                              <pre className="text-[11px] p-3 rounded-xl overflow-x-auto" style={{ background: "var(--code-bg)", color: "var(--code-fg)" }}>
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
                                <div className="text-[10px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: isErr ? "#ef4444" : "var(--muted)" }}>Result</div>
                                <div
                                  className="text-[11px] p-3 rounded-xl max-h-32 overflow-y-auto whitespace-pre-wrap"
                                  style={{
                                    background: isErr ? "color-mix(in srgb, #ef4444 8%, var(--code-bg))" : "var(--code-bg)",
                                    color: isErr ? "#fca5a5" : "var(--code-fg)",
                                  }}
                                >
                                  {tool.result}
                                </div>
                              </div>
                            )}
                            {hasApp && (
                              <button
                                onClick={() => onOpenInPanel?.(tool)}
                                className="flex items-center gap-1.5 text-xs py-1 transition-colors cursor-pointer text-(--muted) hover:text-foreground"
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
            </div>
          )}

          {/* Reasoning — expandable thinking card */}
          {safeReasoning && (
            <details className="group/think" open={isToolExecuting}>
              <summary
                className="inline-flex items-center gap-2 cursor-pointer select-none list-none rounded-xl px-3 py-1.5 transition-colors hover:bg-(--card-hover)"
                style={{ background: "var(--badge-bg)" }}
              >
                <span className="text-xs">💭</span>
                <span className="text-xs font-medium text-(--badge-fg)">Thinking</span>
                <ChevronRight className="w-3 h-3 shrink-0 transition-transform group-open/think:rotate-90 text-(--muted)" />
              </summary>
              <div
                className="mt-2 rounded-2xl px-4 py-3"
                style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
              >
                <div className="whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-(--muted)">
                  {safeReasoning}
                </div>
              </div>
            </details>
          )}

          {/* Main markdown content */}
          {safeContent && (
            <div className="prose-chat">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={{
                  table({ children, ...props }) {
                    return <CopyableMarkdownTable {...props}>{children}</CopyableMarkdownTable>;
                  },
                }}
              >
                {safeContent}
              </ReactMarkdown>
            </div>
          )}

          {/* Action buttons — fade in on hover */}
          {safeContent && (
            <div className="flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
              <button
                onClick={copyToClipboard}
                className="p-1.5 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted)"
                title="Copy"
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
              <AudioPlayer text={safeContent} />
              {onRegenerate && (
                <button
                  onClick={onRegenerate}
                  className="p-1.5 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted)"
                  title="Regenerate"
                >
                  <RotateCw className="w-3.5 h-3.5" />
                </button>
              )}
              {timestamp && (
                <span className="text-[11px] ml-1.5 text-(--muted)">
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
