import { requestJson, requestVoid } from "./_client";

export interface ToolStep {
  call_id: string;
  name: string;
  args: string;
  /** `null`: no result came back (the run stopped first). */
  ok: boolean | null;
  output: string;
  risk: string | null;
}

export interface RunDetail {
  run_id: string;
  status: string;
  started_at: string | null;
  duration_ms: number | null;
  model: string | null;
  llm_calls: number;
  tokens: number;
  cost_usd: number;
  tools: ToolStep[];
  error: string | null;
  user_message: string;
}

export interface Rating {
  for_id: string;
  value: -1 | 1;
  comment: string | null;
}

export const runsApi = {
  /** What each run of the conversation did, oldest first. */
  async getRuns(threadId: string): Promise<RunDetail[]> {
    return requestJson<RunDetail[]>(`/threads/${threadId}/runs`);
  },

  async getRatings(threadId: string): Promise<Rating[]> {
    return requestJson<Rating[]>(`/threads/${threadId}/feedback`);
  },

  /** Rate an answer (a run) up or down; `0` takes the rating back. */
  async rateRun(threadId: string, runId: string, value: -1 | 0 | 1, comment?: string): Promise<void> {
    await requestVoid(`/feedbacks`, { method: "POST", body: JSON.stringify({ thread_id: threadId, for_id: runId, value, comment }) });
  },
};
