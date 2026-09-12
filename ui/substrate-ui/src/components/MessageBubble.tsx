"use client";

import { createElement, useCallback, useEffect, useMemo, useRef, useState, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
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
  FileText,
  FileSpreadsheet,
  Presentation,
} from "lucide-react";
import { CitationSource, ToolCall, UploadedFile } from "@/types";
import { AudioPlayer } from "@/components/AudioPlayer";
import { Mermaid } from "@/components/Mermaid";
import { CitationChip } from "@/components/CitationChip";
import { SourcesStrip } from "@/components/SourcesStrip";
import { buildWorkspaceFileUrl } from "@/lib/api/_client";
import {
  getAttachmentKind,
  getAttachmentIcon,
  formatFileSize,
} from "@/lib/file-utils";

function isPersistentToolCall(toolCall: ToolCall): boolean {
  return Boolean(toolCall._meta?.ui?.httpUrl);
}

// react-markdown sanitizes URLs by default, blanking any scheme not in its
// allow-list (http/https/mailto/…). Our code-interpreter refs use a custom
// `sandbox:` scheme, so pass those through untouched and defer to the default
// (XSS-safe) transform for everything else.
function sandboxUrlTransform(url: string): string {
  return url.startsWith("sandbox:") || url.startsWith("citation:")
    ? url
    : defaultUrlTransform(url);
}

// Rewrites `[1]`/`[1, 2]` citation markers into `[1](citation:1)` links the
// `a` override below turns into clickable CitationChips — but ONLY when
// every index is in `validIndices` (a message's real, machine-built source
// map). An out-of-range or model-hallucinated `[9]` is left as literal
// text: there is no code path here that can produce a chip pointing at a
// source that doesn't exist.
//
// Fenced/inline code is protected first (extract → rewrite → restore, same
// idiom preprocessMarkdown uses above for math) so a code sample containing
// e.g. `arr[1]` is never mistaken for a citation.
export function linkifyCitations(content: string, validIndices: Set<number>): string {
  if (!content || validIndices.size === 0) return content;

  const codeBlocks: string[] = [];
  let processed = content.replace(/```[\s\S]*?```/g, (match) => {
    codeBlocks.push(match);
    return `__CITATION_CODE_${codeBlocks.length - 1}__`;
  });
  processed = processed.replace(/`[^`\n]*`/g, (match) => {
    codeBlocks.push(match);
    return `__CITATION_CODE_${codeBlocks.length - 1}__`;
  });

  processed = processed.replace(
    /(!?)\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?!\()/g,
    (fullMatch: string, bang: string, numbers: string) => {
      if (bang) return fullMatch; // ![1] — image syntax, not a citation
      const indices = numbers.split(",").map((n) => parseInt(n.trim(), 10));
      if (!indices.every((n) => validIndices.has(n))) return fullMatch;
      return indices.map((n) => `[${n}](citation:${n})`).join("");
    }
  );

  processed = processed.replace(/__CITATION_CODE_(\d+)__/g, (_, index) => {
    return codeBlocks[parseInt(index, 10)];
  });

  return processed;
}

/**
 * A model-embedded image from markdown. Resolves `sandbox:<path>` refs to the
 * thread's workspace file endpoint, and degrades gracefully — an empty/
 * unresolvable src renders nothing (avoids React's empty-`src` warning), and a
 * failed load (e.g. the model referenced a file it didn't actually save)
 * collapses to a small caption instead of the browser's broken-image icon.
 */
function MarkdownImage({
  src,
  alt,
  threadId,
  onOpen,
}: {
  src?: string;
  alt?: string;
  threadId?: string | null;
  onOpen?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const raw = typeof src === "string" ? src.trim() : "";
  const resolved = raw.startsWith("sandbox:")
    ? threadId
      ? buildWorkspaceFileUrl(threadId, raw)
      : ""
    : raw;

  if (!resolved) return null;
  if (failed) {
    return (
      <span className="my-1 inline-block text-xs italic text-(--muted)">
        {alt ? `${alt} (image unavailable)` : "Image unavailable"}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen?.()}
      className="group/inline-image block cursor-pointer text-left focus:outline-none"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={resolved}
        alt={alt ?? ""}
        onError={() => setFailed(true)}
        className="my-2 max-h-[520px] w-auto max-w-full rounded-2xl border border-(--border) bg-(--card) shadow-md transition-shadow duration-200 group-hover/inline-image:shadow-lg"
      />
    </button>
  );
}

// Per-type icon/color/label for a generated-file card (the `sandbox:` link
// card below) — matches office-suite convention (PowerPoint orange, Excel
// green, Word blue) so the file type reads at a glance, the same way
// Claude's own artifact cards do.
function officeFileBadge(name: string): {
  Icon: typeof FileText;
  label: string;
  badgeClass: string;
} {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pptx" || ext === "ppt") {
    return { Icon: Presentation, label: "Presentation", badgeClass: "bg-orange-500/15 text-orange-500" };
  }
  if (ext === "xlsx" || ext === "xls" || ext === "csv") {
    return { Icon: FileSpreadsheet, label: "Spreadsheet", badgeClass: "bg-emerald-500/15 text-emerald-500" };
  }
  if (ext === "docx" || ext === "doc") {
    return { Icon: FileText, label: "Document", badgeClass: "bg-blue-500/15 text-blue-500" };
  }
  if (ext === "md" || ext === "markdown") {
    return { Icon: FileText, label: "Document", badgeClass: "bg-teal-500/15 text-teal-500" };
  }
  return { Icon: FileText, label: "File", badgeClass: "bg-(--muted)/15 text-(--muted)" };
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

  // 2. Temporarily extract inline math \(...\) — mapped to $$ (double
  //    dollar), not single $: ReactMarkdown's remarkMath is configured with
  //    singleDollarTextMath: false (see below) precisely so a bare "$" in
  //    prose is always currency, never a math delimiter, so a single-$
  //    restoration here would silently stop being recognized as math too.
  //    micromark-extension-math accepts $$…$$ inline (same line), not just
  //    as a block, so this still renders correctly.
  processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (_, match) => {
    mathBlocks.push(`$$${match}$$`);
    return `__MATH_BLOCK_${mathBlocks.length - 1}__`;
  });

  // 3. Temporarily extract block math \[...\] and map to $$
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, match) => {
    mathBlocks.push(`$$${match}$$`);
    return `__MATH_BLOCK_${mathBlocks.length - 1}__`;
  });

  // Step 4 used to backslash-escape every remaining bare "$" here so
  // remark-math wouldn't misparse currency as inline math — with
  // singleDollarTextMath: false that ambiguity no longer exists, so bare
  // dollar amounts are left completely alone (removing this step also
  // removes a real bug: a stray unmatched "\$" was visibly leaking into
  // rendered output as a literal backslash on some inputs).

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
    return <div className="w-full sm:w-72">{content}</div>;
  }

  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noreferrer"
      className="block w-full cursor-pointer sm:w-72"
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
  /** Active thread id — resolves `sandbox:<path>` markdown refs to workspace files. */
  threadId?: string | null;
  /** Open a code-interpreter file artifact (sandbox: ref) in the side panel. */
  onOpenArtifact?: (path: string, fileName: string) => void;
  /** Grounded source references from knowledge_search — inline [n] chips +
   * a "Sources" strip. See src/types/citations.ts. */
  sources?: CitationSource[];
  /** Open a citation's source file in the side panel, at its cited page. */
  onOpenSource?: (source: CitationSource) => void;
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
  onOpenInPanel,
  threadId,
  onOpenArtifact,
  sources,
  onOpenSource,
}: Props) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState<number>(-1);
  // Which gallery the lightbox is currently showing — "main" (user uploads /
  // model-curated images), "tool" (chart/table crops from knowledge_search
  // etc., normally collapsed under "N charts generated"), or "inline" (every
  // image the model embedded directly in this message's markdown text via
  // ![alt](sandbox:...), navigable among each other — see
  // inlineImageAttachments above for why this isn't matched against the
  // "tool" gallery instead). All three share one lightbox instead of each
  // needing its own copy of the carousel/keyboard-nav logic below.
  const [activeImageSource, setActiveImageSource] = useState<"main" | "tool" | "inline">("main");
  const [inlineImageIndex, setInlineImageIndex] = useState<number>(0);
  const [isCollapsed, setIsCollapsed] = useState(true);

  // Ensure content is always a valid string
  const safeContent = typeof content === 'string' ? content : String(content || "");
  const safeReasoning = typeof reasoning === 'string' ? reasoning : String(reasoning || "");
  // Every markdown image ref in THIS message's raw content, in document
  // order, deduped — the navigable list for the "inline" lightbox below.
  // Not matched against toolImageAttachments: that gallery's `name` is a
  // synthetic sequential name the backend assigns when auto-capturing tool
  // output (`code_interpreter-0.png`, `-1.png`, ...), decoupled from
  // whatever real filename the model wrote and references here via
  // `sandbox:...` — matching by name only works by coincidence. Extracting
  // straight from this message's own markdown instead is correct regardless
  // of naming on either side.
  const inlineImageRefs = useMemo(() => {
    const refs: string[] = [];
    const seen = new Set<string>();
    const re = /!\[[^\]]*\]\(([^)\s]+)\)/g;
    let match: RegExpExecArray | null;
    while ((match = re.exec(safeContent)) !== null) {
      const ref = match[1];
      if (!seen.has(ref)) {
        seen.add(ref);
        refs.push(ref);
      }
    }
    return refs;
  }, [safeContent]);
  const inlineImageAttachments = useMemo(
    () =>
      inlineImageRefs.map((raw) => {
        const resolved = raw.startsWith("sandbox:")
          ? threadId
            ? buildWorkspaceFileUrl(threadId, raw)
            : ""
          : raw;
        return { id: `inline:${raw}`, name: raw, mime: "image/*", size: 0, url: resolved };
      }),
    [inlineImageRefs, threadId]
  );
  const sourceByIndex = useMemo(
    () => new Map((sources ?? []).map((s) => [s.index, s])),
    [sources]
  );
  const validCitationIndices = useMemo(
    () => new Set(sourceByIndex.keys()),
    [sourceByIndex]
  );
  const isLongUserMessage = isUser && (safeContent.length > 300 || safeContent.split("\n").length > 5);
  const visibleToolCalls = toolCalls?.filter((tool) => isToolExecuting || isPersistentToolCall(tool)) ?? [];
  const visibleAttachments = attachments ?? [];
  // Tool-generated images (code_interpreter plots, knowledge_search chart/
  // table crops) render collapsed, separate from user uploads / model-curated
  // images which render in the main gallery. Deduped by url: these are
  // data: URIs (see agents/runtime/context/tool.py), so identical bytes
  // always produce an identical string — the same image can legitimately be
  // attached by multiple tool calls in one turn (e.g. the model asking
  // knowledge_search several differently-worded questions that each surface
  // the same chart), and without this it renders as N duplicate thumbnails.
  const toolImageAttachments = visibleAttachments
    .filter((attachment) => {
      const kind = getAttachmentKind(attachment.mime, attachment.name);
      return attachment.origin === "tool" && kind === "image" && Boolean(attachment.url);
    })
    .filter(
      (attachment, index, arr) =>
        arr.findIndex((a) => a.url === attachment.url) === index
    );
  const imageAttachments = visibleAttachments.filter((attachment) => {
    const kind = getAttachmentKind(attachment.mime, attachment.name);
    return attachment.origin !== "tool" && kind === "image" && Boolean(attachment.url);
  });
  const documentAttachments = visibleAttachments.filter((attachment) => {
    const kind = getAttachmentKind(attachment.mime, attachment.name);
    return attachment.origin !== "tool" && (kind !== "image" || !attachment.url);
  });

  // The lightbox operates over whichever gallery is currently open — see
  // activeImageSource above.
  const activeList =
    activeImageSource === "tool"
      ? toolImageAttachments
      : activeImageSource === "inline"
        ? inlineImageAttachments
        : imageAttachments;

  const openLightbox = useCallback((source: "main" | "tool", index: number) => {
    setActiveImageSource(source);
    setActiveImageIndex(index);
  }, []);

  const openInlineLightbox = useCallback((index: number) => {
    setActiveImageSource("inline");
    setActiveImageIndex(index);
  }, []);

  useEffect(() => {
    if (activeImageIndex < 0) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveImageIndex(-1);
      } else if (event.key === "ArrowLeft") {
        setActiveImageIndex((prev) => (prev > 0 ? prev - 1 : activeList.length - 1));
      } else if (event.key === "ArrowRight") {
        setActiveImageIndex((prev) => (prev < activeList.length - 1 ? prev + 1 : 0));
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeImageIndex, activeList]);

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

  const currentActiveAttachment = activeImageIndex >= 0 && activeImageIndex < activeList.length
    ? activeList[activeImageIndex]
    : null; const imageLightbox = currentActiveAttachment?.url ? (
      <div className="substrate-fade-in fixed inset-0 z-70 flex flex-col items-center justify-center p-3 select-none bg-black/60 backdrop-blur-sm">
        <button
          type="button"
          className="absolute inset-0 bg-transparent cursor-default"
          onClick={() => setActiveImageIndex(-1)}
          aria-label="Close image preview"
        />
        <div
          className="relative w-full h-full flex flex-col items-center justify-center"
          onClick={(e) => {
            // This wrapper spans the whole viewport too (w-full h-full), so it
            // sits on top of — and swallows clicks meant for — the invisible
            // backdrop button above whenever the click lands on empty space
            // around the image rather than a real interactive child (image,
            // buttons, arrows). Only close when the click target is this
            // wrapper itself, not a bubbled click from one of those children.
            if (e.target === e.currentTarget) setActiveImageIndex(-1);
          }}
        >
          {/* Floating Left Filename and Pagination details */}
          <div className="absolute top-4 left-4 sm:left-6 flex items-center gap-2.5 z-50">
            <div className="truncate text-sm sm:text-sm font-semibold text-white/90 max-w-[120px] sm:max-w-xs" title={currentActiveAttachment.name}>
              {currentActiveAttachment.name}
            </div>
            {activeList.length > 1 && (
              <span className="shrink-0 text-[10px] bg-white/10 text-white/70 px-2 py-2 rounded-full font-medium border border-white/5">
                {activeImageIndex + 1} of {activeList.length}
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

          {/* Clean Center Image: 100% responsive, fills ~75-80% of viewport area.
            Also w-full, so it's its own "empty space" trap distinct from the
            outer wrapper above — same target-check guard needed here too, or
            a click in this box's letterboxed padding (around a narrower
            image) never reaches either close handler. */}
          <div
            className="relative w-full h-[65vh] sm:h-[75vh] max-w-[90vw] flex items-center justify-center mt-12 sm:mt-16 animate-in fade-in zoom-in-95 duration-200"
            onClick={(e) => {
              if (e.target === e.currentTarget) setActiveImageIndex(-1);
            }}
          >
            <img
              src={currentActiveAttachment.url}
              alt={currentActiveAttachment.name}
              className="max-w-full max-h-full object-contain rounded-xl"
            />
          </div>

          {/* Viewport Floating Carousel Arrows */}
          {activeList.length > 1 && (
            <>
              {/* Left Nav Button */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setActiveImageIndex((prev) => (prev > 0 ? prev - 1 : activeList.length - 1));
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
                  setActiveImageIndex((prev) => (prev < activeList.length - 1 ? prev + 1 : 0));
                }}
                className="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 flex h-10 w-10 sm:h-12 sm:w-12 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-black/50 text-white backdrop-blur-md transition-all duration-300 hover:bg-neutral-800 hover:scale-105 active:scale-95 shadow-md z-40 animate-in slide-in-from-right-6 duration-300"
                aria-label="Next image"
              >
                <ChevronRight className="h-5.5 w-5.5 sm:h-6 sm:w-6" />
              </button>
            </>
          )}

          {/* Carousel Bottom Dot Indicators - 25% smaller */}
          {activeList.length > 1 && (
            <div className="absolute bottom-2 sm:bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 z-50 bg-neutral-900/80 backdrop-blur-md border border-white/10 rounded-full px-3 py-1.5 shadow-md">
              {activeList.map((_, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setActiveImageIndex(idx)}
                  className={`h-1.5 cursor-pointer rounded-full transition-all duration-300 ${idx === activeImageIndex
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
            onClick={() => openLightbox("main", 0)}
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
            onClick={() => openLightbox("main", inlineImageIndex)}
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
                className={`h-1.5 w-1.5 cursor-pointer rounded-full transition-all ${idx === inlineImageIndex ? "bg-white w-3" : "bg-white/50 hover:bg-white/80"
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
        <div className="substrate-fade-up group px-4 sm:px-6">
          <div className="mx-auto max-w-(--chat-width) flex justify-end">
            <div className="flex max-w-[85%] flex-col items-end gap-2 sm:max-w-[75%]">
              {imageAttachments.length > 0 && (
                <div className="flex justify-end w-full">
                  {renderImageGallery()}
                </div>
              )}
              {documentAttachments.length > 0 && (
                <div className="flex w-full max-w-xl flex-wrap justify-end gap-2.5">
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
                    className={`px-4 pt-3 text-[15px] leading-relaxed relative ${isLongUserMessage && isCollapsed ? "max-h-[140px] overflow-hidden" : "pb-3"
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
      <div className="substrate-fade-up group relative px-4 sm:px-6">
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
                        ? `Running tools… ${visibleToolCalls.filter((t) => t.result !== undefined).length}/${visibleToolCalls.length}`
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
                        safe: "#34d399",
                        sensitive: "#f59e0b",
                        critical: "#ef4444",
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
                  // singleDollarTextMath: false — a bare `$...$` is currency,
                  // not math, essentially always in this app (financial RAG
                  // answers are full of dollar amounts). Left on, remark-math
                  // greedily pairs the first `$` it sees with the NEXT `$`
                  // anywhere later in the message as one inline-math span —
                  // observed pairing across several sentences of a 10-Q
                  // summary, which KaTeX then rendered as one formula
                  // (math mode collapses inter-word spacing, and any content
                  // it couldn't parse — like a citation's `(citation:1)` —
                  // leaked out as raw unrendered text). `$$...$$` block math
                  // and `\(...\)`/`\[...\]` (preprocessMarkdown below) still
                  // work for a model that intentionally wants real math.
                  remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
                  rehypePlugins={[rehypeKatex]}
                  urlTransform={sandboxUrlTransform}
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
                    img({ src, alt }) {
                      // Model-curated chart: ![alt](sandbox:name.png) → served
                      // full-size inline from the thread's workspace. Opens
                      // the "inline" lightbox — navigable across every image
                      // in THIS message, in the order they appear — not the
                      // "tool" gallery: that gallery's `name` is a synthetic
                      // sequential name the backend assigns when
                      // auto-capturing tool output, decoupled from whatever
                      // real filename the model references here.
                      const raw = typeof src === "string" ? src.trim() : "";
                      const galleryIndex = inlineImageRefs.indexOf(raw);
                      return (
                        <MarkdownImage
                          src={typeof src === "string" ? src : undefined}
                          alt={alt}
                          threadId={threadId}
                          onOpen={() => openInlineLightbox(Math.max(galleryIndex, 0))}
                        />
                      );
                    },
                    a({ href, children }) {
                      const raw = typeof href === "string" ? href.trim() : "";
                      // Inline citation marker, rewritten by linkifyCitations
                      // below from a model's [n] into [n](citation:n) — only
                      // ever emitted for an index present in sourceByIndex, so
                      // this never needs its own "not found" UI. Degrading to
                      // plain children (not null) means even a hypothetical
                      // future bug here fails safe as literal-looking text
                      // rather than silently swallowing the marker.
                      if (raw.startsWith("citation:")) {
                        const source = sourceByIndex.get(Number(raw.slice(9)));
                        if (!source) return <>{children}</>;
                        return <CitationChip source={source} onOpen={onOpenSource} />;
                      }
                      // Model-referenced file: [label](sandbox:report.xlsx) →
                      // a card that opens the file in the side-panel artifact
                      // viewer (Claude-style) with a download fallback.
                      if (raw.startsWith("sandbox:") && threadId) {
                        const path = raw.replace(/^sandbox:/, "").replace(/^\.?\//, "");
                        const name = path.split("/").pop() || path;
                        const url = buildWorkspaceFileUrl(threadId, raw);
                        const { Icon, label, badgeClass } = officeFileBadge(name);
                        const ext = name.split(".").pop()?.toUpperCase() || "FILE";
                        return (
                          <span
                            role={onOpenArtifact ? "button" : undefined}
                            tabIndex={onOpenArtifact ? 0 : undefined}
                            onClick={() => onOpenArtifact?.(path, name)}
                            onKeyDown={(e) => {
                              if (onOpenArtifact && (e.key === "Enter" || e.key === " ")) {
                                e.preventDefault();
                                onOpenArtifact(path, name);
                              }
                            }}
                            className={`my-1 flex w-full max-w-md items-center gap-3 rounded-2xl border border-(--border) bg-(--card) p-3 shadow-xs hover:shadow-sm hover:bg-(--card-hover) hover:border-(--border-hover) transition-all duration-200 ${onOpenArtifact ? "cursor-pointer" : ""}`}
                          >
                            <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${badgeClass}`}>
                              <Icon className="h-5 w-5" />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-semibold text-foreground">
                                {name}
                              </span>
                              <span className="block text-xs text-(--muted)">
                                {label} · {ext}
                              </span>
                            </span>
                            <a
                              href={url}
                              download={name}
                              onClick={(e) => e.stopPropagation()}
                              className="btn-icon shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold hover:scale-[1.03] active:scale-[0.97] transition-all flex items-center gap-1.5"
                              style={{
                                background: "var(--accent)",
                                color: "var(--accent-foreground)",
                                minWidth: "unset",
                                minHeight: "unset",
                                textDecoration: "none",
                              }}
                            >
                              <Download className="h-3.5 w-3.5" />
                              Download
                            </a>
                          </span>
                        );
                      }
                      return (
                        <a href={raw} target="_blank" rel="noreferrer">
                          {children}
                        </a>
                      );
                    },
                  }}
                >
                  {linkifyCitations(preprocessMarkdown(safeContent), validCitationIndices)}
                </ReactMarkdown>
                {role === "assistant" && (
                  <SourcesStrip sources={sources} onOpenSource={onOpenSource} />
                )}
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
              <div className="flex w-full max-w-xl flex-wrap gap-2.5 pt-1">
                {documentAttachments.map((attachment) => (
                  <AttachmentDocumentCard key={attachment.id} attachment={attachment} />
                ))}
              </div>
            )}

            {/* Tool-generated charts — collapsed so exploratory re-runs don't
              flood the chat; the model surfaces the key ones inline above via
              sandbox: markdown refs. */}
            {toolImageAttachments.length > 0 && (
              <details className="group/plots w-full">
                <summary
                  className="inline-flex cursor-pointer select-none list-none items-center gap-2 rounded-xl px-3 py-1.5 text-xs font-medium text-(--badge-fg) transition-colors hover:bg-(--card-hover)"
                  style={{ background: "var(--badge-bg)" }}
                >
                  <WrenchIcon className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
                  {`${toolImageAttachments.length} chart${toolImageAttachments.length > 1 ? "s" : ""} generated`}
                  <ChevronRight className="h-3 w-3 shrink-0 transition-transform group-open/plots:rotate-90 text-(--muted)" />
                </summary>
                <div className="mt-2 flex flex-wrap gap-2">
                  {toolImageAttachments.map((attachment, idx) => (
                    <button
                      key={attachment.id}
                      type="button"
                      onClick={() => openLightbox("tool", idx)}
                      className="block cursor-pointer overflow-hidden rounded-xl border border-(--border) bg-(--card) shadow-sm transition-shadow hover:shadow-md"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={attachment.url}
                        alt={attachment.name}
                        className="h-28 w-auto max-w-[220px] object-contain"
                      />
                    </button>
                  ))}
                </div>
              </details>
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
