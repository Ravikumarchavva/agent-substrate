"use client";

import type { CitationSource } from "@/types";
import { SourceChip } from "@/components/CitationChip";

/**
 * The clickable "Sources" row under an assistant message — built entirely
 * from `Message.sources`, which itself is built entirely from real
 * retrieval metadata (see agent-substrate's capabilities/knowledge/
 * citations.py). Renders nothing when there are no sources, so it's always
 * safe to mount unconditionally.
 */
export function SourcesStrip({
  sources,
  onOpenSource,
}: {
  sources?: CitationSource[];
  onOpenSource?: (source: CitationSource) => void;
}) {
  if (!sources || sources.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-(--border) pt-2">
      <span className="text-xs font-medium text-(--muted)">Sources</span>
      {sources.map((source) => (
        <SourceChip key={source.index} source={source} onOpen={onOpenSource} />
      ))}
    </div>
  );
}
