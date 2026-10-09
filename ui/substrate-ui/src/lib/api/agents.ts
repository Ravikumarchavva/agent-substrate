import { requestJson, requestVoid } from "./_client";

export interface Agent {
  id: string;
  name: string;
  role: string;
  instructions: string;
  /** `null`: every tool. */
  allowed_tools: string[] | null;
  /** Where its files live; the Storage page shows them under its name. */
  workspace_id: string;
  created_at: string;
  /** The agent's one conversation; absent until it is first opened. */
  thread_id: string | null;
  last_active: string | null;
  /** A one-line preview of the last thing said in that conversation. */
  last_message: string | null;
  /** Working on a reply to you right now. */
  working: boolean;
  /** The model it thinks with (`provider/name`), or null for the deployment's own. */
  model: string | null;
  /** Whether that model reads pictures; null while it uses the deployment's. */
  sees: boolean | null;
  /** Its picture (an object key; show it with `buildObjectUrl`), or null for initials. */
  avatar: string | null;
  /** When you pinned its chat to the top of your list, or null. */
  pinned_at: string | null;
}

export interface AgentInput {
  name: string;
  role: string;
  instructions: string;
  allowed_tools: string[] | null;
  model: string | null;
}

export interface ToolInfo {
  name: string;
  description: string;
}

/** One time an agent asked another to do something. */
export interface Exchange {
  thread_id: string;
  asker_id: string | null;
  asker: string;
  target_id: string;
  target: string;
  request: string;
  /** What came back; null while nothing has. */
  answer: string | null;
  /** `waiting` means it stopped to ask you something. */
  status: "done" | "working" | "waiting" | "failed";
  at: string;
}

export const agentsApi = {
  /** When this agent asked another agent for something, or was asked, newest first. */
  async getAgentExchanges(id: string): Promise<Exchange[]> {
    return requestJson<Exchange[]>(`/agents/${id}/exchanges`);
  },

  async getAgents(): Promise<Agent[]> {
    return requestJson<Agent[]>("/agents");
  },

  async getAgentTools(): Promise<ToolInfo[]> {
    return requestJson<ToolInfo[]>("/agents/tools");
  },

  async createAgent(body: AgentInput): Promise<Agent> {
    return requestJson<Agent>("/agents", { method: "POST", body: JSON.stringify(body) });
  },

  async updateAgent(id: string, body: AgentInput): Promise<Agent> {
    const { allowed_tools, model, ...rest } = body;
    // `null` means "all tools" and "the deployment's model": for a patch, where null also means "leave it", they are flags.
    return requestJson<Agent>(`/agents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({
        ...rest,
        ...(allowed_tools === null ? { all_tools: true } : { allowed_tools }),
        ...(model === null ? { default_model: true } : { model }),
      }),
    });
  },

  /** The agent's conversation (made the first time), to talk to it directly. */
  async openAgentThread(id: string): Promise<string> {
    return (await requestJson<{ id: string }>(`/agents/${id}/thread`, { method: "POST" })).id;
  },

  async setAgentAvatar(id: string, file: File): Promise<Agent> {
    const form = new FormData();
    form.append("file", file);
    return requestJson<Agent>(`/agents/${id}/avatar`, { method: "PUT", body: form });
  },

  async clearAgentAvatar(id: string): Promise<Agent> {
    return requestJson<Agent>(`/agents/${id}/avatar`, { method: "DELETE" });
  },

  async setAgentPinned(id: string, pinned: boolean): Promise<Agent> {
    return requestJson<Agent>(`/agents/${id}/pin`, { method: pinned ? "PUT" : "DELETE" });
  },

  async deleteAgent(id: string): Promise<void> {
    await requestVoid(`/agents/${id}`, { method: "DELETE" });
  },
};
