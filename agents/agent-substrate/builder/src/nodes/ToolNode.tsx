/* ── ToolNode — tool card ─────────────────────────────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ToolIcon } from "./icons";

type ToolData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function ToolNodeComponent({ data, selected }: NodeProps<ToolData>) {
  const label = String(data.label || data.config?.tool_name || "Tool");

  return (
    <div className={`node-card node-type-tool ${selected ? "node-card-selected node-border-tool" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-icon" style={{ background: "#22c55e20", color: "#22c55e" }}><ToolIcon /></div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">Tool</div>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

export const ToolNode = memo(ToolNodeComponent);
