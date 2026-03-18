/* ── WhileNode — repeat-until loop control node ──────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { WhileIcon } from "./icons";

type WhileData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function WhileNodeComponent({ data, selected }: NodeProps<WhileData>) {
  const label = String(data.label || "While");
  const condition = String(data.config?.condition || "");

  return (
    <div className={`node-multi node-multi-condition ${selected ? "node-multi-selected node-border-condition" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />

      {/* Header */}
      <div className="node-multi-header">
        <div className="node-icon" style={{ background: "#f59e0b22", color: "#f59e0b" }}>
          <WhileIcon />
        </div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          <div className="node-sub">While loop</div>
        </div>
      </div>

      {/* Condition + exit handles */}
      <div className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="node-multi-row relative">
          <span className="text-[10px] font-mono truncate max-w-[180px]">
            {condition || "condition expression"}
          </span>
          <Handle
            type="source"
            position={Position.Right}
            id="body"
            className="node-handle"
            style={{ top: "50%" }}
          />
        </div>
        <div className="node-multi-row relative">
          <span style={{ color: "var(--text-dim)" }}>Done</span>
          <Handle
            type="source"
            position={Position.Right}
            id="done"
            className="node-handle"
            style={{ top: "50%" }}
          />
        </div>
      </div>
    </div>
  );
}

export const WhileNode = memo(WhileNodeComponent);
