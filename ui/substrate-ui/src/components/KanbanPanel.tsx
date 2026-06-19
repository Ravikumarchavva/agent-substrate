"use client";

import { useRef, useEffect, useState } from "react";
import { Task, TaskList, TaskStatus } from "@/types";
import { api } from "@/lib/api";
import {
  Circle,
  Loader2,
  CheckCircle2,
  XCircle,
  PauseCircle,
  AlertCircle,
  Plus,
  X,
  ListTodo,
  RotateCcw,
} from "lucide-react";
import { PanelShell } from "@/components/PanelShell";

interface KanbanPanelProps {
  taskList: TaskList;
  onTaskListChange?: (updated: TaskList) => void;
  onDismiss?: () => void;
}

// Non-terminal statuses that can be advanced by clicking
const NEXT: Partial<Record<TaskStatus, TaskStatus>> = {
  planned: "in_progress",
  in_progress: "succeeded",
  blocked: "in_progress",
};

const STATUS_ICON: Record<TaskStatus, React.ReactNode> = {
  planned: <Circle className="w-4 h-4" style={{ color: "var(--muted)" }} />,
  in_progress: <Loader2 className="w-4 h-4 animate-spin" style={{ color: "#f59e0b" }} />,
  blocked: <PauseCircle className="w-4 h-4" style={{ color: "#fb923c" }} />,
  succeeded: <CheckCircle2 className="w-4 h-4" style={{ color: "#22c55e" }} />,
  failed: <XCircle className="w-4 h-4" style={{ color: "#ef4444" }} />,
  abandoned: <AlertCircle className="w-4 h-4" style={{ color: "var(--muted)" }} />,
};

const STATUS_TEXT_COLOR: Record<TaskStatus, string> = {
  planned: "var(--foreground)",
  in_progress: "var(--foreground)",
  blocked: "#fb923c",
  succeeded: "var(--muted)",
  failed: "#f87171",
  abandoned: "var(--muted)",
};

function TaskRow({
  task,
  taskListId,
  onStatusChange,
  onDelete,
  onRetry,
}: {
  task: Task;
  taskListId: string;
  onStatusChange: (listId: string, taskId: string, status: TaskStatus) => void;
  onDelete: (listId: string, taskId: string) => void;
  onRetry: (listId: string, taskId: string) => void;
}) {
  const nextStatus = NEXT[task.status];
  const isTerminal = !nextStatus;
  const canRetry = task.status === "failed" || task.status === "abandoned";
  const isAbandoned = task.status === "abandoned";

  return (
    <div
      className="group flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors"
      style={{
        cursor: nextStatus ? "pointer" : "default",
        textDecoration: task.status === "succeeded" || task.status === "abandoned" ? "line-through" : "none",
      }}
      onClick={() => nextStatus && onStatusChange(taskListId, task.id, nextStatus)}
      title={
        task.note
          ? task.note
          : nextStatus
          ? `Click to mark ${nextStatus.replace("_", " ")}`
          : undefined
      }
    >
      <span className="shrink-0">{STATUS_ICON[task.status]}</span>

      <span
        className="flex-1 text-sm leading-snug"
        style={{ color: STATUS_TEXT_COLOR[task.status] }}
      >
        {task.title}
        {task.note && (
          <span className="ml-1.5 text-[11px]" style={{ color: "var(--muted)" }}>
            — {task.note}
          </span>
        )}
      </span>

      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {canRetry && (
          <button
            className="p-0.5 rounded transition-colors hover:bg-(--card) cursor-pointer"
            style={{ color: isAbandoned ? "#f59e0b" : "#ef4444" }}
            title={isAbandoned ? "Force retry (resets counter)" : "Retry"}
            onClick={(e) => {
              e.stopPropagation();
              if (isAbandoned && !confirm("This task was abandoned. Force retry will reset the retry counter. Continue?")) return;
              onRetry(taskListId, task.id);
            }}
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        )}
        {!isTerminal && (
          <button
            className="p-0.5 rounded transition-colors hover:bg-(--card) cursor-pointer"
            style={{ color: "var(--muted)" }}
            onClick={(e) => {
              e.stopPropagation();
              onDelete(taskListId, task.id);
            }}
            aria-label="Delete task"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

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
        placeholder="Task title…"
        className="flex-1 text-xs bg-(--input-bg) border border-(--border) rounded-lg px-2.5 py-1.5 outline-none"
        style={{ color: "var(--foreground)" }}
      />
      <button
        onClick={submit}
        className="text-xs px-2.5 py-1.5 rounded-lg transition-colors text-(--accent-foreground) cursor-pointer"
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

export function KanbanPanel({ taskList, onTaskListChange, onDismiss }: KanbanPanelProps) {
  if (!taskList || taskList.tasks.length === 0) return null;

  const total = taskList.tasks.length;
  const doneCount = taskList.tasks.filter((t) => t.status === "succeeded").length;
  const allDone = doneCount === total;
  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  const byStatus = (s: TaskStatus) =>
    taskList.tasks.filter((t) => t.status === s).sort((a, b) => a.order - b.order);

  const planned = byStatus("planned");
  const inProgress = byStatus("in_progress");
  const blocked = byStatus("blocked");
  const succeeded = byStatus("succeeded");
  const failed = byStatus("failed");
  const abandoned = byStatus("abandoned");

  const mutate = (updater: (tl: TaskList) => TaskList) => {
    onTaskListChange?.(updater(taskList));
  };

  const handleStatus = (listId: string, taskId: string, status: TaskStatus) => {
    mutate((tl) => ({
      ...tl,
      tasks: tl.tasks.map((t) => (t.id === taskId ? { ...t, status } : t)),
    }));
    void api.updateTask(listId, taskId, { status });
  };

  const handleDelete = (listId: string, taskId: string) => {
    mutate((tl) => ({ ...tl, tasks: tl.tasks.filter((t) => t.id !== taskId) }));
    void api.deleteTask(listId, taskId);
  };

  const handleAdd = (listId: string, title: string) => {
    void api.addTasks(listId, [title]);
  };

  const handleRetry = (listId: string, taskId: string) => {
    mutate((tl) => ({
      ...tl,
      tasks: tl.tasks.map((t) =>
        t.id === taskId ? { ...t, status: "in_progress" as TaskStatus, retry_count: 0, note: "" } : t
      ),
    }));
    void api.retryTask(listId, taskId);
  };

  const badge = (
    <span
      className="text-[10px] px-2 py-0.5 rounded-full font-medium"
      style={{
        background: allDone ? "color-mix(in srgb, #22c55e 20%, transparent)" : "var(--card)",
        color: allDone ? "#22c55e" : "var(--muted)",
      }}
    >
      {doneCount}/{total}
    </span>
  );

  const progressBar = (
    <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border)", minWidth: 48 }}>
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: allDone ? "#22c55e" : "var(--accent)" }}
      />
    </div>
  );

  const agentLabel = taskList.agent_label || taskList.agent_id || "Agent";

  return (
    <PanelShell
      icon={<ListTodo className="w-4 h-4" />}
      title={agentLabel === "Agent" ? "Tasks" : agentLabel}
      badge={badge}
      headerRight={progressBar}
      onDismiss={onDismiss}
    >
      {planned.length > 0 && (
        <div>
          <SectionHeader label="Planned" count={planned.length} color="var(--muted)" />
          {planned.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
          <AddTaskForm taskListId={taskList.id} onAdd={handleAdd} />
        </div>
      )}

      {inProgress.length > 0 && (
        <div className={planned.length > 0 ? "mt-1 pt-2 border-t border-(--border)" : ""}>
          <SectionHeader label="In Progress" count={inProgress.length} color="#f59e0b" />
          {inProgress.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
          {planned.length === 0 && <AddTaskForm taskListId={taskList.id} onAdd={handleAdd} />}
        </div>
      )}

      {blocked.length > 0 && (
        <div className="mt-1 pt-2 border-t border-(--border)">
          <SectionHeader label="Blocked" count={blocked.length} color="#fb923c" />
          {blocked.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
        </div>
      )}

      {failed.length > 0 && (
        <div className="mt-1 pt-2 border-t border-(--border)">
          <SectionHeader label="Failed" count={failed.length} color="#ef4444" />
          {failed.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
        </div>
      )}

      {abandoned.length > 0 && (
        <div className="mt-1 pt-2 border-t border-(--border)">
          <SectionHeader label="Abandoned" count={abandoned.length} color="var(--muted)" />
          {abandoned.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
        </div>
      )}

      {succeeded.length > 0 && (
        <div className="mt-1 pt-2 border-t border-(--border)">
          <SectionHeader label="Done" count={succeeded.length} color="#22c55e" />
          {succeeded.map((t) => (
            <TaskRow key={t.id} task={t} taskListId={taskList.id} onStatusChange={handleStatus} onDelete={handleDelete} onRetry={handleRetry} />
          ))}
        </div>
      )}
    </PanelShell>
  );
}
