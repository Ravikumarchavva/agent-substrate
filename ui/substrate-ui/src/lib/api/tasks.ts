import { TaskList, TaskStatus } from "@/types";
import { API_BASE, requestVoid } from "./_client";

export const taskApi = {
  async getTaskList(conversationId: string): Promise<TaskList | null> {
    const res = await fetch(`${API_BASE}/tasks/${conversationId}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.task_list ?? null;
  },

  async updateTask(
    taskListId: string,
    taskId: string,
    update: { status?: TaskStatus; title?: string }
  ): Promise<void> {
    await requestVoid(`/tasks/${taskListId}/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(update),
    });
  },

  async addTasks(taskListId: string, tasks: string[]): Promise<void> {
    await requestVoid(`/tasks/${taskListId}/tasks`, {
      method: "POST",
      body: JSON.stringify({ tasks }),
    });
  },

  async deleteTask(taskListId: string, taskId: string): Promise<void> {
    await requestVoid(`/tasks/${taskListId}/${taskId}`, {
      method: "DELETE",
    });
  },
};
