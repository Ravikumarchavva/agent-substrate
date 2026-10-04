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

  async deleteAgent(id: string): Promise<void> {
    await requestVoid(`/agents/${id}`, { method: "DELETE" });
  },
};
