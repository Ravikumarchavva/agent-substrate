import { API_BASE, getErrorMessage, requestJson } from "./_client";
import type { Branch, HistoryCheckpoint } from "@/types";

export interface BranchForkPayload {
  source_branch_id: string;
  new_branch_id: string;
  fork_from_message_id?: string;
}

export interface CheckpointCreatePayload {
  anchor_message_id: string;
  summary: string;
  state?: Record<string, unknown>;
}

export interface ResolvedBranchMessage {
  id: string;
  parent_id: string | null;
  role: string;
  content: Array<Record<string, unknown>>;
  text: string;
  created_at: string;
}

export const branchApi = {
  async getBranches(threadId: string): Promise<Branch[]> {
    return requestJson<Branch[]>(`/threads/${threadId}/branches`);
  },

  async forkBranch(threadId: string, payload: BranchForkPayload): Promise<Branch> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/branches/fork`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      throw new Error(await getErrorMessage(res, "Failed to fork branch"));
    }
    return res.json();
  },

  async getBranch(threadId: string, branchId: string): Promise<Branch> {
    return requestJson<Branch>(`/threads/${threadId}/branches/${branchId}`);
  },

  async getBranchMessages(threadId: string, branchId: string): Promise<ResolvedBranchMessage[]> {
    return requestJson<ResolvedBranchMessage[]>(`/threads/${threadId}/branches/${branchId}/messages`);
  },

  async getCheckpoints(threadId: string, branchId: string): Promise<HistoryCheckpoint[]> {
    return requestJson<HistoryCheckpoint[]>(`/threads/${threadId}/branches/${branchId}/checkpoints`);
  },

  async createCheckpoint(
    threadId: string,
    branchId: string,
    payload: CheckpointCreatePayload
  ): Promise<HistoryCheckpoint> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/branches/${branchId}/checkpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      throw new Error(await getErrorMessage(res, "Failed to create checkpoint"));
    }
    return res.json();
  },
};

