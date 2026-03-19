/* ── EndNode — terminal node ──────────────────────────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { EndIcon } from "./icons";

type EndData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function EndNodeComponent({ data, selected }: NodeProps<EndData>) {
  const label = String(data.label || "End");

  return (
    <div className={`node-card ${selected ? "node-card-selected" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-icon" style={{ background: "#22c55e20", color: "#22c55e" }}>
        <EndIcon />
      </div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">Terminate flow</div>
      </div>
    </div>
  );
}

export const EndNode = memo(EndNodeComponent);
