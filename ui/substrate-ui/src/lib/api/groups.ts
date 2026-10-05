import { requestJson, requestVoid } from "./_client";

/** How closely a member follows the group: every message, only what is addressed to it, or only what names it. */
export type MemberMode = "all" | "mentions" | "muted";

export interface GroupMember {
  agent_id: string;
  name: string;
  role: string;
  mode: MemberMode;
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
  at: string;
}

export interface GroupMessages {
  entries: GroupEntry[];
  latest: number;
  /** Members thinking about it right now. */
  working: string[];
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

  async deleteGroup(id: string): Promise<void> {
    await requestVoid(`/groups/${id}`, { method: "DELETE" });
  },

  /** What was said after `after` (the whole log when omitted). */
  async getGroupMessages(id: string, after = -1): Promise<GroupMessages> {
    return requestJson<GroupMessages>(`/groups/${id}/messages?after=${after}`);
  },

  async sendGroupMessage(id: string, text: string, replyTo: number | null): Promise<GroupEntry> {
    return requestJson<GroupEntry>(`/groups/${id}/messages`, { method: "POST", body: JSON.stringify({ text, reply_to: replyTo }) });
  },

  async markGroupRead(id: string, upto: number): Promise<void> {
    await requestVoid(`/groups/${id}/read`, { method: "POST", body: JSON.stringify({ upto }) });
  },

  async addGroupMember(id: string, agentId: string, mode: MemberMode = "all"): Promise<Group> {
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
