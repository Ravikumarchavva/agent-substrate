import type { Exchange } from "@/lib/api/agents";
import type { GroupEntry } from "@/lib/api/groups";

/** An agent it has talked to with another agent, and how much. */
export interface Counterpart {
  id: string;
  name: string;
  /** How many times they have asked each other. */
  count: number;
  /** When they last did. */
  last: string;
}

const between = (x: Exchange, a: string, b: string) => (x.asker_id === a && x.target_id === b) || (x.asker_id === b && x.target_id === a);

/** The other agents `agentId` has asked or been asked by, the most recent first. A plain chat asking it is you, not another agent, so it is not one. */
export function counterparts(agentId: string, exchanges: Exchange[]): Counterpart[] {
  const found = new Map<string, Counterpart>();
  for (const x of exchanges) {
    if (!x.asker_id) continue;
    const mine = x.asker_id === agentId;
    const id = mine ? x.target_id : x.asker_id;
    if (id === agentId) continue;
    const name = mine ? x.target : x.asker;
    const seen = found.get(id);
    found.set(id, { id, name, count: (seen?.count ?? 0) + 1, last: !seen || x.at > seen.last ? x.at : seen.last });
  }
  return [...found.values()].sort((a, b) => b.last.localeCompare(a.last));
}

const NOT_DONE: Record<Exchange["status"], string> = {
  done: "(it answered with nothing)",
  working: "Working on it…",
  waiting: "Stopped to wait for you to answer something.",
  failed: "Could not do that.",
};

/**
 * What `agentId` and `otherId` said to each other, oldest first, as messages in a conversation: each time one asked, the request and then the answer.
 * Whatever `agentId` said is "mine" (on the right), because this is its view of its own conversations.
 */
export function talkEntries(agentId: string, otherId: string, exchanges: Exchange[]): GroupEntry[] {
  const entries: GroupEntry[] = [];
  const asked = exchanges.filter((x) => between(x, agentId, otherId)).sort((a, b) => a.at.localeCompare(b.at));
  for (const x of asked) {
    const side = (id: string | null, name: string, text: string, tag: string): GroupEntry => ({
      seq: entries.length,
      id: `${x.thread_id}:${tag}`,
      sender_id: id ?? "plain",
      sender: name,
      from_user: id === agentId,
      kind: "message",
      text,
      mentions: [],
      reply_to: null,
      attachments: [],
      at: x.at,
    });
    entries.push(side(x.asker_id, x.asker, x.request, "ask"));
    entries.push(side(x.target_id, x.target, x.answer ?? NOT_DONE[x.status], "answer"));
  }
  return entries;
}
