import {
  AdminStats,
  AdminStep,
  AdminStorageSession,
  AdminStorageUser,
  AdminThread,
  AdminUser,
  InstructionValidationResult,
} from "@/types";
import { API_BASE, requestJsonFromUrl, requestVoidFromUrl } from "./_client";

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

  // Routed through /api/backend/* (the verified-session proxy) rather than
  // a bespoke /api/admin/* route handler — agent-substrate's
  // /admin/storage* routes require a real Bearer JWT with role=
  // platform_admin (require_admin), and /api/backend/[...path]/route.ts is
  // the proxy that mints one from the caller's DB-backed session.
  async getAdminStorageUsers(): Promise<AdminStorageUser[]> {
    return requestJsonFromUrl<AdminStorageUser[]>(`${API_BASE}/admin/storage`);
  },

  async getAdminStorageSessions(userId: string): Promise<AdminStorageSession[]> {
    return requestJsonFromUrl<AdminStorageSession[]>(
      `${API_BASE}/admin/storage/${encodeURIComponent(userId)}/sessions`,
    );
  },

  async setAdminStorageQuota(
    userId: string,
    quotaBytes: number | null,
  ): Promise<Pick<AdminStorageUser, "user_id" | "quota_bytes">> {
    return requestJsonFromUrl<Pick<AdminStorageUser, "user_id" | "quota_bytes">>(
      `${API_BASE}/admin/storage/${encodeURIComponent(userId)}/quota`,
      { method: "PUT", body: JSON.stringify({ quota_bytes: quotaBytes }) },
    );
  },

  async deleteAdminThread(threadId: string): Promise<void> {
    await requestVoidFromUrl(`/api/admin/threads/${threadId}`, {
      method: "DELETE",
    });
  },

  async disconnectGoogle(): Promise<void> {
    await requestVoidFromUrl("/chat/api/auth/google/logout", {
      method: "POST",
      credentials: "include",
    });
  },

  async disconnectSpotify(): Promise<void> {
    await requestVoidFromUrl("/chat/api/spotify/logout", {
      method: "POST",
      credentials: "include",
    });
  },

  async disconnectWorkspace(): Promise<void> {
    await requestVoidFromUrl("/chat/api/workspace/token", { method: "DELETE" });
  },
};
