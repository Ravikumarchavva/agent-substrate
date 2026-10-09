import type { GroupEntry } from "./groups";
import { requestJson } from "./_client";

/** One row of an agent's own chat list. `key` is how to open it: `you`, `pair-<id>` (another agent) or `group-<id>`. */
export interface ViewChat {
  key: string;
  kind: "you" | "agent" | "group";
  id: string;
  name: string;
  avatar: string | null;
  preview: string;
  last_sender: string | null;
  at: string | null;
  /** Times the two agents have asked each other, or members of the group. */
  count: number;
}

export type ViewFilter = "all" | "agents" | "groups";

export interface ViewChats {
  items: ViewChat[];
  /** Pass as `before` for the next page; null on the last. */
  next: string | null;
}

export interface ViewMessages {
  /** The conversation itself (its name and picture), so a link to it opens without the list. */
  chat: ViewChat;
  entries: GroupEntry[];
  /** There is more before the first of these. */
  has_more: boolean;
}

/** An agent's account, read only: what you would see if you were it. Nothing here writes. */
export const observeApi = {
  async getViewChats(agentId: string, query: { q?: string; kind?: ViewFilter; before?: string | null; limit?: number } = {}): Promise<ViewChats> {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.kind && query.kind !== "all") params.set("kind", query.kind);
    if (query.before) params.set("before", query.before);
    if (query.limit) params.set("limit", String(query.limit));
    const qs = params.toString();
    return requestJson<ViewChats>(`/agents/${agentId}/view/chats${qs ? `?${qs}` : ""}`);
  },

  /** The latest page of one conversation, or the page before the entry numbered `before`. */
  async getViewMessages(agentId: string, key: string, before?: number): Promise<ViewMessages> {
    return requestJson<ViewMessages>(`/agents/${agentId}/view/chats/${key}/messages${before === undefined ? "" : `?before=${before}`}`);
  },
};
