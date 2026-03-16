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
    <div className={`
      flex items-center gap-2.5 px-4 py-2.5 rounded-full border transition-all
      ${selected
        ? "border-emerald-500 shadow-[0_0_0_1px_rgba(16,185,129,0.5)] bg-emerald-500/5"
        : "border-[var(--border)] bg-[var(--bg-surface)] hover:border-[var(--text-dim)]"
      }
    `}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 text-white bg-emerald-500">
        <EndIcon />
      </div>
      <span className="text-[13px] font-medium text-[var(--text)] pr-1">{label}</span>
    </div>
  );
}

export const EndNode = memo(EndNodeComponent);
