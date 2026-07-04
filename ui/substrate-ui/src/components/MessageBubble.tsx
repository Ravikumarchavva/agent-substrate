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
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Loader2,
  PanelRightOpen,
  WrenchIcon,
  ArrowUpRight,
  X,
  Download,
} from "lucide-react";
import { ToolCall, UploadedFile } from "@/types";
import { AudioPlayer } from "@/components/AudioPlayer";
import { Mermaid } from "@/components/Mermaid";
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

function preprocessMarkdown(content: string): string {
  if (!content) return "";

  const mathBlocks: string[] = [];

  // 1. Temporarily extract block math $$...$$
  let processed = content.replace(/\$\$([\s\S]*?)\$\$/g, (_, match) => {
    mathBlocks.push(`$$${match}$$`);
    return `__MATH_BLOCK_${mathBlocks.length - 1}__`;
  });

  // 2. Temporarily extract inline math \(...\) and map to single $
  processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (_, match) => {
    mathBlocks.push(`$${match}$`);
    return `__MATH_BLOCK_${mathBlocks.length - 1}__`;
  });

  // 3. Temporarily extract block math \[...\] and map to $$
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, match) => {
    mathBlocks.push(`$$${match}$$`);
    return `__MATH_BLOCK_${mathBlocks.length - 1}__`;
  });

  // 4. Escape all remaining raw '$' signs (these are guaranteed to be currency)
  processed = processed.replace(/\$/g, '\\$');

  // 5. Restore the safe math blocks with $ and $$ delimiters
  processed = processed.replace(/__MATH_BLOCK_(\d+)__/g, (_, index) => {
    return mathBlocks[parseInt(index, 10)];
  });

  return processed;
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
    <div className="relative my-4 w-full">
      <button
        type="button"
        onClick={handleCopyTable}
        className="btn-icon absolute right-1.5 top-2.5 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground"
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

function CopyablePre({ children, className, ...props }: ComponentPropsWithoutRef<"pre">) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopyCode = async () => {
    if (!preRef.current) {
      return;
    }

    const text = preRef.current.textContent || "";
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="relative my-4 w-full group/code">
      <button
        type="button"
        onClick={handleCopyCode}
        className="btn-icon absolute right-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-(--muted) bg-(--card)/85 backdrop-blur-sm border border-(--border) opacity-0 group-hover/code:opacity-100 transition-all hover:bg-(--card-hover) hover:text-foreground shadow-sm"
        aria-label={copied ? "Copied" : "Copy code"}
        title={copied ? "Copied" : "Copy code"}
        style={{ minWidth: 'unset', minHeight: 'unset' }}
      >
        {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre ref={preRef} className={className} style={{ margin: 0 }} {...props}>
        {children}
      </pre>
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
  const [activeImageIndex, setActiveImageIndex] = useState<number>(-1);
  const [inlineImageIndex, setInlineImageIndex] = useState<number>(0);
  const [isCollapsed, setIsCollapsed] = useState(true);
  
  // Ensure content is always a valid string
  const safeContent = typeof content === 'string' ? content : String(content || "");
  const safeReasoning = typeof reasoning === 'string' ? reasoning : String(reasoning || "");
  const isLongUserMessage = isUser && (safeContent.length > 300 || safeContent.split("\n").length > 5);
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
    if (activeImageIndex < 0) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveImageIndex(-1);
      } else if (event.key === "ArrowLeft") {
        setActiveImageIndex((prev) => (prev > 0 ? prev - 1 : imageAttachments.length - 1));
      } else if (event.key === "ArrowRight") {
        setActiveImageIndex((prev) => (prev < imageAttachments.length - 1 ? prev + 1 : 0));
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeImageIndex, imageAttachments]);

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

  const handleDownload = async (url: string, name: string) => {
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      window.open(url, "_blank");
    }
  };

  const currentActiveAttachment = activeImageIndex >= 0 && activeImageIndex < imageAttachments.length
    ? imageAttachments[activeImageIndex]
    : null;  const imageLightbox = currentActiveAttachment?.url ? (
    <div className="ravi-fade-in fixed inset-0 z-70 flex flex-col items-center justify-center p-3 select-none bg-black/60 backdrop-blur-sm">
      <button
        type="button"
        className="absolute inset-0 bg-transparent cursor-default"
        onClick={() => setActiveImageIndex(-1)}
        aria-label="Close image preview"
      />
      <div className="relative w-full h-full flex flex-col items-center justify-center">
        {/* Floating Left Filename and Pagination details */}
        <div className="absolute top-4 left-4 sm:left-6 flex items-center gap-2.5 z-50">
          <div className="truncate text-sm sm:text-sm font-semibold text-white/90 max-w-[120px] sm:max-w-xs" title={currentActiveAttachment.name}>
            {currentActiveAttachment.name}
          </div>
          {imageAttachments.length > 1 && (
            <span className="shrink-0 text-[10px] bg-white/10 text-white/70 px-2 py-2 rounded-full font-medium border border-white/5">
              {activeImageIndex + 1} of {imageAttachments.length}
            </span>
          )}
        </div>

        {/* Floating Right perfectly-centered Download and Close Icon buttons */}
        <div className="absolute top-4 right-4 sm:right-6 flex items-center gap-2 z-50">
          <button
            type="button"
            onClick={() => handleDownload(currentActiveAttachment.url!, currentActiveAttachment.name)}
            className="flex items-center justify-center h-8 w-8 rounded-xl border border-white/10 bg-black/40 text-white/80 transition-all hover:bg-neutral-800 hover:scale-105 active:scale-95 cursor-pointer"
            title="Download image"
          >
            <Download className="h-4 w-4 shrink-0" />
          </button>
          <button
            type="button"
            onClick={() => setActiveImageIndex(-1)}
            className="flex items-center justify-center h-8 w-8 rounded-xl border border-white/10 bg-black/40 text-white/80 transition-all hover:bg-red-500 hover:text-white hover:border-red-500 hover:scale-105 active:scale-95 cursor-pointer"
            aria-label="Close image preview"
          >
            <X className="h-4 w-4 shrink-0" />
          </button>
        </div>

        {/* Clean Center Image: 100% responsive, fills ~75-80% of viewport area */}
        <div className="relative w-full h-[65vh] sm:h-[75vh] max-w-[90vw] flex items-center justify-center mt-12 sm:mt-16 animate-in fade-in zoom-in-95 duration-200">
          <img
            src={currentActiveAttachment.url}
            alt={currentActiveAttachment.name}
            className="max-w-full max-h-full object-contain rounded-xl"
          />
        </div>

        {/* Viewport Floating Carousel Arrows */}
        {imageAttachments.length > 1 && (
          <>
            {/* Left Nav Button */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setActiveImageIndex((prev) => (prev > 0 ? prev - 1 : imageAttachments.length - 1));
              }}
              className="absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 flex h-10 w-10 sm:h-12 sm:w-12 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-black/50 text-white backdrop-blur-md transition-all duration-300 hover:bg-neutral-800 hover:scale-105 active:scale-95 shadow-md z-40 animate-in slide-in-from-left-6 duration-300"
              aria-label="Previous image"
            >
              <ChevronLeft className="h-5.5 w-5.5 sm:h-6 sm:w-6" />
            </button>

            {/* Right Nav Button */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setActiveImageIndex((prev) => (prev < imageAttachments.length - 1 ? prev + 1 : 0));
              }}
              className="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 flex h-10 w-10 sm:h-12 sm:w-12 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-black/50 text-white backdrop-blur-md transition-all duration-300 hover:bg-neutral-800 hover:scale-105 active:scale-95 shadow-md z-40 animate-in slide-in-from-right-6 duration-300"
              aria-label="Next image"
            >
              <ChevronRight className="h-5.5 w-5.5 sm:h-6 sm:w-6" />
            </button>
          </>
        )}

        {/* Carousel Bottom Dot Indicators - 25% smaller */}
        {imageAttachments.length > 1 && (
          <div className="absolute bottom-2 sm:bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 z-50 bg-neutral-900/80 backdrop-blur-md border border-white/10 rounded-full px-3 py-1.5 shadow-md">
            {imageAttachments.map((_, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setActiveImageIndex(idx)}
                className={`h-1.5 cursor-pointer rounded-full transition-all duration-300 ${
                  idx === activeImageIndex
                    ? "bg-white w-3.5"
                    : "bg-white/30 hover:bg-white/60 w-1.5"
                }`}
                aria-label={`Go to image ${idx + 1}`}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  ) : null;

  const renderImageGallery = () => {
    if (imageAttachments.length === 0) return null;

    if (imageAttachments.length === 1) {
      const attachment = imageAttachments[0];
      return (
        <div className="w-full max-w-[720px] pt-1">
          <button
            type="button"
            onClick={() => setActiveImageIndex(0)}
            className="group/image relative block w-full overflow-hidden rounded-2xl cursor-pointer border border-(--border) bg-(--card) shadow-md transition-all duration-300 hover:shadow-lg focus:outline-none"
          >
            <img
              src={attachment.url ?? ""}
              alt={attachment.name}
              className="block w-full max-h-[480px] sm:max-h-[520px] object-contain transition-transform duration-300 group-hover/image:scale-[1.015]"
            />
          </button>
        </div>
      );
    }

    const currentAttachment = imageAttachments[inlineImageIndex] || imageAttachments[0];

    return (
      <div className="w-full max-w-[720px] pt-1">
        <div className="group/carousel relative w-full overflow-hidden rounded-2xl border border-(--border) bg-(--card) shadow-md transition-all duration-300 hover:shadow-lg select-none">
          <button
            type="button"
            onClick={() => setActiveImageIndex(inlineImageIndex)}
            className="block w-full text-left focus:outline-none cursor-pointer"
          >
            <img
              src={currentAttachment.url ?? ""}
              alt={currentAttachment.name}
              className="block w-full max-h-[480px] sm:max-h-[520px] object-contain transition-transform duration-300 hover:scale-[1.015]"
            />
          </button>

          {/* Left Arrow */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setInlineImageIndex((prev) => (prev > 0 ? prev - 1 : imageAttachments.length - 1));
            }}
            className="absolute left-3 top-1/2 -translate-y-1/2 flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-black/45 text-white backdrop-blur-md opacity-0 group-hover/carousel:opacity-100 transition-opacity duration-200 hover:bg-black/70 hover:scale-105 active:scale-95 z-10"
            aria-label="Previous slide"
          >
            <ChevronLeft className="h-5 w-5" />
          </button>

          {/* Right Arrow */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setInlineImageIndex((prev) => (prev < imageAttachments.length - 1 ? prev + 1 : 0));
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-black/45 text-white backdrop-blur-md opacity-0 group-hover/carousel:opacity-100 transition-opacity duration-200 hover:bg-black/70 hover:scale-105 active:scale-95 z-10"
            aria-label="Next slide"
          >
            <ChevronRight className="h-5 w-5" />
          </button>

          {/* Dots Indicator */}
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1.5 z-10 bg-black/35 backdrop-blur-md px-2.5 py-1 rounded-full border border-white/5 opacity-80 group-hover/carousel:opacity-100 transition-opacity duration-200">
            {imageAttachments.map((_, idx) => (
              <button
                key={idx}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setInlineImageIndex(idx);
                }}
                className={`h-1.5 w-1.5 cursor-pointer rounded-full transition-all ${
                  idx === inlineImageIndex ? "bg-white w-3" : "bg-white/50 hover:bg-white/80"
                }`}
                aria-label={`Go to slide ${idx + 1}`}
              />
            ))}
          </div>
        </div>
      </div>
    );
  };

  /* ── User message: right-aligned speech bubble ── */
  if (isUser) {
    return (
      <>
        <div className="ravi-fade-up group px-4 sm:px-6">
          <div className="mx-auto max-w-(--chat-width) flex justify-end">
          <div className="flex max-w-[85%] flex-col items-end gap-2 sm:max-w-[75%]">
            {imageAttachments.length > 0 && (
              <div className="flex justify-end w-full">
                {renderImageGallery()}
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
                className="user-bubble-md overflow-hidden flex flex-col"
                style={{
                  background: "var(--user-bubble)",
                  borderRadius: "20px 20px 4px 20px",
                }}
              >
                <div
                  className={`px-4 pt-3 text-[15px] leading-relaxed relative ${
                    isLongUserMessage && isCollapsed ? "max-h-[140px] overflow-hidden" : "pb-3"
                  }`}
                >
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      p({ children }) { return <p className="mb-1 last:mb-0">{children}</p>; },
                      ul({ children }) { return <ul className="list-disc pl-4 mb-1 space-y-0.5">{children}</ul>; },
                      ol({ children }) { return <ol className="list-decimal pl-4 mb-1 space-y-0.5">{children}</ol>; },
                      li({ children }) { return <li className="leading-snug">{children}</li>; },
                      strong({ children }) { return <strong className="font-semibold">{children}</strong>; },
                      code({ children }) { return <code className="bg-black/20 rounded px-1 text-[13px] font-mono">{children}</code>; },
                    }}
                  >
                    {safeContent}
                  </ReactMarkdown>
                  {isLongUserMessage && isCollapsed && (
                    <div 
                      className="absolute bottom-0 left-0 right-0 h-12 pointer-events-none" 
                      style={{
                        background: "linear-gradient(to top, var(--user-bubble) 20%, transparent 100%)"
                      }}
                    />
                  )}
                </div>
                {isLongUserMessage && (
                  <div className="px-4 pb-2.5 pt-1 flex justify-start">
                    <button
                      type="button"
                      onClick={() => setIsCollapsed(!isCollapsed)}
                      className="text-[11px] font-semibold text-(--muted) hover:text-foreground transition-colors cursor-pointer flex items-center gap-1 select-none btn-icon"
                      style={{ minHeight: "unset", minWidth: "unset" }}
                    >
                      {isCollapsed ? (
                        <>
                          Show more
                          <ChevronDown className="w-3.5 h-3.5 shrink-0" />
                        </>
                      ) : (
                        <>
                          Show less
                          <ChevronUp className="w-3.5 h-3.5 shrink-0" />
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            )}
            {(safeContent || timestamp) && (
              <div className="flex items-center gap-2 mt-1 pr-1">
                {safeContent && (
                  <div className="opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 flex items-center">
                    <button
                      type="button"
                      onClick={copyToClipboard}
                      className="btn-icon flex items-center justify-center w-6 h-6 rounded-md hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted)"
                      title="Copy message"
                      style={{ minWidth: "unset", minHeight: "unset" }}
                    >
                      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                )}
                {timestamp && (
                  <span className="text-[11px] text-(--muted) select-none">
                    {formatTime(timestamp)}
                  </span>
                )}
              </div>
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
    <>
      <div className="ravi-fade-up group relative px-4 sm:px-6">
      <div className="mx-auto max-w-(--chat-width)">
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
                  pre({ children, className }) {
                    // ReactMarkdown wraps fenced code in <pre><code>. For mermaid we
                    // must render OUTSIDE the <pre> — otherwise the .prose-chat pre
                    // card styling (bg/border/padding) boxes the diagram. Detect a
                    // mermaid child and render the diagram unwrapped.
                    const child = Array.isArray(children) ? children[0] : children;
                    const childClass =
                      (child as { props?: { className?: string } } | undefined)?.props
                        ?.className || "";
                    if (/language-mermaid/.test(childClass)) {
                      const raw = (child as { props?: { children?: unknown } }).props
                        ?.children;
                      return <Mermaid chart={String(raw).replace(/\n$/, "")} />;
                    }
                    return <CopyablePre className={className}>{children}</CopyablePre>;
                  },
                  code({ className, children }) {
                    return <code className={className}>{children}</code>;
                  },
                }}
              >
                {preprocessMarkdown(safeContent)}
              </ReactMarkdown>
            </div>
          )}

          {/* Generated/Attached Images (left-aligned for assistant) */}
          {imageAttachments.length > 0 && (
            <div className="flex justify-start w-full">
              {renderImageGallery()}
            </div>
          )}

          {/* Generated/Attached Documents (left-aligned for assistant) */}
          {documentAttachments.length > 0 && (
            <div className="grid w-full max-w-xl gap-2.5 sm:grid-cols-2 pt-1">
              {documentAttachments.map((attachment) => (
                <AttachmentDocumentCard key={attachment.id} attachment={attachment} />
              ))}
            </div>
          )}

          {/* Action buttons — fade in on hover */}
          {safeContent && (
            <div className="flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
              <button
                onClick={copyToClipboard}
                className="btn-icon flex items-center justify-center w-6 h-6 rounded-md hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted)"
                title="Copy"
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
              <AudioPlayer text={safeContent} />
              {onRegenerate && (
                <button
                  onClick={onRegenerate}
                  className="btn-icon flex items-center justify-center w-6 h-6 rounded-md hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted)"
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
    {imageLightbox}
  </>
  );
}
