"use client";

import { useEffect, useState } from "react";
import { fetchRateLimitStatus, type RateLimitStatus } from "@/lib/api/rate_limit";

function formatResetIn(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 60) return `${seconds}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

function formatWindowLabel(seconds: number): string {
  if (seconds <= 60) return "per minute";
  if (seconds <= 3600) return "per hour";
  return "per day";
}

export function RateLimitBar() {
  const [status, setStatus] = useState<RateLimitStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const s = await fetchRateLimitStatus();
      if (!cancelled) setStatus(s);
    }

    void load();
    // Poll every 30 s so the bar stays accurate after the user sends messages
    const id = setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Don't render when disabled or data not yet loaded
  if (!status || !status.enabled) return null;

  const { used, limit, reset_in, window_seconds } = status;
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const remaining = Math.max(0, limit - used);
  const exhausted = used >= limit;

  // Colour: green → amber → red as usage rises
  const barColour =
    pct >= 100
      ? "bg-red-500"
      : pct >= 75
        ? "bg-amber-400"
        : "bg-emerald-500";

  return (
    <div className="mx-2 mb-2 rounded-2xl border border-(--border) bg-(--card) px-3 py-2.5">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-(--muted)">
          Daily limit
        </span>
        <span className="text-[11px] text-(--muted)">
          Reset in:&nbsp;
          <span className="font-medium text-foreground">{formatResetIn(reset_in)}</span>
        </span>
      </div>

      {/* Progress bar */}
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-(--border)">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColour}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Footer row */}
      <div className="mt-1.5 flex items-center justify-between">
        <span className={`text-[11px] ${exhausted ? "text-red-400" : "text-(--muted)"}`}>
          {exhausted
            ? "Limit reached"
            : `${remaining} of ${limit} messages left`}
        </span>
        <span className="text-[11px] text-(--muted)">{formatWindowLabel(window_seconds)}</span>
      </div>
    </div>
  );
}
