"use client";

/**
 * PanelShell — shared base wrapper for all auxiliary panels:
 *   KanbanPanel, ToolApprovalCard (inline), HumanInputCard (inline), AppPanel
 *
 * Provides:
 *  - Consistent rounded card style (matches Chainlit step-card aesthetic)
 *  - Icon + title header row with optional right-side actions slot
 *  - Collapsible content area
 *  - Dismiss button
 */

import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";

interface PanelShellProps {
  /** Icon element next to the title */
  icon?: ReactNode;
  /** Panel title text */
  title: string;
  /** Badge/pill next to the title (e.g. progress "3/5") */
  badge?: ReactNode;
  /** Extra controls placed on the far right of the header */
  headerRight?: ReactNode;
  /** Whether the panel can be collapsed. Defaults true. */
  collapsible?: boolean;
  /** Whether the panel starts collapsed. Defaults false. */
  defaultCollapsed?: boolean;
  /** Called when the × button is clicked. Omit to hide the button. */
  onDismiss?: () => void;
  /** Main content */
  children: ReactNode;
  /** Extra className on the outer wrapper */
  className?: string;
}

export function PanelShell({
  icon,
  title,
  badge,
  headerRight,
  collapsible = true,
  defaultCollapsed = false,
  onDismiss,
  children,
  className = "",
}: PanelShellProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  return (
    <div
      className={`rounded-xl border border-(--border) bg-(--step-bg) overflow-hidden ${className}`}
      style={{ background: "var(--step-bg)" }}
    >
      {/* ── Header ── */}
      <div
        className="flex items-center gap-2 px-3 py-2.5 border-b border-(--border)"
        style={{ background: "var(--card)" }}
      >
        {icon && (
          <span className="shrink-0" style={{ color: "var(--muted)" }}>
            {icon}
          </span>
        )}

        <span className="text-xs font-semibold tracking-wide" style={{ color: "var(--foreground)" }}>
          {title}
        </span>

        {badge && <span className="ml-0.5">{badge}</span>}

        {/* Push right-side actions to the end */}
        <div className="flex-1" />

        {headerRight && <div className="flex items-center gap-1">{headerRight}</div>}

        {collapsible && (
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="p-1 rounded hover:bg-(--card-hover) transition-colors"
            style={{ color: "var(--muted)" }}
            aria-label={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
          </button>
        )}

        {onDismiss && (
          <button
            onClick={onDismiss}
            className="p-1 rounded hover:bg-(--card-hover) transition-colors"
            style={{ color: "var(--muted)" }}
            aria-label="Dismiss"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* ── Body ── */}
      {!collapsed && <div className="p-3">{children}</div>}
    </div>
  );
}
