"use client";

import { useState, useRef, useCallback } from "react";
import { usePipelineStore } from "@/store/pipeline-store";
import { api } from "@/lib/api";
import { Button, Badge, Input } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ChevronLeft, Play, Save, Settings2, Trash2, Code2, Copy, Pencil, Network } from "lucide-react";

interface CanvasToolbarProps {
  onRun: () => void;
  onBack: () => void;
  onDeleteComplete?: () => void;
}

export function CanvasToolbar({ onRun, onBack, onDeleteComplete }: CanvasToolbarProps) {
  const pipelineId = usePipelineStore((s) => s.pipelineId);
  const pipelineName = usePipelineStore((s) => s.pipelineName);
  const setPipelineName = usePipelineStore((s) => s.setPipelineName);
  const setPipelineId = usePipelineStore((s) => s.setPipelineId);
  const toPipelineConfig = usePipelineStore((s) => s.toPipelineConfig);
  const markClean = usePipelineStore((s) => s.markClean);

  const [saving, setSaving] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [tempName, setTempName] = useState(pipelineName);
  const nameInputRef = useRef<HTMLInputElement>(null);

  const startNameEdit = () => {
    setTempName(pipelineName);
    setEditingName(true);
    setTimeout(() => {
      nameInputRef.current?.focus();
      nameInputRef.current?.select();
    }, 0);
  };

  const commitNameEdit = () => {
    if (tempName.trim()) {
      setPipelineName(tempName.trim());
    }
    setEditingName(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") commitNameEdit();
    if (e.key === "Escape") {
      setEditingName(false);
      setTempName(pipelineName);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const conf = toPipelineConfig();
      if (!pipelineId) {
         const created = await api.createPipeline(pipelineName, conf);
        setPipelineId(created.id);
      } else {
        await api.updatePipeline(pipelineId, conf);
      }
      markClean();
    } catch (err) {
      console.error("Save failed", err);
      alert("Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!pipelineId) return;
    if (!window.confirm("Delete this workflow?")) return;
    try {
      await api.deletePipeline(pipelineId);
      onDeleteComplete?.();
      onBack();
    } catch (err) {
      console.error("Delete failed", err);
      alert("Delete failed");
    }
  };

  return (
    <>
      <header className="border-b border-(--border) bg-(--bg-surface)/80 backdrop-blur-md px-4 py-2.5 flex items-center justify-between shrink-0 z-50">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" className="h-7 w-7 text-(--text-dim) hover:bg-(--bg-hover)" onClick={onBack} title="Back to home">
            <ChevronLeft className="w-4 h-4" />
          </Button>

          <div className="h-5 w-px bg-(--border) mx-0.5" />

          {editingName ? (
            <div className="flex items-center gap-2">
              <Input
                ref={nameInputRef}
                value={tempName}
                 onChange={(val) => setTempName(val)}
                onBlur={commitNameEdit}
                onKeyDown={onKeyDown}
                className="h-7 w-44 text-xs px-2"
              />
            </div>
          ) : (
            <div
              className="flex items-center gap-2 px-2 py-1 -ml-2 rounded-lg hover:bg-(--bg-hover) cursor-pointer transition-colors group"
              onClick={startNameEdit}
              title="Rename workflow"
            >
              <span className="font-semibold text-xs text-(--text) max-w-[200px] truncate">{pipelineName}</span>
              <Pencil className="w-3 h-3 text-(--text-dim) opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
          )}

          <Badge variant="outline" className="text-[9px] font-normal px-1.5 py-0 h-4 w-8 bg-(--bg-elevated) text-(--text-dim) border-(--border)">Draft</Badge>
        </div>

        <div className="flex items-center gap-1.5">
          <Button variant="outline" size="sm" className="h-7 w-16 text-[11px] bg-(--bg) border-(--border) hover:cursor-pointer" onClick={handleSave} disabled={saving}>
            <Save className="w-3 h-3 mr-1" />
            {saving ? "Saving..." : "Save"}
          </Button>
          
          <Button variant="outline" size="icon" className="h-7 w-7 bg-(--bg) border-(--border) hover:cursor-pointer" title="Copy pipeline JSON" onClick={() => {
            navigator.clipboard.writeText(JSON.stringify(toPipelineConfig(), null, 2));
          }}>
            <Code2 className="w-3.5 h-3.5 text-(--text-dim)" />
          </Button>

          {pipelineId && (
            <Button variant="outline" size="icon" className="h-7 w-7 bg-(--bg) text-(--danger) hover:bg-(--danger)/10 border-transparent hover:cursor-pointer" title="Delete" onClick={handleDelete}>
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          )}

          <div className="h-5 w-px bg-(--border) mx-0.5" />
          
          <Button size="sm" className="h-7 w-16 text-[11px] gap-1.5 font-medium hover:cursor-pointer" onClick={onRun}>
             <Play className="w-3 h-3 fill-current" />
             Run
          </Button>

          <ThemeToggle />
        </div>
      </header>

    </>
  );
}