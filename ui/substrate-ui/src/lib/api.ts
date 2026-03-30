import { Thread, BackendMessage, Message, ToolCall, TaskList, TaskStatus, UploadedFile, TranscribeResult, RealtimeToken, TTSVoice } from "@/types";

// API service layer for backend communication
// Use ?? (not ||) so that NEXT_PUBLIC_API_URL="" (k8s build) yields ""
// enabling relative paths that the ingress routes to gateway-bff.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const api = {
  // Thread management
  async getThreads(): Promise<Thread[]> {
    const res = await fetch(`${API_BASE}/threads`);
    if (!res.ok) throw new Error("Failed to fetch threads");
    return res.json();
  },

  async createThread(name?: string): Promise<Thread> {
    const res = await fetch(`${API_BASE}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name || "New Chat" }),
    });
    if (!res.ok) throw new Error("Failed to create thread");
    return res.json();
  },

  async deleteThread(threadId: string): Promise<void> {
    const res = await fetch(`${API_BASE}/threads/${threadId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Failed to delete thread");
  },

  async updateThread(threadId: string, name: string): Promise<Thread> {
    const res = await fetch(`${API_BASE}/threads/${threadId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error("Failed to update thread");
    return res.json();
  },

  // Message management
  async getMessages(threadId: string): Promise<Message[]> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/messages`);
    if (!res.ok) throw new Error("Failed to fetch messages");
    const backendMessages: BackendMessage[] = await res.json();

    const messages: Message[] = [];

    for (const msg of backendMessages) {
      if (msg.type === "user_message") {
        messages.push({
          id: msg.id,
          role: "user",
          content: msg.input || "",
          timestamp: new Date(msg.created_at),
        });
      } else if (msg.type === "assistant_message") {
        // Parse tool calls from generation (includes _meta for MCP Apps)
        const toolCalls: ToolCall[] | undefined =
          msg.generation?.tool_calls?.map((tc) => ({
            id: tc.id,
            name: tc.name,
            arguments: tc.arguments,
            result: "Completed",
            _meta: tc._meta || undefined,
          }));

        const content = Array.isArray(msg.output)
          ? msg.output.join("")
          : msg.output || "";

        messages.push({
          id: msg.id,
          role: "assistant",
          content,
          timestamp: new Date(msg.created_at),
          toolCalls: toolCalls && toolCalls.length > 0 ? toolCalls : undefined,
        });
      } else if (msg.type === "tool_result") {
        // Only show tool results that DON'T have a companion MCP App
        // (MCP App tools show their UI inline on the assistant message)
        const hasApp = msg.metadata?.has_app === true;
        if (!hasApp) {
          messages.push({
            id: msg.id,
            role: "tool_result",
            content: (typeof msg.output === "string" ? msg.output : "") || "",
            timestamp: new Date(msg.created_at),
            metadata: {
              toolName: msg.name || "tool",
              isError: msg.is_error || false,
            },
          });
        }
      }
    }

    return messages;
  },

  // MCP App context update
  async updateMcpContext(
    threadId: string,
    toolName: string,
    context: unknown
  ): Promise<void> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/mcp-context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name: toolName, context }),
    });
    if (!res.ok) {
      console.error("Failed to update MCP context");
    }
  },

  // ---------------------------------------------------------------------------
  // Task Manager API
  // ---------------------------------------------------------------------------

  async getTaskList(conversationId: string): Promise<TaskList | null> {
    const res = await fetch(`${API_BASE}/tasks/${conversationId}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.task_list ?? null;
  },

  async updateTask(
    taskListId: string,
    taskId: string,
    update: { status?: TaskStatus; title?: string }
  ): Promise<void> {
    await fetch(`${API_BASE}/tasks/${taskListId}/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    });
  },

  async addTasks(taskListId: string, tasks: string[]): Promise<void> {
    await fetch(`${API_BASE}/tasks/${taskListId}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tasks }),
    });
  },

  async deleteTask(taskListId: string, taskId: string): Promise<void> {
    await fetch(`${API_BASE}/tasks/${taskListId}/${taskId}`, {
      method: "DELETE",
    });
  },

  // ---------------------------------------------------------------------------
  // File Management API
  // ---------------------------------------------------------------------------

  async uploadFile(threadId: string, file: File): Promise<UploadedFile> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/threads/${threadId}/files`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(`Failed to upload file: ${res.statusText}`);
    return res.json();
  },

  async listFiles(threadId: string): Promise<UploadedFile[]> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/files`);
    if (!res.ok) throw new Error("Failed to list files");
    return res.json();
  },

  async deleteFile(threadId: string, fileId: string): Promise<void> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/files/${fileId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Failed to delete file");
  },

  // ---------------------------------------------------------------------------
  // Audio / Voice API
  // ---------------------------------------------------------------------------

  /** Send recorded audio blob to Whisper for transcription. */
  async transcribeAudio(blob: Blob, mimeType: string): Promise<TranscribeResult> {
    const ext = mimeType.includes("ogg") ? "ogg"
      : mimeType.includes("mp4") ? "mp4"
      : mimeType.includes("wav") ? "wav"
      : "webm";
    const form = new FormData();
    form.append("file", blob, `recording.${ext}`);
    const sttModel = localStorage.getItem("stt_model")?.trim();
    if (sttModel) form.append("model", sttModel);
    const res = await fetch("/api/audio/transcribe", { method: "POST", body: form });
    if (!res.ok) throw new Error(`Transcription failed: ${res.statusText}`);
    return res.json();
  },

  /** Fetch TTS audio as a Blob, ready for playback. */
  async textToSpeech(
    text: string,
    voice: TTSVoice = "coral",
    model = "gpt-4o-mini-tts",
  ): Promise<Blob> {
    const res = await fetch("/api/audio/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, model }),
    });
    if (!res.ok) throw new Error(`TTS failed: ${res.statusText}`);
    return res.blob();
  },

  /** Get a short-lived ephemeral Realtime session token from the backend. */
  async getRealtimeToken(): Promise<RealtimeToken> {
    const res = await fetch("/api/audio/realtime-token");
    if (!res.ok) throw new Error(`Failed to get realtime token: ${res.statusText}`);
    return res.json();
  },

  // Chat streaming (handled separately in chat route)
};


