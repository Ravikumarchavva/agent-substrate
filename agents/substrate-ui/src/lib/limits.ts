/** Shared wording for a daily limit: "2h 5m", "per day". */
export function formatResetIn(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 60) return `${seconds}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

/** How full a limit is, as a tone for its bar: green, then amber from 75%, red when reached. */
export function limitTone(used: number, limit: number): "success" | "warning" | "danger" {
  if (limit <= 0 || used >= limit) return "danger";
  return used >= limit * 0.75 ? "warning" : "success";
}
