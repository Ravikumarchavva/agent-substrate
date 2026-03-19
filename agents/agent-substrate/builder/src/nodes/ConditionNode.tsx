/* ── ConditionNode — If/else with expression-based routing ────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ConditionIcon } from "./icons";

type ConditionData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function ConditionNodeComponent({ data, selected }: NodeProps<ConditionData>) {
  const label = String(data.label || "If / else");
  const conditions = Array.isArray(data.config?.conditions)
    ? (data.config.conditions as Array<{ expression: string; label: string }>)
    : [];

  return (
    <div className={`node-multi node-multi-condition ${selected ? "node-multi-selected node-border-condition" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />

      {/* Header */}
      <div className="node-multi-header">
        <div className="node-icon" style={{ background: "#f59e0b20", color: "#f59e0b" }}><ConditionIcon /></div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          <div className="node-sub">If / else</div>
        </div>
      </div>

      {/* Condition branches */}
      <div className="node-multi-section">
        {conditions.map((c, i) => (
          <div key={i} className="node-multi-row node-multi-row-branch relative">
            <span className="node-expression-text">
              {c.expression || c.label || `Condition ${i + 1}`}
            </span>
            <span className="node-row-output">Output</span>
            <Handle type="source" position={Position.Right} id={`cond-${i}`} className="node-handle node-handle-branch" />
          </div>
        ))}
        <div className="node-multi-row node-multi-row-branch relative">
          <span className="node-branch-pill node-branch-pill-muted">Else</span>
          <span className="node-row-output">Output</span>
          <Handle type="source" position={Position.Right} id="else" className="node-handle node-handle-branch" />
        </div>
      </div>
    </div>
  );
}

export const ConditionNode = memo(ConditionNodeComponent);
