import { Thread, BackendMessage, Message, ToolCall } from "@/types";

// API service layer for backend communication
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

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

  // Chat streaming (handled separately in chat route)
};
