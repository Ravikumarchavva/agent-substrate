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
        <div className="node-icon bg-amber-500"><GuardrailIcon /></div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          {schema && <div className="node-sub">{schema}</div>}
        </div>
      </div>

      {/* Pass / Fail branches */}
      <div className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="node-multi-row relative">
          <span className="text-[10px] text-emerald-400 font-medium">Pass</span>
          <Handle type="source" position={Position.Right} id="pass" className="node-handle" />
        </div>
        <div className="node-multi-row relative">
          <span className="text-[10px] text-red-400 font-medium">Fail</span>
          <Handle type="source" position={Position.Right} id="fail" className="node-handle" />
        </div>
      </div>
    </div>
  );
}

export const GuardrailNode = memo(GuardrailNodeComponent);
