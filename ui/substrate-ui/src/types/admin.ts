/** Admin thread summary shown in the settings panel. */
export interface AdminThread {
  id: string;
  name: string;
  user_identifier: string | null;
  created_at: string | null;
  updated_at: string | null;
  deleted_at: string | null;
  step_count: number;
}

/** Admin user summary shown in the settings panel. */
export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  isAdmin: boolean;
  createdAt: string;
}

/** Aggregate admin stats surfaced in the settings panel. */
export interface AdminStats {
  total_threads: number;
  total_steps: number;
}

/** Individual persisted step for admin thread inspection. */
export interface AdminStep {
  id: string;
  type: string;
  name: string;
  input: string | null;
  output: string | null;
  is_error: boolean | null;
  created_at: string | null;
}

/** One user's workspace storage summary — admin storage tab. */
export interface AdminStorageUser {
  user_id: string;
  used_bytes: number;
  quota_bytes: number;
  session_count: number;
}

/** One session under a user's workspace (attaches to the code interpreter
 * when live — see agent-substrate's sandbox_runtime.py). */
export interface AdminStorageSession {
  session_id: string;
  size_bytes: number;
  file_count: number;
}
