import { TaskList, TaskStatus } from "@/types";
import { API_BASE, requestVoid } from "./_client";

export const taskApi = {
  async getBoards(conversationId: string): Promise<TaskList[]> {
    const res = await fetch(`${API_BASE}/tasks/${conversationId}`);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.boards as TaskList[]) ?? [];
  },

  async updateTask(
    taskListId: string,
    taskId: string,
    update: { status?: TaskStatus; title?: string; note?: string }
  ): Promise<void> {
    await requestVoid(`/tasks/${taskListId}/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(update),
    });
  },

  async retryTask(taskListId: string, taskId: string): Promise<void> {
    await requestVoid(`/tasks/${taskListId}/${taskId}/retry`, {
      method: "POST",
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
