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
}

export interface AgentInput {
  name: string;
  role: string;
  instructions: string;
  allowed_tools: string[] | null;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export const agentsApi = {
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
    const { allowed_tools, ...rest } = body;
    return requestJson<Agent>(`/agents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(allowed_tools === null ? { ...rest, all_tools: true } : { ...rest, allowed_tools }),
    });
  },

  /** The agent's conversation (made the first time), to talk to it directly. */
  async openAgentThread(id: string): Promise<string> {
    return (await requestJson<{ id: string }>(`/agents/${id}/thread`, { method: "POST" })).id;
  },

  async deleteAgent(id: string): Promise<void> {
    await requestVoid(`/agents/${id}`, { method: "DELETE" });
  },
};
