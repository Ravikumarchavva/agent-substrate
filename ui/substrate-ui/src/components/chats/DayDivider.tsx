import { dayLabel } from "@/components/groups/text";

/** The pill between two days of a conversation: "Today", "Yesterday", "Monday". */
export function DayDivider({ at }: { at: string }) {
  return <p className="mx-auto my-3 w-fit rounded-lg bg-card px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted shadow-sm">{dayLabel(at)}</p>;
}
