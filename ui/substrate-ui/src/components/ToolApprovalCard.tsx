"use client";

import { useState } from "react";
import { ShieldAlert, ShieldCheck, ChevronRight } from "lucide-react";
import { PanelShell } from "@/components/PanelShell";

type ToolApprovalCardProps = {
  requestId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  context?: string;
  /** Per-call risk tier from the engine ("safe" | "high" | "critical"). */
  risk?: string;
  /** Plain-language summary of what the code does (for code_interpreter). */
  summary?: string;
  onRespond: (requestId: string, data: Record<string, unknown>) => void;
};

// Risk tier → accent colour, mirroring the tool-call dots in MessageBubble.
const RISK_STYLE: Record<string, { color: string; label: string }> = {
  safe: { color: "#34d399", label: "Low risk" },
  high: { color: "#f59e0b", label: "Elevated risk" },
  critical: { color: "#ef4444", label: "High risk" },
};

export function ToolApprovalCard({
  requestId,
  toolName,
  arguments: toolArgs,
  context,
  risk,
  summary,
  onRespond,
}: ToolApprovalCardProps) {
  const [status, setStatus] = useState<
    "pending" | "approved" | "denied" | "modified"
  >("pending");
  const [showModify, setShowModify] = useState(false);
  const [editedArgs, setEditedArgs] = useState(
    JSON.stringify(toolArgs, null, 2)
  );
  const [reason, setReason] = useState("");
  const [jsonError, setJsonError] = useState("");

  const isPending = status === "pending";
  const riskStyle = RISK_STYLE[risk ?? ""] ?? RISK_STYLE.critical;

  // code_interpreter (and similar) carry a `code` string — show it as a code
  // block rather than dumping the whole args object as JSON.
  const codeArg =
    typeof toolArgs.code === "string" ? (toolArgs.code as string) : null;

  function handleApprove() {
    setStatus("approved");
    onRespond(requestId, { action: "approve" });
  }

  function handleDeny() {
    setStatus("denied");
    onRespond(requestId, { action: "deny", reason: reason || undefined });
  }

  function handleModifySubmit() {
    try {
      const parsed = JSON.parse(editedArgs);
      setJsonError("");
      setStatus("modified");
      onRespond(requestId, {
        action: "modify",
        modified_arguments: parsed,
        reason: reason || undefined,
      });
    } catch {
      setJsonError("Invalid JSON — please fix syntax errors");
    }
  }

  const statusBadge = {
    approved: { label: "Approved", cls: "bg-green-600/20 text-green-400" },
    denied: { label: "Denied", cls: "bg-red-600/20 text-red-400" },
    modified: {
      label: "Modified & approved",
      cls: "bg-blue-600/20 text-blue-400",
    },
    pending: null,
  }[status];

  const headerBadge = (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{
        background: `color-mix(in srgb, ${riskStyle.color} 15%, transparent)`,
        color: riskStyle.color,
      }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: riskStyle.color }}
      />
      {riskStyle.label}
    </span>
  );

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-full text-sm sm:max-w-[85%]">
        <PanelShell
          icon={
            isPending ? (
              <ShieldAlert className="h-4 w-4" style={{ color: riskStyle.color }} />
            ) : (
              <ShieldCheck className="h-4 w-4" style={{ color: "var(--muted)" }} />
            )
          }
          title="Approval needed"
          badge={headerBadge}
          collapsible={false}
        >
          {/* One-line what-and-why. */}
          <div className="mb-3 flex items-baseline gap-1.5">
            <span className="text-[13px] font-medium text-foreground">
              {toolName.replace(/_/g, " ")}
            </span>
            {summary && (
              <span className="text-[13px] text-(--muted)">— {summary}</span>
            )}
          </div>

          {context && (
            <p className="mb-2 text-xs" style={{ color: "var(--muted)" }}>
              {context}
            </p>
          )}

          {/* Code / arguments viewer */}
          {showModify && isPending ? (
            <div className="mb-3">
              <textarea
                value={editedArgs}
                onChange={(e) => {
                  setEditedArgs(e.target.value);
                  setJsonError("");
                }}
                className="w-full rounded-xl p-2.5 font-mono text-xs outline-none focus:ring-1"
                style={{
                  background: "var(--code-bg)",
                  color: "var(--code-fg)",
                  border: "1px solid var(--border)",
                  resize: "vertical",
                }}
                rows={Math.min(editedArgs.split("\n").length + 1, 14)}
              />
              {jsonError && (
                <p className="mt-1 text-xs" style={{ color: "#f87171" }}>
                  {jsonError}
                </p>
              )}
            </div>
          ) : (
            <details className="group mb-3" open>
              <summary
                className="mb-1 inline-flex cursor-pointer select-none list-none items-center gap-1 text-[11px] font-semibold uppercase tracking-wider"
                style={{ color: "var(--muted)" }}
              >
                <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
                {codeArg ? "Code to run" : "Arguments"}
              </summary>
              <pre
                className="max-h-72 overflow-auto rounded-xl p-3 font-mono text-[11px] leading-relaxed"
                style={{
                  background: "var(--code-bg)",
                  color: "var(--code-fg)",
                  border: "1px solid var(--border)",
                }}
              >
                {codeArg ?? JSON.stringify(toolArgs, null, 2)}
              </pre>
            </details>
          )}

          {/* Status badge (shown after response) */}
          {statusBadge && (
            <div
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium ${statusBadge.cls}`}
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              {statusBadge.label}
              {reason && (
                <span className="ml-1" style={{ color: "var(--muted)" }}>
                  — {reason}
                </span>
              )}
            </div>
          )}

          {/* Action buttons (only when pending) */}
          {isPending && (
            <div className="mt-1 space-y-2">
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (optional)"
                className="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none"
                style={{
                  background: "var(--code-bg)",
                  color: "var(--foreground)",
                  border: "1px solid var(--border)",
                }}
              />

              <div className="flex flex-wrap gap-2">
                <button
                  onClick={handleApprove}
                  className="cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-85"
                  style={{
                    background: "var(--accent)",
                    color: "var(--accent-foreground)",
                  }}
                >
                  Approve
                </button>
                <button
                  onClick={handleDeny}
                  className="cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-85"
                  style={{ background: "#dc2626" }}
                >
                  Deny
                </button>
                {showModify ? (
                  <button
                    onClick={handleModifySubmit}
                    className="cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-85"
                    style={{ background: "#2563eb" }}
                  >
                    Save &amp; approve
                  </button>
                ) : (
                  <button
                    onClick={() => setShowModify(true)}
                    className="cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-75"
                    style={{
                      background: "var(--card-hover)",
                      color: "var(--foreground)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    Modify
                  </button>
                )}
              </div>
            </div>
          )}
        </PanelShell>
      </div>
    </div>
  );
}
