/* ── SkillNode — skill card ────────────────────────────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { SkillIcon } from "./icons";

type SkillData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function SkillNodeComponent({ data, selected }: NodeProps<SkillData>) {
  const label = String(data.label || data.config?.skill_name || "Skill");

  return (
    <div className={`node-card node-type-skill ${selected ? "node-card-selected node-border-skill" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-icon" style={{ background: "#a855f720", color: "#a855f7" }}><SkillIcon /></div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">MCP / Skill</div>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

export const SkillNode = memo(SkillNodeComponent);
