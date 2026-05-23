import {
  AdminStats,
  AdminStep,
  AdminThread,
  AdminUser,
  InstructionValidationResult,
} from "@/types";
import { requestJsonFromUrl, requestVoidFromUrl } from "./_client";

export const adminApi = {
  async checkCustomInstructions(
    instructions: string,
  ): Promise<InstructionValidationResult> {
    return requestJsonFromUrl<InstructionValidationResult>(
      "/api/settings/check-instructions",
      {
        method: "POST",
        body: JSON.stringify({ instructions }),
      },
    );
  },

  async getAdminThreads(): Promise<AdminThread[]> {
    return requestJsonFromUrl<AdminThread[]>("/api/admin/threads");
  },

  async getAdminUsers(): Promise<AdminUser[]> {
    return requestJsonFromUrl<AdminUser[]>("/api/admin/users");
  },

  async getAdminStats(): Promise<AdminStats> {
    return requestJsonFromUrl<AdminStats>("/api/admin/stats");
  },

  async getAdminThreadSteps(threadId: string): Promise<AdminStep[]> {
    return requestJsonFromUrl<AdminStep[]>(`/api/admin/threads/${threadId}/steps`);
  },

  async deleteAdminThread(threadId: string): Promise<void> {
    await requestVoidFromUrl(`/api/admin/threads/${threadId}`, {
      method: "DELETE",
    });
  },

  async disconnectGoogle(): Promise<void> {
    await requestVoidFromUrl("/api/auth/google/logout", {
      method: "POST",
      credentials: "include",
    });
  },

  async disconnectSpotify(): Promise<void> {
    await requestVoidFromUrl("/api/spotify/logout", {
      method: "POST",
      credentials: "include",
    });
  },

  async disconnectWorkspace(): Promise<void> {
    await requestVoidFromUrl("/api/workspace/token", { method: "DELETE" });
  },
};
