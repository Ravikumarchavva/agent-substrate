"use client";

import { useEffect, useState } from "react";
import { fetchDocQuotaStatus, type DocQuotaStatus } from "@/lib/api/doc_quota";

function formatResetIn(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 60) return `${seconds}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

export function DocQuotaBar() {
  const [status, setStatus] = useState<DocQuotaStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const s = await fetchDocQuotaStatus();
      if (!cancelled) setStatus(s);
    }

    void load();
    // Poll every 30s — same cadence as RateLimitBar, so both stay in sync
    // after the user sends a message that commits a document.
    const id = setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!status || !status.enabled) return null;

  const { used, limit, reset_in } = status;
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const remaining = Math.max(0, limit - used);
  const exhausted = used >= limit;

  const barColour = pct >= 100 ? "bg-red-500" : pct >= 75 ? "bg-amber-400" : "bg-emerald-500";

  return (
    <div className="mx-2 mb-2 rounded-2xl border border-(--border) bg-(--card) px-3 py-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-(--muted)">
          Document limit
        </span>
        <span className="text-[11px] text-(--muted)">
          Reset in:&nbsp;
          <span className="font-medium text-foreground">{formatResetIn(reset_in)}</span>
        </span>
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-(--border)">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColour}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-1.5 flex items-center justify-between">
        <span className={`text-[11px] ${exhausted ? "text-red-400" : "text-(--muted)"}`}>
          {exhausted ? "Limit reached" : `${remaining} of ${limit} documents left`}
        </span>
        <span className="text-[11px] text-(--muted)">per day</span>
      </div>
    </div>
  );
}
