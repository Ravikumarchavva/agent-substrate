export type ToolCallMeta = {
  ui?: {
    resourceUri: string;
    httpUrl: string;
  };
};

export type ToolCall = {
  id: string;
  name: string;
  arguments: string | Record<string, any>;
  result?: string;
  isError?: boolean;
  _meta?: ToolCallMeta;
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
  type: string; // "user_message" | "assistant_message" | "tool_result"
  name?: string;
  input?: string;
  output?: string | string[];
  created_at: string;
  generation?: {
    finish_reason?: string;
    tool_calls?: Array<{
      id: string;
      name: string;
      arguments: string | Record<string, any>;
      _meta?: ToolCallMeta;
    }>;
  };
  metadata?: Record<string, any>;
  is_error?: boolean;
};

export type User = {
  id: string;
  name: string;
  email: string;
  avatar?: string;
};

export type Element = {
  id: string;
  thread_id?: string;
  type?: string;
  name: string;
  mime?: string;
  size?: string;
  display?: string;
  url?: string;
  for_id?: string;
  props?: Record<string, unknown>;
};

export type UploadedFile = {
  id: string;
  thread_id?: string;
  name: string;
  mime?: string;
  size?: number;
};

// ---------------------------------------------------------------------------
// Audio / Voice Types
// ---------------------------------------------------------------------------

/** Available TTS voices from the OpenAI TTS API. */
export type TTSVoice =
  | "alloy" | "ash" | "ballad" | "coral" | "echo"
  | "fable" | "nova" | "onyx" | "sage" | "shimmer"
  | "verse" | "marin" | "cedar";

/** Response from POST /api/audio/transcribe */
export type TranscribeResult = {
  text: string;
};

/** Response from GET /api/audio/realtime-token */
export type RealtimeToken = {
  client_secret: string;
  expires_at: number;
  session_id: string;
};

// ---------------------------------------------------------------------------
// Task Manager Types
// ---------------------------------------------------------------------------

export type TaskStatus = "todo" | "in_progress" | "done" | "failed";

export type Task = {
  id: string;
  title: string;
  status: TaskStatus;
  order: number;
};

export type TaskList = {
  id: string;
  conversation_id: string;
  tasks: Task[];
};

