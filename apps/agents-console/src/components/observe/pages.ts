import type { ViewChat } from "@/lib/api/observe";

/** What the list shows after another page arrived: the new rows after the ones held, a row seen twice kept once (the later copy wins), and still newest first. */
export function mergePage(held: ViewChat[], page: ViewChat[]): ViewChat[] {
  const byKey = new Map<string, ViewChat>();
  for (const row of [...held, ...page]) byKey.set(row.key, row);
  return [...byKey.values()].sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
}

/** A row after something was said in its conversation: moved to the top with the new line. A conversation not held yet is not made up from one entry. */
export function bumped(held: ViewChat[], key: string, preview: string, lastSender: string | null, at: string): ViewChat[] {
  const row = held.find((r) => r.key === key);
  if (!row) return held;
  return mergePage(held, [{ ...row, preview, last_sender: lastSender, at }]);
}
