"use client";

import { useState } from "react";
import { TaskList } from "@/types";
import { KanbanPanel } from "@/components/KanbanPanel";
import { ListTodo, X } from "lucide-react";

interface TaskBoardsProps {
  boards: Map<string, TaskList>;
  onBoardChange?: (updated: TaskList) => void;
}

function BoardStack({ boards, onBoardChange }: TaskBoardsProps) {
  // Sort: root board (no parent) first, then subagent boards
  const allBoards = Array.from(boards.values());
  const rootBoards = allBoards.filter((b) => !b.parent_agent_id);
  const childBoards = allBoards.filter((b) => !!b.parent_agent_id);

  if (allBoards.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {rootBoards.map((tl) => (
        <div key={tl.id}>
          <KanbanPanel taskList={tl} onTaskListChange={onBoardChange} />
          {/* Subagent boards that belong to this root */}
          {childBoards
            .filter((c) => c.parent_agent_id === tl.agent_id)
            .map((child) => (
              <div key={child.id} className="ml-3 mt-2 pl-3 border-l-2 border-(--border)">
                <KanbanPanel taskList={child} onTaskListChange={onBoardChange} />
              </div>
            ))}
        </div>
      ))}
      {/* Orphan subagent boards whose parent isn't in the map */}
      {childBoards
        .filter((c) => !rootBoards.some((r) => r.agent_id === c.parent_agent_id))
        .map((tl) => (
          <KanbanPanel key={tl.id} taskList={tl} onTaskListChange={onBoardChange} />
        ))}
    </div>
  );
}

// Desktop dock — rendered as a right-rail sibling to the AppPanel
export function TaskBoardsDock({ boards, onBoardChange }: TaskBoardsProps) {
  const [collapsed, setCollapsed] = useState(false);

  const total = Array.from(boards.values()).reduce((s, b) => s + b.tasks.length, 0);
  const done = Array.from(boards.values()).reduce(
    (s, b) => s + b.tasks.filter((t) => t.status === "succeeded").length,
    0
  );

  if (boards.size === 0) return null;

  return (
    <div
      className="hidden lg:flex flex-col shrink-0 border-l border-(--border) overflow-y-auto"
      style={{
        width: collapsed ? "44px" : "260px",
        minWidth: collapsed ? "44px" : "260px",
        transition: "width 0.2s ease, min-width 0.2s ease",
        background: "var(--background)",
      }}
    >
      {/* Dock header */}
      <div
        className="flex items-center gap-2 px-3 py-2.5 border-b border-(--border) sticky top-0"
        style={{ background: "var(--background)" }}
      >
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="p-1 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer"
          style={{ color: "var(--muted)" }}
          aria-label={collapsed ? "Expand tasks" : "Collapse tasks"}
        >
          <ListTodo className="w-4 h-4" />
        </button>
        {!collapsed && (
          <>
            <span className="text-xs font-medium" style={{ color: "var(--muted)" }}>
              Tasks
            </span>
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-full ml-auto"
              style={{ background: "var(--card)", color: "var(--muted)" }}
            >
              {done}/{total}
            </span>
          </>
        )}
      </div>

      {!collapsed && (
        <div className="p-2">
          <BoardStack boards={boards} onBoardChange={onBoardChange} />
        </div>
      )}
    </div>
  );
}

// Mobile floating pill + bottom sheet
export function TaskBoardsMobile({ boards, onBoardChange }: TaskBoardsProps) {
  const [open, setOpen] = useState(false);

  const total = Array.from(boards.values()).reduce((s, b) => s + b.tasks.length, 0);
  const done = Array.from(boards.values()).reduce(
    (s, b) => s + b.tasks.filter((t) => t.status === "succeeded").length,
    0
  );

  if (boards.size === 0) return null;

  return (
    <>
      {/* Floating pill */}
      <button
        onClick={() => setOpen(true)}
        className="lg:hidden fixed bottom-24 right-4 z-30 flex items-center gap-1.5 px-3 py-2 rounded-full shadow-lg border border-(--border) cursor-pointer transition-colors hover:bg-(--card-hover)"
        style={{ background: "var(--background)", color: "var(--foreground)" }}
      >
        <ListTodo className="w-4 h-4" style={{ color: "var(--accent)" }} />
        <span className="text-xs font-medium">
          Tasks {done}/{total}
        </span>
      </button>

      {/* Bottom sheet */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-50 flex flex-col justify-end">
          {/* Backdrop */}
          <button
            type="button"
            className="absolute inset-0 cursor-pointer"
            style={{ background: "rgba(0,0,0,0.5)" }}
            onClick={() => setOpen(false)}
            aria-label="Close"
          />
          {/* Sheet */}
          <div
            className="relative rounded-t-2xl border-t border-(--border) overflow-y-auto"
            style={{
              background: "var(--background)",
              maxHeight: "75vh",
            }}
          >
            <div
              className="flex items-center justify-between px-4 py-3 border-b border-(--border) sticky top-0"
              style={{ background: "var(--background)" }}
            >
              <div className="flex items-center gap-2">
                <ListTodo className="w-4 h-4" style={{ color: "var(--accent)" }} />
                <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                  Tasks
                </span>
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full"
                  style={{ background: "var(--card)", color: "var(--muted)" }}
                >
                  {done}/{total}
                </span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-1.5 rounded-lg hover:bg-(--card-hover) transition-colors cursor-pointer"
                style={{ color: "var(--muted)" }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-3">
              <BoardStack boards={boards} onBoardChange={onBoardChange} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
