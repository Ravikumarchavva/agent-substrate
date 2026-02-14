export type ToolCall = {
  id: string;
  name: string;
  arguments: string;
  result?: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "tool_approval" | "human_input" | "tool_result";
  content: string;
  reasoning?: string;
  timestamp: Date;
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  metadata?: Record<string, unknown>;
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
  type: string; // "user_message" | "assistant_message"
  name?: string;
  input?: string;
  output?: string | string[];
  created_at: string;
};

export type User = {
  id: string;
  name: string;
  email: string;
  avatar?: string;
};
