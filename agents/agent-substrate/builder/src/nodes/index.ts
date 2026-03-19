/* ── Node type registry for React Flow ───────────────────────────────── */

export { AgentNode } from "./AgentNode";
export { ToolNode } from "./ToolNode";
export { SkillNode } from "./SkillNode";
export { GuardrailNode } from "./GuardrailNode";
export { RouterNode } from "./RouterNode";
export { MemoryNode } from "./MemoryNode";
export { StartNode } from "./StartNode";
export { EndNode } from "./EndNode";
export { NoteNode } from "./NoteNode";
export { ConditionNode } from "./ConditionNode";
export { ApprovalNode } from "./ApprovalNode";
export { WhileNode } from "./WhileNode";
export { McpNode } from "./McpNode";

import type { NodeTypes } from "@xyflow/react";
import { AgentNode } from "./AgentNode";
import { ToolNode } from "./ToolNode";
import { SkillNode } from "./SkillNode";
import { GuardrailNode } from "./GuardrailNode";
import { RouterNode } from "./RouterNode";
import { MemoryNode } from "./MemoryNode";
import { StartNode } from "./StartNode";
import { EndNode } from "./EndNode";
import { NoteNode } from "./NoteNode";
import { ConditionNode } from "./ConditionNode";
import { ApprovalNode } from "./ApprovalNode";
import { WhileNode } from "./WhileNode";
import { McpNode } from "./McpNode";

export const nodeTypes: NodeTypes = {
  agent: AgentNode,
  tool: ToolNode,
  skill: SkillNode,
  guardrail: GuardrailNode,
  router: RouterNode,
  memory: MemoryNode,
  start: StartNode,
  end: EndNode,
  note: NoteNode,
  condition: ConditionNode,
  approval: ApprovalNode,
  while: WhileNode,
  mcp: McpNode,
};
