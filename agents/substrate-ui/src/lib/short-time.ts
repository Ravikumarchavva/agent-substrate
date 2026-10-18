/** A time as a chat list shows it: the clock today ("11:42 AM"), the weekday this week ("Mon"), else the date ("Sep 3"). */
export function shortTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(at)) / 86_400_000);
  if (days <= 0) return at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (days < 7) return at.toLocaleDateString([], { weekday: "short" });
  return at.toLocaleDateString([], { month: "short", day: "numeric" });
}
