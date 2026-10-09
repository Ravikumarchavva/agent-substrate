"use client";

import { MessageSquare, Users } from "lucide-react";
import { Button, cn } from "@/design";
import type { Agent, Exchange } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { counterparts } from "./talk";

type Props = {
  agent: Agent;
  groups: Group[];
  exchanges: Exchange[] | null;
  /** The other agent whose talk with this one is open; none means the conversation with you. */
  withId: string | null;
  onOpenYou: () => void;
  onOpenTalk: (otherId: string) => void;
  onOpenGroup: (groupId: string) => void;
};

/**
 * Whose view you are in: the agent's own conversations. The one with you, each other agent it has talked to (which you may read but not join)
 * and the groups it is in. Nothing is shown for an agent that has only ever talked to you.
 */
export function AgentConversations({ agent, groups, exchanges, withId, onOpenYou, onOpenTalk, onOpenGroup }: Props) {
  const others = counterparts(agent.id, exchanges ?? []);
  const mine = groups.filter((g) => g.members.some((m) => m.agent_id === agent.id));
  if (others.length === 0 && mine.length === 0) return null;
  const chip = (active: boolean) => cn("shrink-0 rounded-full px-3 font-medium", active ? "bg-accent/15 text-accent hover:bg-accent/20" : "bg-card text-muted hover:bg-card-hover");
  return (
    <nav aria-label={`${agent.name}'s conversations`} className="flex items-center gap-2 overflow-x-auto border-b border-border bg-background px-4 py-2">
      <span className="shrink-0 text-2xs font-semibold uppercase tracking-wider text-muted">{agent.name}&rsquo;s chats</span>
      <Button variant="ghost" size="sm" aria-pressed={withId === null} onClick={onOpenYou} className={chip(withId === null)}>
        <MessageSquare /> You
      </Button>
      {others.map((o) => (
        <Button key={o.id} variant="ghost" size="sm" aria-pressed={withId === o.id} onClick={() => onOpenTalk(o.id)} className={chip(withId === o.id)}>
          {o.name}
          <span className="tabular-nums text-muted">{o.count}</span>
        </Button>
      ))}
      {mine.map((g) => (
        <Button key={g.id} variant="ghost" size="sm" onClick={() => onOpenGroup(g.id)} className={chip(false)}>
          <Users /> {g.name}
        </Button>
      ))}
    </nav>
  );
}
