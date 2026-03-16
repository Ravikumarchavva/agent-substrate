/* ── CanvasToolbar — top bar + floating bottom bar ────────────────────────
 *
 * Top bar: pipeline name · Draft badge · Settings · Evaluate · Code · Publish
 * Bottom bar: floating pill with play / undo / redo / zoom controls
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useState, useRef, useCallback } from "react";
import { usePipelineStore } from "@/store/pipeline-store";
import { api } from "@/lib/api";
import { Button, Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import styles from "./CanvasToolbar.module.css";

/* ── Main component ───────────────────────────────────────────────────── */

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
  const isDirty = usePipelineStore((s) => s.isDirty);
  const isRunning = usePipelineStore((s) => s.isRunning);
  const undo = usePipelineStore((s) => s.undo);
  const redo = usePipelineStore((s) => s.redo);
  const undoLen = usePipelineStore((s) => s.undoStack.length);
  const redoLen = usePipelineStore((s) => s.redoStack.length);

  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  /* ── Save pipeline ─────────────────────────────────────────────────── */
  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const config = toPipelineConfig();
      if (pipelineId) {
        await api.updatePipeline(pipelineId, { name: pipelineName, config });
      } else {
        const created = await api.createPipeline(pipelineName, config);
        setPipelineId(created.id);
      }
      markClean();
    } catch (err) {
      console.error("Save failed:", err);
    } finally {
      setSaving(false);
    }
  }, [pipelineId, pipelineName, toPipelineConfig, setPipelineId, markClean]);

  /* ── Export as Python ──────────────────────────────────────────────── */
  const handleExport = useCallback(async () => {
    if (!pipelineId) {
      alert("Save the pipeline first before exporting.");
      return;
    }
    try {
      const code = await api.exportPipeline(pipelineId);
      const blob = new Blob([code], { type: "text/x-python" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${pipelineName.replace(/\s+/g, "_").toLowerCase()}.py`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed:", err);
    }
  }, [pipelineId, pipelineName]);

  /* ── Import from JSON ──────────────────────────────────────────────── */
  const handleImport = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const config = JSON.parse(text);
        usePipelineStore.getState().loadPipeline(config);
      } catch (err) {
        console.error("Import failed:", err);
      }
    };
    input.click();
  }, []);

  /* ── Inline name editing ───────────────────────────────────────────── */
  const startEdit = () => {
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };
  const finishEdit = () => {
    setEditing(false);
    if (!inputRef.current?.value.trim()) setPipelineName("Untitled Pipeline");
  };

  const handleDelete = useCallback(async () => {
    if (!pipelineId) return;
    const confirmed = window.confirm(`Delete workflow “${pipelineName}”? This cannot be undone.`);
    if (!confirmed) return;

    try {
      await api.deletePipeline(pipelineId);
      onDeleteComplete?.();
      onBack();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  }, [pipelineId, pipelineName, onBack, onDeleteComplete]);

  return (
    <div className={styles.shell}>
      {/* ── TOP BAR ─────────────────────────────────────────────────── */}
      <header
        className={styles.header}
        style={{ background: "var(--bg-surface)", borderColor: "var(--border)" }}
      >
        {/* Back arrow */}
        <button
          onClick={onBack}
          title="Back to home"
          className={styles.backButton}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>

        <div className={styles.nameGroup}>
          <div>
            {editing ? (
              <input
                ref={inputRef}
                value={pipelineName}
                onChange={(e) => setPipelineName(e.target.value)}
                onBlur={finishEdit}
                onKeyDown={(e) => e.key === "Enter" && finishEdit()}
                className={styles.nameInput}
                style={{ width: `${Math.max(pipelineName.length * 9, 180)}px` }}
              />
            ) : (
              <button onClick={startEdit} className={styles.nameButton} title="Rename workflow">
                {pipelineName}
              </button>
            )}
            <div className={styles.subtleText}>Workflow editor</div>
          </div>
          <Badge variant={isDirty ? "warning" : "saved"}>
            {isDirty ? "Draft" : "Saved"}
          </Badge>
        </div>

        <div className={styles.spacer} />

        <div className={styles.group}>
          <Button onClick={handleImport} title="Import JSON" variant="ghost" size="sm">Import</Button>
          <Button onClick={handleExport} disabled={!pipelineId} title="Export Python" variant="ghost" size="sm">
            &lt;/&gt; Code
          </Button>
        </div>

        <div className={styles.divider} />

        <div className={styles.group}>
          <Button onClick={handleSave} disabled={saving} title="Save pipeline" variant="accent" size="sm">
            {saving ? "Saving…" : "Save"}
          </Button>
          <div className={styles.runButton}>
            <Button onClick={onRun} disabled={isRunning} title="Run pipeline" variant="primary" size="sm">
              {isRunning ? "Running…" : "▶ Run"}
            </Button>
          </div>
        </div>

        {pipelineId ? (
          <>
            <div className={styles.divider} />
            <Button onClick={handleDelete} title="Delete workflow" variant="danger" size="sm">
              Delete
            </Button>
          </>
        ) : null}

        <div className={styles.divider} />
        <ThemeToggle />
      </header>

      {/* ── BOTTOM FLOATING BAR ─────────────────────────────────────── */}
      <div
        className={styles.floatingDock}
        style={{ background: "var(--bg-surface)", borderColor: "var(--border)" }}
      >
        <Button onClick={undo} disabled={undoLen === 0} title="Undo (Ctrl+Z)" variant="ghost" size="sm">↶ Undo</Button>
        <Button onClick={redo} disabled={redoLen === 0} title="Redo (Ctrl+Y)" variant="ghost" size="sm">↷ Redo</Button>
        <div className="w-px h-4 mx-0.5" style={{ background: "var(--border)" }} />
        <Button onClick={onRun} disabled={isRunning} title="Open test chat" variant="ghost" size="sm">
          <span style={{ color: "var(--success)" }}>▶</span> Test chat
        </Button>
      </div>
    </div>
  );
}
