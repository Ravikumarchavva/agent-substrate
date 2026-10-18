import { API_BASE } from "./_client";

export interface RateLimitStatus {
  enabled: boolean;
  used: number;
  limit: number;
  window_seconds: number;
  reset_in: number;
}

export async function fetchRateLimitStatus(): Promise<RateLimitStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/rate-limit/status`, { credentials: "include" });
    if (!res.ok) return null;
    return (await res.json()) as RateLimitStatus;
  } catch {
    return null;
  }
}
