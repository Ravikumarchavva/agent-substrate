/* ── McpNode — MCP server card ───────────────────────────────────────── */
"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { McpIcon } from "./icons";

type McpData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function _hostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function McpNodeComponent({ data, selected }: NodeProps<McpData>) {
  const cfg = data.config ?? {};
  const label =
    (cfg.server_name as string) ||
    (cfg.url ? _hostname(cfg.url as string) : null) ||
    data.label ||
    "MCP Server";
  const transport = (cfg.transport as string) ?? "sse";

  return (
    <div
      className={`node-card node-type-mcp ${selected ? "node-card-selected node-border-mcp" : ""}`}
    >
      {/* Source handle — connects to Agent target handle */}
      <Handle type="source" position={Position.Right} className="node-handle" />
      <div
        className="node-icon"
        style={{ background: "#a855f720", color: "#a855f7" }}
      >
        <McpIcon />
      </div>
      <div className="node-body">
        <div className="node-label">{label}</div>
        <div className="node-sub">{transport === "stdio" ? "stdio" : "SSE"}</div>
      </div>
    </div>
  );
}

export const McpNode = memo(McpNodeComponent);
