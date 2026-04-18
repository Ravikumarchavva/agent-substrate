export type TaskStatus = "todo" | "in_progress" | "done" | "failed";

export type Task = {
  id: string;
  title: string;
  status: TaskStatus;
  order: number;
};

export type TaskList = {
  id: string;
  conversation_id: string;
  tasks: Task[];
};
