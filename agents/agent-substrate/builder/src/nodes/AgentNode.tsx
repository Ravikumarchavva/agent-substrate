/* ── AgentNode — compact card mirroring OpenAI builder ──────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { AgentIcon } from "./icons";

type AgentData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function AgentNodeComponent({ data, selected }: NodeProps<AgentData>) {
  const label = String(data.label || "Agent");

  return (
    <div className={`node-card node-type-agent ${selected ? "node-card-selected node-border-agent" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-icon bg-blue-500"><AgentIcon /></div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">Agent</div>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

export const AgentNode = memo(AgentNodeComponent);
