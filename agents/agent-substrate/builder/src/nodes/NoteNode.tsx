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
      className={`node-note ${selected ? "ring-2 ring-yellow-500/50" : ""}`}
      onDoubleClick={() => setEditing(true)}
    >
      {editing ? (
        <textarea
          autoFocus
          defaultValue={text}
          onBlur={handleBlur}
          className="w-full bg-transparent text-[12px] leading-relaxed outline-none resize-none min-h-[60px]"
        />
      ) : (
        <p className="text-[12px] leading-relaxed whitespace-pre-wrap">
          {text || "Double-click to add a note…"}
        </p>
      )}
    </div>
  );
}

export const NoteNode = memo(NoteNodeComponent);
