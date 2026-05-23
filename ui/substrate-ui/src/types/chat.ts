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
  role: "user" | "assistant" | "tool_approval" | "human_input" | "tool_result";
  content: string;
  reasoning?: string;
  timestamp: Date;
  attachments?: UploadedFile[];
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  metadata?: Record<string, unknown>;
  /** True for assistant bubbles that continue after a HITL step — suppresses duplicate avatar */
  isContinuation?: boolean;
};

export type Thread = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type BackendMessage = {
  id: string;
  type: "user_message" | "assistant_message" | "tool_result" | "tool_call" | string; // discriminated union with string fallback
  name?: string;
  input?: string;
  output?: string | string[];
  created_at: string;
  generation?: {
    finish_reason?: string;
    tool_calls?: Array<{
      id: string;
      name: string;
      arguments: string | Record<string, unknown>;
      _meta?: ToolCallMeta;
    }>;
  };
  metadata?: Record<string, unknown>;
  is_error?: boolean;
};

export type UploadedFile = {
  id: string;
  thread_id?: string;
  name: string;
  mime: string;
  size: number;
  url?: string;
};
