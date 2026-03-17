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
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        minWidth: 116,
        padding: "8px 14px 8px 10px",
        borderRadius: 14,
        border: selected
          ? "1.5px solid rgba(16,185,129,0.8)"
          : "1.5px solid rgba(16,185,129,0.22)",
        boxShadow: selected
          ? "0 0 0 3px rgba(16,185,129,0.18), 0 10px 24px rgba(0,0,0,0.22)"
          : "0 8px 20px rgba(0,0,0,0.18)",
        background: selected
          ? "linear-gradient(180deg, rgba(16,185,129,0.18) 0%, rgba(16,185,129,0.1) 100%)"
          : "linear-gradient(180deg, rgba(24,37,31,0.98) 0%, rgba(18,28,24,0.98) 100%)",
        transition: "border-color 150ms ease, box-shadow 150ms ease, background 150ms ease",
        cursor: "pointer",
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 11,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          background: "linear-gradient(180deg, #20c997 0%, #10b981 100%)",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.16), 0 4px 12px rgba(16,185,129,0.28)",
          color: "white",
        }}
      >
        <StartIcon />
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
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

export const StartNode = memo(StartNodeComponent);
