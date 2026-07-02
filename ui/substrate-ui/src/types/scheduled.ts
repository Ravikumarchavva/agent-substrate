export interface ScheduledTaskRun {
  id: string;
  task_id: string;
  status: "success" | "failed" | "silent";
  output_summary: string;
  executed_at: string;
  duration_ms: number;
  was_silent: boolean;
  error_message: string | null;
}

export interface ScheduledTask {
  id: string;
  user_id: string | null;
  name: string;
  prompt: string;
  cron_expression: string;
  kind: "cron" | "interval";
  thread_id: string;
  status: "active" | "paused" | "completed" | "error";
  lookback_runs: number;
  task_type: "report" | "monitor" | "reminder" | "learning";
  auto_disable: boolean;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
  recent_runs: ScheduledTaskRun[];
}

export interface CreateScheduledTaskBody {
  name: string;
  prompt: string;
  cron_expression: string;
  kind?: "cron" | "interval";
  task_type?: "report" | "monitor" | "reminder" | "learning";
  lookback_runs?: number;
  auto_disable?: boolean;
}

export interface UpdateScheduledTaskBody {
  name?: string;
  prompt?: string;
  cron_expression?: string;
  kind?: "cron" | "interval";
  status?: "active" | "paused" | "completed" | "error";
  lookback_runs?: number;
  auto_disable?: boolean;
}

export interface ScheduledTaskParseResponse {
  name: string;
  prompt: string;
  cron_expression: string;
  kind: "cron" | "interval";
  task_type: "report" | "monitor" | "reminder" | "learning";
}
