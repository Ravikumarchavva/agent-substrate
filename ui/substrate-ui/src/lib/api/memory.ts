import { Memory } from "@/types";
import { requestJson, requestVoid } from "./_client";

export const memoryApi = {
  async getMemories(): Promise<Memory[]> {
    return requestJson<Memory[]>("/me/memories");
  },

  async addMemory(content: string): Promise<Memory> {
    return requestJson<Memory>("/me/memories", { method: "POST", body: JSON.stringify({ content }) });
  },

  async updateMemory(id: string, content: string): Promise<Memory> {
    return requestJson<Memory>(`/me/memories/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ content }) });
  },

  async deleteMemory(id: string): Promise<void> {
    await requestVoid(`/me/memories/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
};
