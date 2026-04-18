import { Thread } from "@/types";
import { requestJson, requestVoid } from "./_client";

export const threadApi = {
  async getThreads(): Promise<Thread[]> {
    return requestJson<Thread[]>("/threads");
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

  async updateThread(threadId: string, name: string): Promise<Thread> {
    return requestJson<Thread>(`/threads/${threadId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  },
};
