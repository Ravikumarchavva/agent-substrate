import type { CitationSource } from "./citations";

export type ToolCallMeta = {
  ui?: {
    resourceUri: string;
    httpUrl: string;
  };
};

export type ToolCall = {
  id: string;
  name: string;
  arguments: string | Record<string, unknown>;
  result?: string;
  isError?: boolean;
  _meta?: ToolCallMeta;
  /** Risk tier — matches Python ToolRisk.value */
  risk?: "safe" | "sensitive" | "critical";
  /** Colour badge to show in the UI: green | yellow | red */
  color?: "green" | "yellow" | "red";
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "tool_approval" | "human_input" | "tool_result" | "max_iterations";
  content: string;
  reasoning?: string;
  timestamp: Date;
  attachments?: UploadedFile[];
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  metadata?: Record<string, unknown>;
  /** True for assistant bubbles that continue after a HITL step — suppresses duplicate avatar */
  isContinuation?: boolean;
  /** Grounded source references from knowledge_search tool.result events —
   * renders as inline [n] chips and a "Sources" strip. See src/lib/citations.ts. */
  sources?: CitationSource[];
};

export type Thread = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type UploadedFile = {
  id: string;
  thread_id?: string;
  name: string;
  mime: string;
  size: number;
  url?: string;
  /**
   * Path relative to the conversation's shared workspace (e.g.
   * "uploads/data.xlsx") — set by the backend for a non-extractable upload
   * (xlsx/docx/pptx/…) that actually lives in the workspace. Lets the UI
   * open it in the same read-only side-panel viewer an assistant-generated
   * file uses, via openArtifact. Undefined for types with no workspace
   * copy to view this way (a PDF, indexed into RAG instead).
   */
  session_path?: string;
  /**
   * Where the attachment came from. "tool" = auto-captured by a tool run
   * (e.g. code_interpreter plots) — rendered collapsed so exploratory
   * re-runs don't flood the chat; the model surfaces the ones worth showing
   * via `sandbox:` markdown refs instead. Undefined = a user upload or a
   * model-curated attachment, rendered normally.
   */
  origin?: "tool";
};
