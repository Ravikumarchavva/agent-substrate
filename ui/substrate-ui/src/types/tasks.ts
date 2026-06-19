export type TaskStatus =
  | "planned"
  | "in_progress"
  | "blocked"
  | "succeeded"
  | "failed"
  | "abandoned";

export type Task = {
  id: string;
  title: string;
  status: TaskStatus;
  order: number;
  retry_count: number;
  note?: string;
};

export type TaskList = {
  id: string;
  conversation_id: string;
  tasks: Task[];
  max_retries: number;
  agent_id: string;
  agent_label: string;
  parent_agent_id: string | null;
};
