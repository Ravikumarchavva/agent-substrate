/* ── GuardrailNode — shield card with Pass/Fail branches ─────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { GuardrailIcon } from "./icons";

type GuardrailData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function GuardrailNodeComponent({ data, selected }: NodeProps<GuardrailData>) {
  const label = String(data.label || "Guardrails");
  const schema = String(data.config?.schema_name || "");

  return (
    <div className={`node-multi node-multi-guardrail ${selected ? "node-multi-selected node-border-guardrail" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />

      {/* Main header */}
      <div className="node-multi-header">
        <div className="node-icon" style={{ background: "#f59e0b20", color: "#f59e0b" }}><GuardrailIcon /></div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          {schema && <div className="node-sub">{schema}</div>}
        </div>
      </div>

      {/* Pass / Fail branches */}
      <div className="node-multi-section">
        <div className="node-multi-row node-multi-row-branch relative">
          <span className="node-branch-pill node-branch-pill-success">Pass</span>
          <span className="node-row-output">Output</span>
          <Handle type="source" position={Position.Right} id="pass" className="node-handle node-handle-branch" />
        </div>
        <div className="node-multi-row node-multi-row-branch relative">
          <span className="node-branch-pill node-branch-pill-danger">Fail</span>
          <span className="node-row-output">Output</span>
          <Handle type="source" position={Position.Right} id="fail" className="node-handle node-handle-branch" />
        </div>
      </div>
    </div>
  );
}

export const GuardrailNode = memo(GuardrailNodeComponent);
