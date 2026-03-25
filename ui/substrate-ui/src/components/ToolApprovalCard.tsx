"use client";

import { useState } from "react";
import { ShieldAlert } from "lucide-react";
import { PanelShell } from "@/components/PanelShell";

type ToolApprovalCardProps = {
  requestId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  context?: string;
  onRespond: (requestId: string, data: Record<string, unknown>) => void;
};

export function ToolApprovalCard({
  requestId,
  toolName,
  arguments: toolArgs,
  context,
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
    approved: { label: "✅ Approved", cls: "bg-green-600/20 text-green-400" },
    denied: { label: "❌ Denied", cls: "bg-red-600/20 text-red-400" },
    modified: {
      label: "✏️ Modified & Approved",
      cls: "bg-blue-600/20 text-blue-400",
    },
    pending: null,
  }[status];

  const headerBadge = (
    <span
      className="rounded px-2 py-0.5 text-xs font-mono"
      style={{ background: "color-mix(in srgb, #f59e0b 15%, transparent)", color: "#f59e0b" }}
    >
      {toolName}
    </span>
  );

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] w-full text-sm">
        <PanelShell
          icon={<ShieldAlert className="w-4 h-4" style={{ color: "#f59e0b" }} />}
          title="Tool Approval"
          badge={headerBadge}
          collapsible={false}
        >

        {context && (
          <p className="mb-2 text-xs" style={{ color: "var(--muted)" }}>{context}</p>
        )}

        {/* Arguments viewer */}
        <details className="mb-3" open>
          <summary className="cursor-pointer text-xs font-medium" style={{ color: "var(--foreground)" }}>
            Arguments
          </summary>
          {showModify && isPending ? (
            <div className="mt-1">
              <textarea
                value={editedArgs}
                onChange={(e) => {
                  setEditedArgs(e.target.value);
                  setJsonError("");
                }}
                className="w-full rounded p-2 font-mono text-xs outline-none focus:ring-1"
                style={{ background: "var(--code-bg)", color: "var(--foreground)", border: "1px solid var(--border)", resize: "vertical" }}
                rows={Math.min(editedArgs.split("\n").length + 1, 10)}
              />
              {jsonError && (
                <p className="mt-1 text-xs" style={{ color: "#f87171" }}>{jsonError}</p>
              )}
            </div>
          ) : (
            <pre
              className="mt-1 overflow-x-auto rounded p-2 font-mono text-xs"
              style={{ background: "var(--code-bg)", color: "var(--muted)", border: "1px solid var(--border)" }}
            >
              {JSON.stringify(toolArgs, null, 2)}
            </pre>
          )}
        </details>

        {/* Status badge (shown after response) */}
        {statusBadge && (
          <div
            className={`inline-block rounded px-2 py-1 text-xs font-medium ${statusBadge.cls}`}
          >
            {statusBadge.label}
            {reason && (
              <span className="ml-1" style={{ color: "var(--muted)" }}>— {reason}</span>
            )}
          </div>
        )}

        {/* Action buttons (only when pending) */}
        {isPending && (
          <div className="space-y-2 mt-1">
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason (optional)"
              className="w-full rounded px-2 py-1.5 text-xs outline-none"
              style={{
                background: "var(--code-bg)",
                color: "var(--foreground)",
                border: "1px solid var(--border)",
              }}
            />

            <div className="flex gap-2">
              <button
                onClick={handleApprove}
              className="rounded px-3 py-1.5 text-xs font-medium text-white transition-colors cursor-pointer"
                style={{ background: "var(--accent)" }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
              >
                Approve
              </button>
              <button
                onClick={handleDeny}
              className="rounded px-3 py-1.5 text-xs font-medium text-white transition-colors cursor-pointer"
                style={{ background: "#dc2626" }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
              >
                Deny
              </button>
              {showModify ? (
                <button
                  onClick={handleModifySubmit}
                  className="rounded px-3 py-1.5 text-xs font-medium text-white transition-colors cursor-pointer"
                  style={{ background: "#2563eb" }}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
                >
                  Save & Approve
                </button>
              ) : (
                <button
                  onClick={() => setShowModify(true)}
                  className="rounded px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer"
                  style={{
                    background: "var(--card-hover)",
                    color: "var(--foreground)",
                    border: "1px solid var(--border)",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.75")}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
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
