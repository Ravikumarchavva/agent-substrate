/* ── ApprovalNode — user approval gate with Approve/Reject ───────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ApprovalIcon } from "./icons";

type ApprovalData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function ApprovalNodeComponent({ data, selected }: NodeProps<ApprovalData>) {
  const label = String(data.label || "User approval");
  const prompt = String(data.config?.prompt ?? "");

  return (
    <div className={`node-multi node-multi-approval ${selected ? "node-multi-selected node-border-approval" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />

      {/* Header */}
      <div className="node-multi-header">
        <div className="node-icon bg-orange-500"><ApprovalIcon /></div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          <div className="node-sub">User approval</div>
        </div>
      </div>

      {/* Approve / Reject branches */}
      {prompt && <div className="node-multi-description">{prompt}</div>}

      <div className="node-multi-rows">
        <div className="node-multi-row relative">
          <span className="node-branch-label">Approve</span>
          <Handle type="source" position={Position.Right} id="approve" className="node-handle" />
        </div>
        <div className="node-multi-row relative">
          <span className="node-branch-label">Reject</span>
          <Handle type="source" position={Position.Right} id="reject" className="node-handle" />
        </div>
      </div>
    </div>
  );
}

export const ApprovalNode = memo(ApprovalNodeComponent);
