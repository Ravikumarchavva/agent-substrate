/* ── NoteNode — gold sticky note for documentation ───────────────────── */
"use client";

import { memo, useState, useCallback } from "react";
import { type Node, type NodeProps } from "@xyflow/react";
import { usePipelineStore } from "@/store/pipeline-store";

type NoteData = Node<{
  label: string;
  node_type: string;
  config: Record<string, unknown>;
}>;

function NoteNodeComponent({ id, data, selected }: NodeProps<NoteData>) {
  const text = String(data.config?.text ?? data.label ?? "");
  const updateNodeData = usePipelineStore((s) => s.updateNodeData);
  const [editing, setEditing] = useState(false);

  const handleBlur = useCallback((e: React.FocusEvent<HTMLTextAreaElement>) => {
    setEditing(false);
    updateNodeData(id, { config: { ...data.config, text: e.target.value }, label: e.target.value.slice(0, 40) || "Note" });
  }, [id, data.config, updateNodeData]);

  return (
    <div
      className={`
        min-w-[160px] max-w-[280px] rounded-lg p-3 transition-all
        ${selected ? "shadow-lg ring-2 ring-yellow-500/50" : "shadow-md"}
      `}
      style={{ background: "#c4a235", color: "#1a1500" }}
      onDoubleClick={() => setEditing(true)}
    >
      {editing ? (
        <textarea
          autoFocus
          defaultValue={text}
          onBlur={handleBlur}
          className="w-full bg-transparent text-[12px] leading-relaxed outline-none resize-none min-h-[60px]"
          style={{ color: "#1a1500" }}
        />
      ) : (
        <p className="text-[12px] leading-relaxed whitespace-pre-wrap">
          {text || "Double-click to add a note…"}
        </p>
      )}
      {/* Notes don't have handles — purely for documentation */}
    </div>
  );
}

export const NoteNode = memo(NoteNodeComponent);
