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
 * Inline citation chip — a small circle with a document icon. On hover it
 * reveals filename + page as an absolutely-positioned overlay pinned to the
 * icon's left edge, painted on top of surrounding content rather than by
 * resizing the chip's own in-flow box.
 *
 * That distinction matters specifically inside a Markdown table (the
 * "Source" column format the system prompt now defaults structured answers
 * to): a `<table>` under the default `table-layout: auto` sizes every
 * column from the widest content seen in ANY row of that column, live. A
 * chip that grows its *real* box on hover — the previous implementation,
 * via padding + max-width transitions — reflowed the WHOLE table's column
 * widths on every hover, in every row, which read as a layout glitch
 * (hover one row's source icon, every row visibly shifts). An
 * `absolute`-positioned overlay is removed from flow entirely, so hovering
 * it can never change what the table measures. In prose this looks
 * identical to before — a circle icon that grows into a pill.
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
      className="group/cite relative mx-0.5 inline-flex cursor-pointer select-none align-middle"
    >
      {/* The only element that participates in layout — its size never
          changes, so hovering it can never trigger a reflow. */}
      <span
        className="btn-icon flex h-[1.5em] w-[1.5em] items-center justify-center rounded-full leading-none transition-colors duration-150 group-hover/cite:bg-(--card-hover)"
        style={{ background: "var(--badge-bg)" }}
      >
        <FileText className="h-[0.85em] w-[0.85em] text-(--muted)" />
      </span>
      {/* Hover reveal. `pointer-events-none` while collapsed so it can't
          create a dead click zone over a neighbouring chip or cell content;
          re-enabled only once actually visible. */}
      <span
        className="pointer-events-none absolute left-0 top-1/2 z-10 flex -translate-y-1/2 items-center gap-1 whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium opacity-0 shadow-md transition-opacity duration-150 group-hover/cite:pointer-events-auto group-hover/cite:opacity-100"
        style={{ background: "var(--badge-bg)", color: "var(--badge-fg)" }}
      >
        <FileText className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
        <span className="max-w-[160px] truncate">{source.fileName}</span>
        {source.page && (
          <span className="shrink-0 font-normal text-(--muted)">p.{source.page}</span>
        )}
      </span>
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
