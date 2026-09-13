import { getPreferredChatModel } from "@/lib/model-preferences";
import { API_BASE, getErrorMessage, requestJson } from "./_client";
import type { ChatStreamRequest } from "./_client";

export interface PendingHitlRequest {
  request_id: string;
  run_id?: string;
  question?: string;
  context?: string;
  options?: Array<{ key: string; label: string; description?: string }>;
  allow_freeform?: boolean;
}

export class ChatConflictError extends Error {
  constructor() {
    super("A response is already being generated for this thread.");
    this.name = "ChatConflictError";
  }
}

// 423: the thread was locked read-only after a file was deleted from its
// storage (routes/workspace.py::delete_file) — the agent can't reliably
// reply as if a now-missing file still existed. `message` is the backend's
// real reason (routes/workspace.py sets it per-deletion), not a generic
// fallback.
export class ChatLockedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChatLockedError";
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
    const res = await fetch(`/chat/api/chat/respond/${requestId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      throw new Error(await getErrorMessage(res, "Failed to respond to request"));
    }
  },

  async getHitlStatus(threadId: string): Promise<{ pending: PendingHitlRequest[] }> {
    // GET /hitl/status/{thread_id} — used on thread load/reconnect to
    // restore a still-pending ask_human card. The run itself is durably
    // suspended (Postgres) even across a backend restart; without this
    // call the frontend has no way to know a card is still waiting, since
    // input.requested only ever otherwise arrives as a live SSE event.
    return requestJson<{ pending: PendingHitlRequest[] }>(
      `/hitl/status/${threadId}`
    );
  },

  async cancelChat(threadId: string): Promise<void> {
    const res = await fetch("/chat/api/chat/cancel", {
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
    const res = await fetch("/chat/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...payload, model: payload.model ?? getPreferredChatModel() }),
      signal,
    });
    if (res.status === 409) {
      throw new ChatConflictError();
    }
    if (res.status === 423) {
      throw new ChatLockedError(
        await getErrorMessage(res, "This conversation is locked."),
      );
    }
    if (!res.ok || !res.body) {
      throw new Error(await getErrorMessage(res, `HTTP ${res.status}`));
    }
    return res;
  },

  async streamThread(threadId: string, signal: AbortSignal): Promise<Response> {
    // GET /stream/{thread_id} — reconnects to a thread's ALREADY-RUNNING run
    // and relays its remaining wire events. Only meaningful when there's no
    // live streamChat() connection already open for this thread (e.g. after
    // a page refresh, answering a HITL card that's suspended the run) — the
    // run itself is durable and keeps executing server-side regardless of
    // whether anything is connected, so this exists purely to let a
    // reconnecting browser see what happens next.
    const res = await fetch(`${API_BASE}/stream/${threadId}`, {
      method: "GET",
      headers: { Accept: "text/event-stream" },
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(await getErrorMessage(res, `HTTP ${res.status}`));
    }
    return res;
  },
};
