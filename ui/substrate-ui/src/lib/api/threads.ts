import { Thread, ThreadSearchHit } from "@/types";
import { API_BASE, ApiError, getErrorMessage, requestJson, requestVoid } from "./_client";

export const THREAD_PAGE_SIZE = 30;

export type ThreadPatch = { name?: string; pinned?: boolean; archived?: boolean };

export const threadApi = {
  /** One page of the caller's threads: pinned first, then newest. `archived` lists the archive instead. */
  async getThreads(opts: { offset?: number; archived?: boolean } = {}): Promise<Thread[]> {
    const params = new URLSearchParams({ limit: String(THREAD_PAGE_SIZE), offset: String(opts.offset ?? 0) });
    if (opts.archived) params.set("archived", "true");
    return requestJson<Thread[]>(`/threads?${params}`);
  },

  async createThread(name?: string): Promise<Thread> {
    return requestJson<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ name: name || "New Chat" }),
    });
  },

  async deleteThread(threadId: string): Promise<void> {
    await requestVoid(`/threads/${threadId}`, {
      method: "DELETE",
    });
  },

  async updateThread(threadId: string, patch: string | ThreadPatch): Promise<Thread> {
    return requestJson<Thread>(`/threads/${threadId}`, {
      method: "PATCH",
      body: JSON.stringify(typeof patch === "string" ? { name: patch } : patch),
    });
  },

  /** Messages containing `q` across the caller's conversations (the sidebar matches titles itself). */
  async searchThreads(q: string): Promise<ThreadSearchHit[]> {
    return requestJson<ThreadSearchHit[]>(`/threads/search?q=${encodeURIComponent(q)}`);
  },

  /** Download a conversation as Markdown or JSON. */
  async exportThread(threadId: string, format: "md" | "json"): Promise<void> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/export?format=${format}`, { credentials: "include" });
    if (!res.ok) throw new ApiError(await getErrorMessage(res, "Couldn't export the conversation"), res.status);
    const disposition = res.headers.get("content-disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `conversation.${format}`;
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  },

  /** The conversation's public link token, or null when it is not shared. */
  async getShare(threadId: string): Promise<string | null> {
    const share = await requestJson<{ token: string } | null>(`/threads/${threadId}/share`);
    return share?.token ?? null;
  },

  async createShare(threadId: string): Promise<string> {
    return (await requestJson<{ token: string }>(`/threads/${threadId}/share`, { method: "POST" })).token;
  },

  async deleteShare(threadId: string): Promise<void> {
    await requestVoid(`/threads/${threadId}/share`, { method: "DELETE" });
  },
};
