"use client";

import { useMemo } from "react";
import { Eye } from "lucide-react";
import { Avatar } from "@/components/groups/Avatar";
import { Messages } from "@/components/groups/Messages";
import type { Agent, Exchange } from "@/lib/api/agents";
import { ChatHeader } from "./ChatHeader";
import { talkEntries } from "./talk";

type Props = {
  agent: Agent;
  other: Agent | null;
  exchanges: Exchange[] | null;
  onBack: () => void;
  /** The switcher between this agent's conversations, drawn between the header and the messages. */
  switcher: React.ReactNode;
};

const none = async () => undefined;

/** What one agent and another said to each other, as a conversation from the first one's side. You may read it, not write in it. */
export function AgentTalk({ agent, other, exchanges, onBack, switcher }: Props) {
  const entries = useMemo(() => (other && exchanges ? talkEntries(agent.id, other.id, exchanges) : []), [agent.id, other, exchanges]);
  const names = [agent.name, other?.name ?? ""].filter(Boolean);
  const avatars = { [agent.name]: agent.avatar, ...(other ? { [other.name]: other.avatar } : {}) };
  return (
    <>
      <ChatHeader
        onBack={onBack}
        title={`${agent.name} and ${other?.name ?? "…"}`}
        subtitle="What they said to each other"
        avatar={
          <div className="flex -space-x-2" aria-hidden>
            <Avatar name={agent.name} src={agent.avatar} className="size-8 text-xs ring-2 ring-background" />
            {other && <Avatar name={other.name} src={other.avatar} className="size-8 text-xs ring-2 ring-background" />}
          </div>
        }
      />
      {switcher}
      {exchanges !== null && entries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted">They have not talked to each other yet.</div>
      ) : (
        <Messages entries={entries} names={names} avatars={avatars} typing={[]} readByAll={-1} onReply={() => undefined} onReact={() => undefined} onEdit={none} onDelete={none} readOnly />
      )}
      <div className="flex items-center justify-center gap-2 border-t border-border bg-background px-4 py-3 text-sm text-muted">
        <Eye aria-hidden />
        You are reading along: this is between {agent.name} and {other?.name ?? "another agent"}, so there is nothing to write here.
      </div>
    </>
  );
}
