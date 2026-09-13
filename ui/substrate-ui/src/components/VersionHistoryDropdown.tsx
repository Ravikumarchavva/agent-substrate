"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { History, Loader2, RotateCcw, Bot, User, FileClock } from "lucide-react";

type VersionEntry = {
  seq: number;
  author: string; // initial | user | agent | restore
  checksum_sha256: string;
  size_bytes: number;
  created_at: number; // epoch seconds
  restored_from_seq?: number | null;
};

function relTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const AUTHOR_META: Record<string, { label: string; Icon: typeof Bot }> = {
  agent: { label: "Assistant", Icon: Bot },
  user: { label: "You", Icon: User },
  initial: { label: "Original", Icon: FileClock },
  restore: { label: "Restored", Icon: RotateCcw },
};

/**
 * Version-history popover for a workspace file. Lists the `FileVersion` lineage
 * (initial / user / agent) from `GET /workspace/versions` and restores a chosen
 * snapshot via `POST /workspace/versions/restore` — non-destructive (the current
 * state is captured first). After a restore it calls `onRestored` so the panel
 * can remount the viewer on the new canonical bytes.
 */
export function VersionHistoryDropdown({
  threadId,
  path,
  onRestored,
}: {
  threadId: string;
  path: string;
  onRestored: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<VersionEntry[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setVersions(null);
    try {
      const r = await fetch(
        `/chat/api/backend/workspace/versions?thread_id=${encodeURIComponent(
          threadId,
        )}&path=${encodeURIComponent(path)}`,
      );
      if (!r.ok) throw new Error(String(r.status));
      const data = (await r.json()) as { versions: VersionEntry[] };
      setVersions(data.versions);
    } catch {
      setVersions([]);
    }
  }, [threadId, path]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const restore = async (seq: number) => {
    setBusy(seq);
    try {
      const r = await fetch(`/chat/api/backend/workspace/versions/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, path, seq }),
      });
      if (!r.ok) throw new Error(String(r.status));
      setOpen(false);
      onRestored();
    } catch {
      // Leave the popover open; the row simply stops spinning.
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
        title="Version history"
      >
        <History className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-50 w-72 overflow-hidden rounded-lg border border-(--border) bg-background shadow-lg">
          <div className="border-b border-(--border) px-3 py-2 text-xs font-semibold text-foreground">
            Version history
          </div>
          <div className="max-h-80 overflow-auto">
            {versions === null ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-4 w-4 animate-spin text-(--muted)" />
              </div>
            ) : versions.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-(--muted)">
                No versions recorded yet.
              </div>
            ) : (
              [...versions].reverse().map((v, i) => {
                const meta = AUTHOR_META[v.author] ?? AUTHOR_META.user;
                const isLatest = i === 0;
                return (
                  <div
                    key={v.seq}
                    className="flex items-center gap-2 border-b border-(--border)/50 px-3 py-2 last:border-0"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-(--card) text-(--muted)">
                      <meta.Icon className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                        {meta.label}
                        {isLatest && (
                          <span className="rounded bg-(--badge-bg) px-1 py-0.5 text-[10px] text-(--muted)">
                            current
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-(--muted)">
                        v{v.seq} · {relTime(v.created_at)}
                        {v.restored_from_seq != null && ` · from v${v.restored_from_seq}`}
                      </div>
                    </div>
                    {!isLatest && (
                      <button
                        onClick={() => restore(v.seq)}
                        disabled={busy !== null}
                        className="flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground disabled:opacity-40"
                        title={`Restore v${v.seq}`}
                      >
                        {busy === v.seq ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <RotateCcw className="h-3 w-3" />
                        )}
                        Restore
                      </button>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
