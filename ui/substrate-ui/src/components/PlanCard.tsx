"use client";

import { useState } from "react";
import { TaskList, TaskStatus } from "@/types";
import { api } from "@/lib/api";
import {
  Circle,
  Loader2,
  CheckCircle2,
  XCircle,
  PauseCircle,
  AlertCircle,
  ChevronRight,
  ChevronDown,
  ListTodo,
  RotateCcw,
} from "lucide-react";

// Non-terminal statuses that advance on click.
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

interface PlanCardProps {
  taskList: TaskList;
  runActive: boolean;
  onChange?: (updated: TaskList) => void;
}

/**
 * Inline plan card rendered in the conversation flow. Expanded while the run
 * is active; auto-collapses to a one-line chip once the run completes.
 */
export function PlanCard({ taskList, runActive, onChange }: PlanCardProps) {
  const tasks = [...taskList.tasks].sort((a, b) => a.order - b.order);
  const total = tasks.length;
  const done = tasks.filter((t) => t.status === "succeeded").length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const allDone = total > 0 && done === total;
  const current = tasks.find((t) => t.status === "in_progress");
  // This board is "active" only while the run is live AND it still has
  // unfinished steps — so a new, unrelated run never re-expands a done plan.
  const hasPending = tasks.some((t) => t.status === "planned" || t.status === "in_progress");
  const active = runActive && hasPending;

  // Expanded while active; collapses to a chip otherwise. Adjust during render
  // (not in an effect) when `active` flips, so manual toggles persist until the
  // next run boundary.
  const [open, setOpen] = useState(active);
  const [prevActive, setPrevActive] = useState(active);
  if (prevActive !== active) {
    setPrevActive(active);
    setOpen(active);
  }

  if (total === 0) return null;

  const mutate = (updater: (tl: TaskList) => TaskList) => onChange?.(updater(taskList));

  const setStatus = (taskId: string, status: TaskStatus) => {
    mutate((tl) => ({
      ...tl,
      tasks: tl.tasks.map((t) => (t.id === taskId ? { ...t, status } : t)),
    }));
    void api.updateTask(taskList.id, taskId, { status });
  };

  const retry = (taskId: string) => {
    mutate((tl) => ({
      ...tl,
      tasks: tl.tasks.map((t) =>
        t.id === taskId ? { ...t, status: "in_progress" as TaskStatus, retry_count: 0, note: "" } : t
      ),
    }));
    void api.retryTask(taskList.id, taskId);
  };

  const label =
    taskList.agent_label && taskList.agent_label !== "Agent" ? taskList.agent_label : "Plan";

  const headerText = open
    ? label
    : allDone
    ? `${label} · ${done}/${total} done`
    : current
    ? current.title
    : `${label} · ${done}/${total}`;

  return (
    <div
      className="overflow-hidden rounded-xl border border-(--border)"
      style={{ background: "var(--card)" }}
    >
      {/* Header (always visible) — acts as the collapsed chip too */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-(--card-hover)"
      >
        {open ? (
          <ChevronDown className="w-4 h-4 shrink-0" style={{ color: "var(--muted)" }} />
        ) : (
          <ChevronRight className="w-4 h-4 shrink-0" style={{ color: "var(--muted)" }} />
        )}
        {!open && allDone ? (
          <CheckCircle2 className="w-4 h-4 shrink-0" style={{ color: "#22c55e" }} />
        ) : (
          <ListTodo className="w-4 h-4 shrink-0" style={{ color: "var(--accent)" }} />
        )}
        <span
          className="truncate text-sm font-medium"
          style={{ color: "var(--foreground)" }}
        >
          {headerText}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {!open && current && active && (
            <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: "#f59e0b" }} />
          )}
          <span className="text-[11px]" style={{ color: "var(--muted)" }}>
            {done}/{total}
          </span>
          <span
            className="block h-1.5 w-16 overflow-hidden rounded-full"
            style={{ background: "var(--border)" }}
          >
            <span
              className="block h-full rounded-full transition-all duration-500"
              style={{ width: `${pct}%`, background: allDone ? "#22c55e" : "var(--accent)" }}
            />
          </span>
        </span>
      </button>

      {/* Steps */}
      {open && (
        <div className="border-t border-(--border) px-2 pb-2 pt-1">
          {tasks.map((t) => {
            const next = NEXT[t.status];
            const canRetry = t.status === "failed" || t.status === "abandoned";
            return (
              <div
                key={t.id}
                className="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-(--background)"
                style={{
                  cursor: next ? "pointer" : "default",
                }}
                onClick={() => next && setStatus(t.id, next)}
                title={t.note ? t.note : next ? `Click to mark ${next.replace("_", " ")}` : undefined}
              >
                <span className="shrink-0">{STATUS_ICON[t.status]}</span>
                <span
                  className="flex-1 text-sm leading-snug"
                  style={{ color: STATUS_TEXT_COLOR[t.status] }}
                >
                  {t.title}
                  {t.note && (
                    <span className="ml-1.5 text-[11px]" style={{ color: "var(--muted)" }}>
                      — {t.note}
                    </span>
                  )}
                </span>
                {canRetry && (
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 opacity-0 transition-opacity hover:bg-(--background) group-hover:opacity-100 cursor-pointer"
                    style={{ color: "#ef4444" }}
                    title="Retry"
                    onClick={(e) => {
                      e.stopPropagation();
                      retry(t.id);
                    }}
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Render all plan boards stacked, root boards first then subagent boards. */
export function PlanCardStack({
  boards,
  runActive,
  onChange,
}: {
  boards: TaskList[];
  runActive: boolean;
  onChange?: (updated: TaskList) => void;
}) {
  if (boards.length === 0) return null;
  const roots = boards.filter((b) => !b.parent_agent_id);
  const children = boards.filter((b) => !!b.parent_agent_id);
  return (
    <div className="flex flex-col gap-2">
      {roots.map((tl) => (
        <div key={tl.id} className="flex flex-col gap-2">
          <PlanCard taskList={tl} runActive={runActive} onChange={onChange} />
          {children
            .filter((c) => c.parent_agent_id === tl.agent_id)
            .map((child) => (
              <div key={child.id} className="ml-3 border-l-2 border-(--border) pl-3">
                <PlanCard taskList={child} runActive={runActive} onChange={onChange} />
              </div>
            ))}
        </div>
      ))}
      {children
        .filter((c) => !roots.some((r) => r.agent_id === c.parent_agent_id))
        .map((tl) => (
          <PlanCard key={tl.id} taskList={tl} runActive={runActive} onChange={onChange} />
        ))}
    </div>
  );
}
