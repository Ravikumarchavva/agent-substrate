/* ── MemoryNode — memory backend card ─────────────────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { MemoryIcon } from "./icons";

type MemoryData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function MemoryNodeComponent({ data, selected }: NodeProps<MemoryData>) {
  const label = String(data.label || "Memory");

  return (
    <div className={`node-card node-type-memory ${selected ? "node-card-selected node-border-memory" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-icon bg-pink-500"><MemoryIcon /></div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">Memory</div>
      </div>
    </div>
  );
}

export const MemoryNode = memo(MemoryNodeComponent);
