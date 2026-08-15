import { Memory } from "@/types";
import { requestJson, requestVoid } from "./_client";

export const memoryApi = {
  async getMemories(): Promise<Memory[]> {
    return requestJson<Memory[]>("/me/memories");
  },

  async deleteMemory(id: string): Promise<void> {
    await requestVoid(`/me/memories/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
};
