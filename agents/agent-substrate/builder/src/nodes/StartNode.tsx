/* ── StartNode — entry point with play icon ─────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { StartIcon } from "./icons";

type StartData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function StartNodeComponent({ data, selected }: NodeProps<StartData>) {
  const label = String(data.label || "Start");

  return (
    <div className={`node-card ${selected ? "node-card-selected" : ""}`}>
      <div className="node-icon" style={{ background: "#22c55e20", color: "#22c55e" }}>
        <StartIcon />
      </div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">Entry point</div>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

export const StartNode = memo(StartNodeComponent);
