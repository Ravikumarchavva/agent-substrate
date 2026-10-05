import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { chatKey, type ChatKind } from "@/lib/pins";
import { previewOf } from "@/components/groups/text";

/** One row of the chat list: an agent or a group, reduced to what a row shows. */
export type ChatItem = {
  key: string;
  kind: ChatKind;
  id: string;
  name: string;
  /** The last thing said (with who said it, in a group), or the role when nothing has been. */
  preview: string;
  /** Who is typing right now, as a line ("Scout is typing…"), or empty. */
  typing: string[];
  time: string | null;
  unread: number;
  pinned: boolean;
};

export type ChatFilter = "all" | "unread" | "groups" | "agents";

export function buildChatItems(agents: Agent[], groups: Group[], pinned: readonly string[]): ChatItem[] {
  const items: ChatItem[] = [
    ...groups.map(
      (g): ChatItem => ({
        key: chatKey("group", g.id),
        kind: "group",
        id: g.id,
        name: g.name,
        preview: g.last_message ? `${g.last_sender ?? "Group"}: ${previewOf(g.last_message, [])}` : g.members.map((m) => m.name).join(", "),
        typing: g.working,
        time: g.updated_at,
        unread: g.unread,
        pinned: pinned.includes(chatKey("group", g.id)),
      }),
    ),
    ...agents.map(
      (a): ChatItem => ({
        key: chatKey("agent", a.id),
        kind: "agent",
        id: a.id,
        name: a.name,
        preview: a.last_message ?? (a.role || "No role set"),
        typing: a.working ? [a.name] : [],
        time: a.last_active,
        unread: 0,
        pinned: pinned.includes(chatKey("agent", a.id)),
      }),
    ),
  ];
  // Pinned first, then the most recently active.
  return items.sort((x, y) => Number(y.pinned) - Number(x.pinned) || (y.time ? Date.parse(y.time) : 0) - (x.time ? Date.parse(x.time) : 0));
}

export function filterChatItems(items: ChatItem[], filter: ChatFilter, query: string): ChatItem[] {
  const q = query.trim().toLowerCase();
  return items.filter((i) => {
    if (filter === "unread" && i.unread === 0) return false;
    if (filter === "groups" && i.kind !== "group") return false;
    if (filter === "agents" && i.kind !== "agent") return false;
    return !q || i.name.toLowerCase().includes(q) || i.preview.toLowerCase().includes(q);
  });
}

/** Everything unread, for the badge on the Agents entry in the sidebar. */
export const totalUnread = (groups: Group[]) => groups.reduce((n, g) => n + g.unread, 0);
