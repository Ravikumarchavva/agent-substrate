"use client";

import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/design";
import { api } from "@/lib/api";
import { fetchRateLimitStatus, type RateLimitStatus } from "@/lib/api/rate_limit";
import type { Usage } from "@/lib/api/usage";
import { reportError } from "@/lib/report-error";

const money = (usd: number) => (usd === 0 ? "$0" : usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`);
const count = (n: number) => new Intl.NumberFormat("en-US", { notation: n >= 100000 ? "compact" : "standard" }).format(n);

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-border px-4 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** What you have used: today against your daily limit, this month, and the last 30 days day by day. */
export function UsageTab() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [limit, setLimit] = useState<RateLimitStatus | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    api.getUsage(30).then(setUsage).catch((err) => reportError("Couldn't load your usage", err));
    void fetchRateLimitStatus().then(setLimit);
  }, []);

  const busiest = Math.max(1, ...(usage?.days.map((d) => d.messages) ?? [1]));

  const download = async () => {
    setDownloading(true);
    try {
      await api.downloadMyData();
    } catch (err) {
      reportError("Couldn't export your data", err);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Usage</h2>
        <p className="mt-1 text-sm text-muted">{usage?.note ?? "What you have used, so a limit is never a surprise."}</p>
      </div>

      {limit?.enabled && (
        <div className="rounded-xl border border-border px-4 py-3">
          <div className="flex items-baseline justify-between">
            <p className="text-sm font-medium text-foreground">Today&apos;s messages</p>
            <p className="text-sm tabular-nums text-muted">
              {limit.used} of {limit.limit}
            </p>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-border" role="progressbar" aria-valuenow={limit.used} aria-valuemin={0} aria-valuemax={limit.limit} aria-label="Messages used today">
            <div className={`h-full rounded-full ${limit.used >= limit.limit ? "bg-danger" : limit.used >= limit.limit * 0.75 ? "bg-warning" : "bg-success"}`} style={{ width: `${Math.min(100, (limit.used / Math.max(1, limit.limit)) * 100)}%` }} />
          </div>
          <p className="mt-2 text-xs text-muted">
            {limit.used >= limit.limit ? "You've reached today's limit. " : `${limit.limit - limit.used} left. `}
            It resets in {Math.floor(limit.reset_in / 3600)}h {Math.floor((limit.reset_in % 3600) / 60)}m.
          </p>
        </div>
      )}

      {usage === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Today" value={`${usage.today.messages} messages`} hint={`${count(usage.today.tokens)} tokens · ${money(usage.today.cost_usd)}`} />
            <Stat label="This month" value={`${usage.this_month.messages} messages`} hint={`${count(usage.this_month.tokens)} tokens · ${money(usage.this_month.cost_usd)}`} />
            <Stat label="Last 30 days" value={`${usage.window.messages} messages`} hint={`${count(usage.window.tokens)} tokens · ${money(usage.window.cost_usd)}`} />
          </div>

          <figure>
            <figcaption className="mb-2 text-sm font-medium text-foreground">Messages per day</figcaption>
            <div className="flex h-32 items-end gap-1" role="img" aria-label={`Messages per day over the last ${usage.days.length} days`}>
              {usage.days.map((d) => (
                <div key={d.date} className="flex h-full flex-1 flex-col justify-end" title={`${d.date}: ${d.messages} messages · ${money(d.cost_usd)}`}>
                  <div className="w-full rounded-t-sm bg-accent" style={{ height: `${d.messages === 0 ? 2 : Math.max(6, (d.messages / busiest) * 100)}%`, opacity: d.messages === 0 ? 0.25 : 1 }} />
                </div>
              ))}
            </div>
            <div className="mt-1 flex justify-between text-2xs text-muted">
              <span>{usage.days[0]?.date}</span>
              <span>{usage.days[usage.days.length - 1]?.date}</span>
            </div>
          </figure>
        </>
      )}

      <div className="rounded-xl border border-border px-4 py-4">
        <h3 className="text-sm font-semibold text-foreground">Your data</h3>
        <p className="mt-1 text-sm text-muted">Download your conversations, memories, preferences and scheduled tasks as one file. Files are in Storage.</p>
        <Button className="mt-3" variant="secondary" size="lg" disabled={downloading} onClick={() => void download()}>
          <Download /> {downloading ? "Preparing…" : "Download my data"}
        </Button>
      </div>
    </div>
  );
}
