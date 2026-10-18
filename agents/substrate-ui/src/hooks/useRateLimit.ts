"use client";

import { useEffect, useState } from "react";
import { fetchRateLimitStatus, type RateLimitStatus } from "@/lib/api/rate_limit";

/** The daily message limit, refreshed every 30 s and again whenever `refreshOn` changes (a message was sent). `null` until known or when no limit applies. */
export function useRateLimit(refreshOn?: unknown): RateLimitStatus | null {
  const [status, setStatus] = useState<RateLimitStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const s = await fetchRateLimitStatus();
      if (!cancelled) setStatus(s && s.enabled ? s : null);
    };
    void load();
    const id = setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [refreshOn]);

  return status;
}
