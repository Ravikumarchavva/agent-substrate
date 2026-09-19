"use client";

import { useState, useEffect, useRef } from "react";
import { GitFork, X, Loader2, AlertCircle } from "lucide-react";

interface ForkBranchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onFork: (newBranchName: string) => Promise<void>;
  sourceBranchId: string;
  sourceMessageText?: string | null;
}

export function ForkBranchModal({
  isOpen,
  onClose,
  onFork,
  sourceBranchId,
  sourceMessageText,
}: ForkBranchModalProps) {
  const [branchName, setBranchName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      const defaultName = `branch-${Math.random().toString(36).slice(2, 8)}`;
      setBranchName(defaultName);
      setError(null);
      setLoading(false);
      setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 50);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = branchName.trim();
    if (!clean) {
      setError("Branch name cannot be empty");
      return;
    }
    if (!/^[a-zA-Z0-9_\-]+$/.test(clean)) {
      setError("Branch name can only contain letters, numbers, hyphens, and underscores");
      return;
    }

    try {
      setLoading(true);
      setError(null);
      await onFork(clean);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fork branch");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150"
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) onClose();
      }}
    >
      <div className="relative w-full max-w-md rounded-2xl border border-(--border) bg-(--card) p-6 shadow-2xl animate-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-(--card-hover) border border-(--border) text-foreground">
              <GitFork className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">Fork Branch</h2>
              <p className="text-xs text-(--muted)">
                Create an alternate history path from{" "}
                <span className="font-mono font-medium text-foreground">{sourceBranchId}</span>
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="flex items-center justify-center h-7 w-7 rounded-lg text-(--muted) hover:bg-(--card-hover) hover:text-foreground transition-colors disabled:opacity-50 cursor-pointer"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Message preview if forking from a specific turn */}
        {sourceMessageText && (
          <div className="mt-4 rounded-xl border border-(--border)/60 bg-background/60 p-3">
            <div className="text-[11px] font-medium uppercase tracking-wider text-(--muted) mb-1">
              Forking from turn
            </div>
            <p className="text-xs text-foreground/80 line-clamp-2 italic">
              &ldquo;{sourceMessageText}&rdquo;
            </p>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label htmlFor="branch-name" className="block text-xs font-medium text-foreground mb-1.5">
              New Branch Name
            </label>
            <input
              id="branch-name"
              ref={inputRef}
              type="text"
              value={branchName}
              onChange={(e) => {
                setBranchName(e.target.value);
                if (error) setError(null);
              }}
              disabled={loading}
              placeholder="e.g. alternative-approach"
              className="w-full rounded-xl border border-(--border) bg-background px-3.5 py-2 text-sm text-foreground placeholder:text-(--muted) focus:outline-none focus:ring-2 focus:ring-(--accent)/40 focus:border-(--accent) transition-all font-mono"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-xl bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="rounded-xl px-4 py-2 text-xs font-medium text-(--muted) hover:bg-(--card-hover) hover:text-foreground transition-colors disabled:opacity-50 cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !branchName.trim()}
              className="flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold shadow-sm hover:opacity-90 active:scale-[0.98] transition-all disabled:opacity-50 cursor-pointer"
              style={{ background: "var(--accent)", color: "var(--accent-foreground)" }}
            >
              {loading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Forking...</span>
                </>
              ) : (
                <span>Create Branch</span>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

