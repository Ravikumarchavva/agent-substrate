import { requestJson, requestVoid } from "./_client";

/** How closely a member follows the group: every message, only what is addressed to it, or only what names it. */
export type MemberMode = "all" | "mentions" | "muted";

export interface GroupMember {
  agent_id: string;
  name: string;
  role: string;
  mode: MemberMode;
  avatar: string | null;
  /** What this agent has used in the group. */
  tokens_used: number;
  cost_usd: number;
}

export interface Group {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  members: GroupMember[];
  last_message: string | null;
  last_sender: string | null;
  /** Messages from agents the user has not seen. */
  unread: number;
  /** Agents spoke a long time without a person; a message from the user resumes it. */
  paused: boolean;
  /** Members thinking about it right now. */
  working: string[];
  /** What the agents have used in this group, and the most they may. */
  tokens_used: number;
  token_cap: number;
  /** The most the agents may spend here in dollars, or null for no limit; and what they have spent. */
  budget_usd: number | null;
  cost_usd: number;
  /** How many messages in a row the agents may add without you before the group pauses. */
  breaker: number;
  avatar: string | null;
  pinned_at: string | null;
}

/** A file in a group: shared by someone in it, or made by an agent there. */
export interface GroupFile {
  name: string;
  size: number;
  mime: string;
  /** Storage key; the file is at `buildObjectUrl(key)`. */
  key: string;
  /** The start of its text, which the agents are shown. Absent for a picture or a file with no text. */
  excerpt?: string | null;
  truncated?: boolean;
  /** A picture of the first page (a PDF), at `buildObjectUrl(preview_key)`, and how many pages there are. */
  preview_key?: string | null;
  pages?: number | null;
  modified?: number | null;
  /** What a recording says, which the agents read. */
  transcript?: string | null;
}

export interface GroupEntry {
  seq: number;
  sender_id: string;
  sender: string;
  from_user: boolean;
  /** `system` is a note from the group itself (it paused), not something anyone said. */
  kind: "message" | "system";
  text: string;
  /** Names of who it addresses (`everyone` for all). */
  mentions: string[];
  reply_to: number | null;
  attachments: GroupFile[];
  at: string;
}

export interface GroupMessages {
  entries: GroupEntry[];
  latest: number;
  /** Members thinking about it right now. */
  working: string[];
  /** The latest entry every agent has read: your messages up to here show as seen by all. */
  read_by_all: number;
}

export interface Contact {
  agent_id: string;
  name: string;
  role: string;
  note: string;
}

export const groupsApi = {
  async getGroups(): Promise<Group[]> {
    return requestJson<Group[]>("/groups");
  },

  async createGroup(name: string, members: { agent_id: string; mode: MemberMode }[]): Promise<Group> {
    return requestJson<Group>("/groups", { method: "POST", body: JSON.stringify({ name, members }) });
  },

  async renameGroup(id: string, name: string): Promise<Group> {
    return requestJson<Group>(`/groups/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
  },

  async setGroupTokenCap(id: string, tokenCap: number): Promise<Group> {
    return requestJson<Group>(`/groups/${id}`, { method: "PATCH", body: JSON.stringify({ token_cap: tokenCap }) });
  },

  /** `budget_usd: 0` removes the dollar limit. */
  async setGroupLimits(id: string, limits: { budget_usd?: number; breaker?: number }): Promise<Group> {
    return requestJson<Group>(`/groups/${id}`, { method: "PATCH", body: JSON.stringify(limits) });
  },

  async deleteGroup(id: string): Promise<void> {
    await requestVoid(`/groups/${id}`, { method: "DELETE" });
  },

  /** What was said after `after` (the whole log when omitted). With `waitSeconds` the server holds the request until something is said. */
  async getGroupMessages(id: string, after = -1, waitSeconds = 0): Promise<GroupMessages> {
    return requestJson<GroupMessages>(`/groups/${id}/messages?after=${after}&wait=${waitSeconds}`);
  },

  async sendGroupMessage(id: string, text: string, replyTo: number | null, attachments: GroupFile[] = []): Promise<GroupEntry> {
    return requestJson<GroupEntry>(`/groups/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, reply_to: replyTo, attachments: attachments.map(({ key, name, size, mime, excerpt, truncated, preview_key, pages }) => ({ key, name, size, mime, excerpt, truncated, preview_key, pages })) }),
    });
  },

  /** Add a file to the group's shared files; send it with a message to attach it. */
  async uploadGroupFile(id: string, file: File): Promise<GroupFile> {
    const form = new FormData();
    form.append("file", file);
    return requestJson<GroupFile>(`/groups/${id}/files`, { method: "POST", body: form });
  },

  async getGroupFiles(id: string): Promise<GroupFile[]> {
    return requestJson<GroupFile[]>(`/groups/${id}/files`);
  },

  async setGroupAvatar(id: string, file: File): Promise<Group> {
    const form = new FormData();
    form.append("file", file);
    return requestJson<Group>(`/groups/${id}/avatar`, { method: "PUT", body: form });
  },

  async clearGroupAvatar(id: string): Promise<Group> {
    return requestJson<Group>(`/groups/${id}/avatar`, { method: "DELETE" });
  },

  async setGroupPinned(id: string, pinned: boolean): Promise<Group> {
    return requestJson<Group>(`/groups/${id}/pin`, { method: pinned ? "PUT" : "DELETE" });
  },

  async markGroupRead(id: string, upto: number): Promise<void> {
    await requestVoid(`/groups/${id}/read`, { method: "POST", body: JSON.stringify({ upto }) });
  },

  async addGroupMember(id: string, agentId: string, mode: MemberMode = "mentions"): Promise<Group> {
    return requestJson<Group>(`/groups/${id}/members`, { method: "POST", body: JSON.stringify({ agent_id: agentId, mode }) });
  },

  async setGroupMemberMode(id: string, agentId: string, mode: MemberMode): Promise<Group> {
    return requestJson<Group>(`/groups/${id}/members/${agentId}`, { method: "PATCH", body: JSON.stringify({ mode }) });
  },

  async removeGroupMember(id: string, agentId: string): Promise<void> {
    await requestVoid(`/groups/${id}/members/${agentId}`, { method: "DELETE" });
  },

  async getContacts(agentId: string): Promise<Contact[]> {
    return requestJson<Contact[]>(`/agents/${agentId}/contacts`);
  },

  async setContacts(agentId: string, contacts: { agent_id: string; note: string }[]): Promise<Contact[]> {
    return requestJson<Contact[]>(`/agents/${agentId}/contacts`, { method: "PUT", body: JSON.stringify({ contacts }) });
  },
};
