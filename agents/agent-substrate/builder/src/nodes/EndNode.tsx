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
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        minWidth: 110,
        padding: "8px 14px 8px 10px",
        borderRadius: 14,
        border: selected
          ? "1.5px solid rgba(16,185,129,0.8)"
          : "1.5px solid rgba(255,255,255,0.1)",
        boxShadow: selected
          ? "0 0 0 3px rgba(16,185,129,0.18), 0 10px 24px rgba(0,0,0,0.22)"
          : "0 8px 20px rgba(0,0,0,0.18)",
        background: selected
          ? "linear-gradient(180deg, rgba(22,30,28,1) 0%, rgba(17,23,21,1) 100%)"
          : "linear-gradient(180deg, rgba(28,28,28,0.98) 0%, rgba(20,20,20,0.98) 100%)",
        transition: "border-color 150ms ease, box-shadow 150ms ease, background 150ms ease",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 11,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          background: "linear-gradient(180deg, #151515 0%, #0f0f0f 100%)",
          border: "1.5px solid rgba(255,255,255,0.14)",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08)",
          color: "#10b981",
        }}
      >
        <EndIcon />
      </div>
      <span
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text)",
          letterSpacing: "-0.01em",
          userSelect: "none",
        }}
      >
        {label}
      </span>
    </div>
  );
}

export const EndNode = memo(EndNodeComponent);
