"use client";

import { useEffect } from "react";
import { Eye } from "lucide-react";
import { Avatar } from "@/components/groups/Avatar";
import { Messages } from "@/components/groups/Messages";
import { ChatHeader } from "@/components/chats/ChatHeader";
import { Button } from "@/design";
import type { Agent } from "@/lib/api/agents";
import type { GroupEntry } from "@/lib/api/groups";
import type { FeedClient } from "@/lib/realtime";
import { useObservedMessages } from "@/hooks/useObservedMessages";

/** How the feed names a conversation: another agent's pair as `pair-<id>`, a group by its id; the chat with you has no live feed here. */
export function feedId(key: string): string | null {
  if (key.startsWith("pair-")) return key;
  if (key.startsWith("group-")) return key.slice("group-".length);
  return null;
}

/** Who "I" am in a conversation seen from the agent's side: what it said is on the right. */
export function agentAddress(agentId: string, key: string): string {
  if (key.startsWith("group-")) return `member/${agentId}@${key.slice("group-".length)}`;
  if (key.startsWith("pair-")) return `agent/${agentId}`;
  return `agent/${agentId}`;
}

type Props = {
  agent: Agent;
  chatKey: string;
  feed: FeedClient;
  onBack: () => void;
};

/** One of an agent's conversations, read only: the latest page, older ones on request, and what is said as it is said. */
export function ObservedConversation({ agent, chatKey, feed, onBack }: Props) {
  const { entries, chat, hasMore, loading, loadEarlier, add } = useObservedMessages(agent.id, chatKey);
  const live = feedId(chatKey);
  const me = agentAddress(agent.id, chatKey);

  useEffect(() => {
    if (!live) return;
    return feed.subscribe((event) => {
      if (event.type !== "entry" || event.chat !== live) return;
      // The feed speaks to you: what is yours is the agent's here.
      add([{ ...event.entry, from_user: event.entry.sender_id === me } satisfies GroupEntry]);
    });
  }, [feed, live, me, add]);

  const title = !chat ? "" : chat.kind === "group" ? chat.name : `${agent.name} and ${chat.name}`;
  const names = [agent.name, chat?.name ?? ""].filter(Boolean);
  const avatars = { [agent.name]: agent.avatar, ...(chat ? { [chat.name]: chat.avatar } : {}) };
  return (
    <section className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col">
      <ChatHeader
        onBack={onBack}
        title={title || "…"}
        subtitle={`${agent.name}'s view · read only`}
        avatar={
          chat?.kind === "group" ? (
            <Avatar name={chat.name} src={chat.avatar} className="size-10" />
          ) : (
            <div className="flex -space-x-2" aria-hidden>
              <Avatar name={agent.name} src={agent.avatar} className="size-8 text-xs ring-2 ring-background" />
              {chat && <Avatar name={chat.name} src={chat.avatar} className="size-8 text-xs ring-2 ring-background" />}
            </div>
          )
        }
      />
      {hasMore && (
        <div className="flex justify-center border-b border-border bg-background py-1.5">
          <Button variant="ghost" size="sm" disabled={loading} onClick={() => void loadEarlier()}>
            {loading ? "Loading…" : "Load earlier messages"}
          </Button>
        </div>
      )}
      {!loading && entries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted">Nothing has been said here yet.</div>
      ) : (
        <Messages entries={entries} names={names} avatars={avatars} typing={[]} readByAll={-1} onReply={() => undefined} onReact={() => undefined} onEdit={async () => undefined} onDelete={async () => undefined} readOnly />
      )}
      <div className="flex items-center justify-center gap-2 border-t border-border bg-background px-4 py-3 text-sm text-muted">
        <Eye aria-hidden />
        You are reading {agent.name}&rsquo;s chat: there is nothing to write here.
      </div>
    </section>
  );
}
