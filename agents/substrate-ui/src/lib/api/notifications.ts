import { requestJson, requestVoid } from "./_client";

/** Something that happened while the user was away: a scheduled run finished or failed, or the assistant is waiting on them. */
export interface AppNotification {
  id: string;
  kind: "task_run" | "task_failed" | "approval";
  title: string;
  body: string;
  thread_id: string | null;
  created_at: string;
  read_at: string | null;
}

export const notificationsApi = {
  /** Never throws: the bell is a convenience and must not break the page. */
  async getNotifications(): Promise<{ unread: number; items: AppNotification[] }> {
    try {
      return await requestJson<{ unread: number; items: AppNotification[] }>("/notifications");
    } catch {
      return { unread: 0, items: [] };
    }
  },

  async markNotificationRead(id: string): Promise<void> {
    await requestVoid(`/notifications/${id}/read`, { method: "POST" });
  },

  async markAllNotificationsRead(): Promise<void> {
    await requestVoid("/notifications/read-all", { method: "POST" });
  },
};
