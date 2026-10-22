// A grounded source reference behind a retrieved passage — built entirely
// from real retrieval metadata on the backend (integrations/knowledge/
// citations.py), never from the model's own text. `index` maps to the `[n]`
// markers a message's prose cites; the backend guarantees indices are
// stable for the life of a chat thread, so `[2]` always means the same
// source across every knowledge_search call in a conversation.
export type CitationSource = {
  index: number;
  fileName: string;
  fileId?: string;
  /** Thread-relative path — what openArtifact needs to open this file in
   * the side panel (falls back to fileName on the backend when unknown). */
  sessionPath?: string;
  threadId?: string;
  /** Jump target page (1-indexed). Null for file types with no page
   * concept (DOCX/PPTX) or a backend that didn't report one. */
  page?: number | null;
  /** Every page the retrieved chunk actually spanned — wider than `page`
   * for coarsely-chunked sources, shown so imprecision stays visible. */
  pages?: number[];
  score?: number;
  /** Short preview of the retrieved passage, for a hover title. */
  snippet?: string;
  backend?: string;
};
