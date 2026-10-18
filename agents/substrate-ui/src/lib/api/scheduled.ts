import { requestJson, requestVoid } from "./_client";
import {
  ScheduledTask,
  ScheduledTaskRun,
  CreateScheduledTaskBody,
  UpdateScheduledTaskBody,
  ScheduledTaskParseResponse,
} from "@/types";

export const scheduledApi = {
  async getScheduledTasks(status?: string): Promise<ScheduledTask[]> {
    const path = status ? `/scheduled?status=${encodeURIComponent(status)}` : "/scheduled";
    return requestJson<ScheduledTask[]>(path);
  },

  async getScheduledTask(id: string): Promise<ScheduledTask> {
    return requestJson<ScheduledTask>(`/scheduled/${id}`);
  },

  async getScheduledTaskRuns(
    id: string,
    params?: { limit?: number; offset?: number; include_silent?: boolean },
  ): Promise<ScheduledTaskRun[]> {
    const query = new URLSearchParams();
    if (params?.limit !== undefined) query.set("limit", String(params.limit));
    if (params?.offset !== undefined) query.set("offset", String(params.offset));
    if (params?.include_silent !== undefined) query.set("include_silent", String(params.include_silent));
    const path = `/scheduled/${id}/runs?${query.toString()}`;
    return requestJson<ScheduledTaskRun[]>(path);
  },

  async createScheduledTask(body: CreateScheduledTaskBody): Promise<ScheduledTask> {
    return requestJson<ScheduledTask>("/scheduled", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async updateScheduledTask(id: string, body: UpdateScheduledTaskBody): Promise<ScheduledTask> {
    return requestJson<ScheduledTask>(`/scheduled/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  async deleteScheduledTask(id: string): Promise<void> {
    return requestVoid(`/scheduled/${id}`, {
      method: "DELETE",
    });
  },

  async runScheduledTaskNow(id: string): Promise<void> {
    return requestVoid(`/scheduled/${id}/run`, {
      method: "POST",
    });
  },

  async parseScheduledTaskText(text: string): Promise<ScheduledTaskParseResponse> {
    return requestJson<ScheduledTaskParseResponse>("/scheduled/parse", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
  },

  async addScheduledTaskFeedback(id: string, content: string): Promise<void> {
    return requestVoid(`/scheduled/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  },
};
export default scheduledApi;
