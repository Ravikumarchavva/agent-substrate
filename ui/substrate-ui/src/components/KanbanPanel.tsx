"use client";

import { useRef, useEffect, useState } from "react";
import { Task, TaskList, TaskStatus } from "@/types";
import { api } from "@/lib/api";
import {
  Circle, Loader2, CheckCircle2, Plus, X, ListTodo,
} from "lucide-react";
import { PanelShell } from "@/components/PanelShell";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KanbanPanelProps {
  taskList: TaskList | null;
  onTaskStatusChange?: (taskListId: string, taskId: string, status: TaskStatus) => void;
  onTaskDelete?: (taskListId: string, taskId: string) => void;
  onTaskAdd?: (taskListId: string, title: string) => void;
  onDismiss?: () => void;
}

// ---------------------------------------------------------------------------
// Status progression (click to advance)
// ---------------------------------------------------------------------------

const NEXT: Record<TaskStatus, TaskStatus> = {
  todo: "in_progress",
  in_progress: "done",
  done: "todo",
  failed: "todo",
};

// ---------------------------------------------------------------------------
// Single task row
// ---------------------------------------------------------------------------

function TaskRow({
  task,
  taskListId,
  onStatusChange,
  onDelete,
}: {
  task: Task;
  taskListId: string;
  onStatusChange: (listId: string, taskId: string, status: TaskStatus) => void;
  onDelete: (listId: string, taskId: string) => void;
}) {
  const done = task.status === "done";
  const inProgress = task.status === "in_progress";
  const failed = task.status === "failed";

  return (
    <div
      className="group flex items-center gap-2.5 px-2 py-1.5 rounded-lg cursor-pointer transition-colors hover:bg-(--card-hover)"
      onClick={() => onStatusChange(taskListId, task.id, NEXT[task.status])}
      title={
        done ? "Click to reset" : failed ? "Click to retry" : inProgress ? "Click to mark done" : "Click to start"
      }
    >
      {/* Status icon */}
      <span className="shrink-0">
        {done ? (
          <CheckCircle2 className="w-4 h-4" style={{ color: "#22c55e" }} />
        ) : failed ? (
          <Circle className="w-4 h-4" style={{ color: "#ef4444" }} />
        ) : inProgress ? (
          <Loader2 className="w-4 h-4 animate-spin" style={{ color: "#f59e0b" }} />
        ) : (
          <Circle className="w-4 h-4" style={{ color: "var(--muted)" }} />
        )}
      </span>

      {/* Title */}
      <span
        className="flex-1 text-sm leading-snug"
        style={{
          color: done ? "var(--muted)" : failed ? "#f87171" : "var(--foreground)",
          textDecoration: done ? "line-through" : "none",
        }}
      >
        {task.title}
      </span>

      {/* Delete (hover only) */}
      <button
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded transition-all hover:bg-(--card) cursor-pointer"
        style={{ color: "var(--muted)" }}
        onClick={(e) => {
          e.stopPropagation();
          onDelete(taskListId, task.id);
        }}
        aria-label="Delete task"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section divider
// ---------------------------------------------------------------------------

function SectionHeader({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className="flex items-center gap-2 px-2 pt-2 pb-1">
      <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color }}>
        {label}
      </span>
      {count > 0 && (
        <span
          className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
          style={{ background: "var(--card)", color: "var(--muted)" }}
        >
          {count}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline add-task form (shown under Pending section)
// ---------------------------------------------------------------------------

function AddTaskForm({ taskListId, onAdd }: { taskListId: string; onAdd: (id: string, t: string) => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) ref.current?.focus(); }, [open]);

  const submit = () => {
    const t = value.trim();
    if (!t) return;
    onAdd(taskListId, t);
    setValue("");
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-2 py-1.5 text-xs rounded-lg w-full transition-colors hover:bg-(--card-hover) cursor-pointer"
        style={{ color: "var(--muted)" }}
      >
        <Plus className="w-3.5 h-3.5" />
        Add task
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1.5 px-2 py-1">
      <input
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="Task titleâ€¦"
        className="flex-1 text-xs bg-(--input-bg) border border-(--border) rounded-lg px-2.5 py-1.5 outline-none"
        style={{ color: "var(--foreground)" }}
      />
      <button
        onClick={submit}
        className="text-xs px-2.5 py-1.5 rounded-lg transition-colors text-white cursor-pointer"
        style={{ background: "var(--accent)" }}
      >
        Add
      </button>
      <button
        onClick={() => setOpen(false)}
        className="p-1.5 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer"
        style={{ color: "var(--muted)" }}
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// KanbanPanel â€” vertical sectioned list
// ---------------------------------------------------------------------------

export function KanbanPanel({
  taskList,
  onTaskStatusChange,
  onTaskDelete,
  onTaskAdd,
  onDismiss,
}: KanbanPanelProps) {
  if (!taskList || taskList.tasks.length === 0) return null;

  const total = taskList.tasks.length;
  const done  = taskList.tasks.filter((t) => t.status === "done").length;
  const allDone = done === total;

  const pending    = taskList.tasks.filter((t) => t.status === "todo").sort((a, b) => a.order - b.order);
  const inProgress = taskList.tasks.filter((t) => t.status === "in_progress").sort((a, b) => a.order - b.order);
  const completed  = taskList.tasks.filter((t) => t.status === "done").sort((a, b) => a.order - b.order);

  const handleStatus = (listId: string, taskId: string, status: TaskStatus) => {
    onTaskStatusChange?.(listId, taskId, status);
    api.updateTask(listId, taskId, { status });
  };

  const handleDelete = (listId: string, taskId: string) => {
    onTaskDelete?.(listId, taskId);
    api.deleteTask(listId, taskId);
  };

  const handleAdd = (listId: string, title: string) => {
    onTaskAdd?.(listId, title);
    api.addTasks(listId, [title]);
  };

  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const badge = (
    <span
      className="text-[10px] px-2 py-0.5 rounded-full font-medium"
      style={{
        background: allDone ? "color-mix(in srgb, #22c55e 20%, transparent)" : "var(--card)",
        color: allDone ? "#22c55e" : "var(--muted)",
      }}
    >
      {done}/{total}
    </span>
  );

  const progressBar = (
    <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border)", minWidth: 60 }}>
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: allDone ? "#22c55e" : "var(--accent)" }}
      />
    </div>
  );

  return (
    <div className="max-w-3xl mx-auto px-4 pb-3">
      <PanelShell
        icon={<ListTodo className="w-4 h-4" />}
        title="Tasks"
        badge={badge}
        headerRight={progressBar}
        onDismiss={onDismiss}
      >
        {/* â”€â”€ Pending â”€â”€ */}
        <div>
          <SectionHeader label="Pending" count={pending.length} color="var(--muted)" />
          {pending.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} />
          ))}
          <AddTaskForm taskListId={taskList.id} onAdd={handleAdd} />
        </div>

        {/* â”€â”€ In Progress â”€â”€ */}
        {inProgress.length > 0 && (
          <div className="mt-1 pt-2 border-t border-(--border)">
            <SectionHeader label="In Progress" count={inProgress.length} color="#f59e0b" />
            {inProgress.map((t) => (
              <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} />
            ))}
          </div>
        )}

        {/* â”€â”€ Completed â”€â”€ */}
        {completed.length > 0 && (
          <div className="mt-1 pt-2 border-t border-(--border)">
            <SectionHeader label="Completed" count={completed.length} color="#22c55e" />
            {completed.map((t) => (
              <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </PanelShell>
    </div>
  );
}
