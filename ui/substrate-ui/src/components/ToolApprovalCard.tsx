"use client";

import { useState } from "react";

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
      setJsonError("Invalid JSON ΓÇö please fix syntax errors");
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

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-lg border border-amber-600/40 bg-zinc-900 p-4 text-sm">
        {/* Header */}
        <div className="mb-2 flex items-center gap-2">
          <span className="rounded bg-amber-600/20 px-2 py-0.5 text-xs font-semibold text-amber-400">
            🔐 Tool Approval
          </span>
          <span className="font-mono text-xs text-zinc-400">{toolName}</span>
        </div>

        {context && (
          <p className="mb-2 text-xs text-zinc-400">{context}</p>
        )}

        {/* Arguments viewer */}
        <details className="mb-3" open>
          <summary className="cursor-pointer text-xs font-medium text-zinc-300">
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
                className="w-full rounded bg-zinc-800 p-2 font-mono text-xs text-zinc-200 outline-none focus:ring-1 focus:ring-amber-500"
                rows={Math.min(editedArgs.split("\n").length + 1, 10)}
              />
              {jsonError && (
                <p className="mt-1 text-xs text-red-400">{jsonError}</p>
              )}
            </div>
          ) : (
            <pre className="mt-1 overflow-x-auto rounded bg-zinc-800 p-2 font-mono text-xs text-zinc-300">
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
              <span className="ml-1 text-zinc-400">ΓÇö {reason}</span>
            )}
          </div>
        )}

        {/* Action buttons (only when pending) */}
        {isPending && (
          <div className="space-y-2">
            {/* Reason input (shared for deny/modify) */}
            {(showModify || true) && (
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (optional)"
                className="w-full rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 outline-none"
              />
            )}

            <div className="flex gap-2">
              <button
                onClick={handleApprove}
                className="rounded bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-500 transition-colors"
              >
                ✅ Approve
              </button>
              <button
                onClick={handleDeny}
                className="rounded bg-red-600/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-500 transition-colors"
              >
                ❌ Deny
              </button>
              {showModify ? (
                <button
                  onClick={handleModifySubmit}
                  className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 transition-colors"
                >
                  💾 Save & Approve
                </button>
              ) : (
                <button
                  onClick={() => setShowModify(true)}
                  className="rounded bg-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-600 transition-colors"
                >
                  ✏️ Modify
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
