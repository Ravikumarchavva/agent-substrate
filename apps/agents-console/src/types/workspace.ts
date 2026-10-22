export interface WorkspaceUsage {
  used_bytes: number;
  quota_bytes: number;
}

export type WorkspaceFileOwner = "user" | "agent";

export interface WorkspaceFile {
  path: string;
  name: string;
  size_bytes: number;
  modified_at: number;
  session_id: string | null;
  session_name: string | null;
  owner: WorkspaceFileOwner;
}
