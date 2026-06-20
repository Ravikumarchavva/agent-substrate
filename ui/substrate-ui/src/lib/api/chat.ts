import { getPreferredChatModel } from "@/lib/model-preferences";
import { API_BASE, getErrorMessage } from "./_client";
import type { ChatStreamRequest } from "./_client";

export class ChatConflictError extends Error {
  constructor() {
    super("A response is already being generated for this thread.");
    this.name = "ChatConflictError";
  }
}

export const chatApi = {
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

  async respondToHitl(requestId: string, data: Record<string, unknown>): Promise<void> {
    const res = await fetch(`/api/chat/respond/${requestId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      throw new Error(await getErrorMessage(res, "Failed to respond to request"));
    }
  },

  async cancelChat(threadId: string): Promise<void> {
    const res = await fetch("/api/chat/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId }),
    });
    if (!res.ok) {
      throw new Error(await getErrorMessage(res, "Failed to cancel chat"));
    }
  },

  async streamChat(
    payload: Omit<ChatStreamRequest, "model"> & { model?: string },
    signal: AbortSignal,
  ): Promise<Response> {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...payload, model: payload.model ?? getPreferredChatModel() }),
      signal,
    });
    if (res.status === 409) {
      throw new ChatConflictError();
    }
    if (!res.ok || !res.body) {
      throw new Error(await getErrorMessage(res, `HTTP ${res.status}`));
    }
    return res;
  },
};
