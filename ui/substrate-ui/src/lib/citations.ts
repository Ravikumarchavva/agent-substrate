import type { CitationSource } from "@/types";

/**
 * Parse the `structured_content.citations` payload a `tool.result` SSE event
 * carries (see agent-substrate's capabilities/knowledge/citations.py::
 * Citation.to_wire) into `CitationSource[]`.
 *
 * Grounding lives entirely on the backend — this only reshapes snake_case
 * wire fields into the frontend's camelCase type. Entries missing `index`
 * or `file_name` are dropped rather than defaulted, since a citation with
 * no real index can never be linked back to by an inline `[n]` marker.
 */
export function parseCitations(structuredContent: unknown): CitationSource[] {
  if (!structuredContent || typeof structuredContent !== "object") return [];
  const raw = (structuredContent as Record<string, unknown>).citations;
  if (!Array.isArray(raw)) return [];

  const sources: CitationSource[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const index = typeof e.index === "number" ? e.index : NaN;
    const fileName = typeof e.file_name === "string" ? e.file_name : "";
    if (!Number.isFinite(index) || !fileName) continue;

    sources.push({
      index,
      fileName,
      fileId: typeof e.file_id === "string" && e.file_id ? e.file_id : undefined,
      sessionPath:
        typeof e.session_path === "string" && e.session_path
          ? e.session_path
          : undefined,
      threadId:
        typeof e.thread_id === "string" && e.thread_id ? e.thread_id : undefined,
      page: typeof e.page === "number" ? e.page : null,
      pages: Array.isArray(e.pages)
        ? e.pages.filter((p): p is number => typeof p === "number")
        : undefined,
      score: typeof e.score === "number" ? e.score : undefined,
      snippet: typeof e.snippet === "string" ? e.snippet : undefined,
      backend: typeof e.backend === "string" ? e.backend : undefined,
    });
  }
  return sources;
}

/**
 * Merge a new citation batch into an accumulated set, keyed by `index`.
 * Last write wins for a repeated index (the backend's ledger only ever
 * grows/relabels-consistently within one collection, so a later event's
 * copy of an index is never worse than an earlier one) and the result is
 * sorted for stable, predictable rendering order in the Sources strip.
 *
 * Both the live SSE reducer (src/app/page.tsx) and the replay reducer
 * (src/lib/api/history-fold.ts) call this — using one shared merge means
 * a citation set built live and one rebuilt from history can't drift.
 */
export function mergeSources(
  prev: CitationSource[],
  next: CitationSource[]
): CitationSource[] {
  if (next.length === 0) return prev;
  const byIndex = new Map(prev.map((s) => [s.index, s]));
  for (const source of next) byIndex.set(source.index, source);
  return [...byIndex.values()].sort((a, b) => a.index - b.index);
}
