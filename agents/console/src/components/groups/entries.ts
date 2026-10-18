import type { Group, GroupEntry, Reaction } from "@/lib/api/groups";
import { previewOf } from "./text";

/** The kinds that are shown. The others say what happened to an entry and are folded into it. */
export const isShown = (e: GroupEntry) => e.kind === "message" || e.kind === "system";

/** Apply one marker (an edit, a reaction, a delete) to the entry it is about. An unknown target is left alone. */
function applyMarker(all: GroupEntry[], marker: GroupEntry): GroupEntry[] {
  if (marker.reply_to === null) return all;
  return all.map((e) => {
    if (e.seq !== marker.reply_to) return e;
    if (marker.kind === "edit") return { ...e, text: marker.text, edited_at: marker.at };
    if (marker.kind === "tombstone") return { ...e, text: "", attachments: [], reactions: [], deleted_at: marker.at };
    if (marker.kind === "reaction") {
      const others = (e.reactions ?? []).filter((r) => r.sender_id !== marker.sender_id);
      const mine: Reaction[] = marker.text ? [{ emoji: marker.text, sender_id: marker.sender_id, sender: marker.sender, from_user: marker.from_user }] : [];
      return { ...e, reactions: [...others, ...mine] };
    }
    return e;
  });
}

/**
 * What the conversation shows after `incoming` has arrived: new messages added in order, and edits, reactions and deletes applied to the
 * entry they are about. Safe to apply the same thing twice (a reply to your send and the next poll both carry your message), and in any
 * mix of history and live entries: each marker's effect is the same whenever it is applied after the entry it is about.
 */
export function foldEntries(current: GroupEntry[], incoming: GroupEntry[]): GroupEntry[] {
  let all = current;
  const sorted = [...incoming].sort((a, b) => a.seq - b.seq);
  for (const e of sorted) {
    if (isShown(e)) {
      if (!all.some((x) => x.seq === e.seq)) all = [...all, e].sort((a, b) => a.seq - b.seq);
    } else {
      all = applyMarker(all, e);
    }
  }
  return all;
}

/** The group's row in a list after `entry` was said in it: the latest thing, who said it, and one more unread unless you wrote it, are in the conversation, or it is only a note. */
export function groupAfterEntry(group: Group, entry: GroupEntry, open: boolean): Group {
  if (!isShown(entry)) return group;
  const unread = entry.kind === "message" && !entry.from_user && !open ? group.unread + 1 : group.unread;
  return {
    ...group,
    last_message: previewOf(entry.text, entry.attachments).slice(0, 140),
    last_sender: entry.from_user ? "You" : entry.sender,
    updated_at: entry.at,
    unread,
    paused: entry.kind === "system",
  };
}
