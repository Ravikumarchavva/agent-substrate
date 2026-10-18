import { API_BASE } from "./_client";

// Mirrors rate_limit.ts's shape/pattern — see agent-substrate
// routes/files.py::get_doc_quota_status. Daily commit quota (documents
// actually sent in a chat message, not merely uploaded — see
// useFileAttachments.ts's staging/promote flow).
export interface DocQuotaStatus {
  enabled: boolean;
  used: number;
  limit: number;
  reset_in: number;
}

export async function fetchDocQuotaStatus(): Promise<DocQuotaStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/files/quota/status`, { credentials: "include" });
    if (!res.ok) return null;
    return (await res.json()) as DocQuotaStatus;
  } catch {
    return null;
  }
}
