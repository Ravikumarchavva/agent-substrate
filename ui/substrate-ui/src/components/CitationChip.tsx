"use client";

import { FileText } from "lucide-react";
import type { CitationSource } from "@/types";

function pageLabel(source: CitationSource): string {
  const pages = source.pages;
  if (pages && pages.length > 1) {
    const sorted = [...pages].sort((a, b) => a - b);
    const contiguous = sorted[sorted.length - 1] - sorted[0] === sorted.length - 1;
    return contiguous ? ` · pp.${sorted[0]}-${sorted[sorted.length - 1]}` : ` · pp.${sorted.join(",")}`;
  }
  if (source.page) return ` · p.${source.page}`;
  return "";
}

function titleFor(source: CitationSource): string {
  const parts = [`${source.fileName}${pageLabel(source)}`];
  if (source.snippet) parts.push(source.snippet);
  return parts.join(" — ");
}

/**
 * Inline citation chip — renders as a small circle with a document icon.
 * On hover it smoothly expands into a pill showing filename + page.
 * Uses CSS max-width + opacity transitions on the text spans so the
 * expand/collapse is purely declarative (no JS state).
 */
export function CitationChip({
  source,
  onOpen,
}: {
  source: CitationSource;
  onOpen?: (source: CitationSource) => void;
}) {
  return (
    <span
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      title={titleFor(source)}
      onClick={onOpen ? () => onOpen(source) : undefined}
      onKeyDown={
        onOpen
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen(source);
              }
            }
          : undefined
      }
      className="group/cite btn-icon mx-0.5 inline-flex items-center rounded-full px-1 py-0.5 text-xs font-medium leading-none cursor-pointer select-none align-middle transition-all duration-200 ease-out hover:gap-1 hover:px-2 hover:bg-(--card-hover)"
      style={{
        background: "var(--badge-bg)",
        color: "var(--badge-fg)",
        textDecoration: "none",
        minHeight: "unset",
        minWidth: "unset",
      }}
    >
      <FileText className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
      <span className="truncate max-w-0 opacity-0 group-hover/cite:max-w-[160px] group-hover/cite:opacity-100 transition-all duration-200 ease-out overflow-hidden whitespace-nowrap">{source.fileName}</span>
      {source.page && <span className="shrink-0 text-(--muted) font-normal max-w-0 opacity-0 group-hover/cite:max-w-[60px] group-hover/cite:opacity-100 transition-all duration-200 ease-out overflow-hidden whitespace-nowrap">p.{source.page}</span>}
    </span>
  );
}

/**
 * The "Sources" strip variant — one row per citation, shown under an
 * assistant message. See SourcesStrip.tsx.
 */
export function SourceChip({
  source,
  onOpen,
}: {
  source: CitationSource;
  onOpen?: (source: CitationSource) => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen ? () => onOpen(source) : undefined}
      title={source.snippet}
      className="btn-icon inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1 text-xs font-medium transition-colors hover:bg-(--card-hover) cursor-pointer"
      style={{
        background: "var(--badge-bg)",
        color: "var(--badge-fg)",
        minHeight: "unset",
        minWidth: "unset",
      }}
    >
      <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-(--accent) text-[9px] font-semibold text-(--accent-foreground)">
        {source.index}
      </span>
      <FileText className="h-3 w-3 shrink-0 text-(--muted)" />
      <span className="truncate max-w-40 font-medium">{source.fileName}</span>
      {pageLabel(source) && <span className="shrink-0 text-(--muted)">{pageLabel(source)}</span>}
    </button>
  );
}
