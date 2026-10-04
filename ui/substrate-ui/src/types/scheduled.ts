export interface ScheduledTaskRun {
  id: string;
  task_id: string;
  /** `waiting`: the run is parked until you answer or approve in its conversation. */
  status: "success" | "failed" | "silent" | "waiting";
  output_summary: string;
  executed_at: string;
  duration_ms: number;
  was_silent: boolean;
  error_message: string | null;
  tokens?: number;
  cost_usd?: number;
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
  /** Email the result of each run. */
  email_results?: boolean;
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
  email_results?: boolean;
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
