/* ── Pipeline graph types (mirrors Python PipelineConfig schema) ────────── */

export type NodeType =
  | "agent"
  | "tool"
  | "skill"
  | "guardrail"
  | "router"
  | "memory"
  | "start"
  | "end"
  | "note"
  | "condition"
  | "approval"
  | "mcp";

export type EdgeType =
  | "agent_tool"
  | "agent_guardrail"
  | "agent_skill"
  | "agent_memory"
  | "router_route"
  | "router_input"
  | "agent_mcp";

export interface Position {
  x: number;
  y: number;
}

export interface NodeConfig {
  id: string;
  node_type: NodeType;
  label: string;
  position: Position;
  config: Record<string, unknown>;
}

export interface EdgeConfig {
  id: string;
  source: string;
  target: string;
  source_handle: string;
  target_handle: string;
  edge_type: EdgeType;
  label: string;
}

export interface PipelineConfig {
  id: string;
  name: string;
  description: string;
  nodes: NodeConfig[];
  edges: EdgeConfig[];
}

/* ── API response types ─────────────────────────────────────────────────── */

export interface PipelineOut {
  id: string;
  name: string;
  description: string | null;
  config: PipelineConfig;
  created_at: string;
  updated_at: string;
}

export interface RegistryTool {
  name: string;
  description: string;
  risk: "safe" | "sensitive" | "critical";
  hitl_mode: string;
  input_schema: Record<string, unknown>;
}

export interface RegistrySkill {
  name: string;
  description: string;
  version: string;
}

export interface RegistryGuardrailSchema {
  name: string;
  description: string;
  fields: Array<{ name: string; type: string; description: string }>;
}

export interface RegistryMcpServer {
  id: string;
  name: string;
  url: string;
  transport: "sse" | "stdio";
  command: string;
  args: string[];
  enabled_tools: string[];
}

export interface RegistryResponse {
  tools: RegistryTool[];
  skills: RegistrySkill[];
  guardrail_schemas: RegistryGuardrailSchema[];
  models: string[];
  mcp_servers: RegistryMcpServer[];
}

/* ── SSE event types (from /builder/pipelines/{id}/run) ─────────────────── */

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}
