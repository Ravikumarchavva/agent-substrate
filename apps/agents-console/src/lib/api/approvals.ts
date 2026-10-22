import { requestJson } from "./_client";

/** Something the assistant is waiting on the user for, in one of their conversations. */
export interface PendingApproval {
  thread_id: string;
  thread_name: string | null;
  /** `approval`: it wants to use a tool. `input`: it is asking a question. */
  kind: "approval" | "input";
  summary: string;
  requested_at: string | null;
}

export const approvalsApi = {
  /** Empty on any failure: the badge is a convenience and must never break the page. */
  async getApprovals(): Promise<PendingApproval[]> {
    try {
      return await requestJson<PendingApproval[]>("/approvals");
    } catch {
      return [];
    }
  },
};
