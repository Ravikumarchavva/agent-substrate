/* ── RouterNode — routing with condition branches (Classify style) ─────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ClassifyIcon } from "./icons";

type RouterData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function RouterNodeComponent({ data, selected }: NodeProps<RouterData>) {
  const label = String(data.label || "Classify");
  const routes = Array.isArray(data.config?.routes) ? (data.config.routes as string[]) : [];

  return (
    <div className={`node-multi node-multi-router ${selected ? "node-multi-selected node-border-router" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />

      {/* Header */}
      <div className="node-multi-header">
        <div className="node-icon bg-cyan-500"><ClassifyIcon /></div>
        <div className="node-body">
          <div className="node-label">{label}</div>
          <div className="node-sub">Router</div>
        </div>
      </div>

      {/* Route branches */}
      {routes.length > 0 && (
        <div className="border-t" style={{ borderColor: "var(--border)" }}>
          {routes.map((r: string, i: number) => (
            <div key={i} className="node-multi-row relative">
              <span className="text-[11px] font-mono">{r}</span>
              <Handle type="source" position={Position.Right} id={`route-${i}`} className="node-handle" />
            </div>
          ))}
          <div className="node-multi-row relative">
            <span style={{ color: "var(--text-dim)" }}>Else</span>
            <Handle type="source" position={Position.Right} id="else" className="node-handle" />
          </div>
        </div>
      )}

      {routes.length === 0 && (
        <Handle type="source" position={Position.Right} className="node-handle" />
      )}
    </div>
  );
}

export const RouterNode = memo(RouterNodeComponent);
