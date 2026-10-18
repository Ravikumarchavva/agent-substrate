"use client";

import { useEffect, useMemo, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { ChatList } from "@/components/chats/ChatList";
import type { ChatItem } from "@/components/chats/items";
import { previewOf } from "@/components/groups/text";
import { cn } from "@/design";
import { useObservedChats } from "@/hooks/useObservedChats";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { ViewChat, ViewFilter } from "@/lib/api/observe";
import { FeedClient } from "@/lib/realtime";
import { ObservedConversation } from "./ObservedConversation";
import { ViewBanner } from "./ViewBanner";

type Props = {
  agent: Agent;
  /** The open conversation (`you`, `pair-<id>`, `group-<id>`), or none. */
  chatKey: string | null;
  onOpen: (key: string | null) => void;
  onSwitch: () => void;
  onExit: () => void;
};

const toItem = (c: ViewChat): ChatItem => ({
  key: c.key,
  kind: c.kind === "group" ? "group" : "agent",
  id: c.id,
  name: c.name,
  avatar: c.avatar,
  preview: c.last_sender && c.kind !== "you" ? `${c.last_sender}: ${c.preview}` : c.preview,
  typing: [],
  time: c.at,
  unread: 0,
  pinned: false,
});

/**
 * An agent's account, as read-only as it gets: its chat list on the left, searched and paged by the server (a hundred contacts is a page), one
 * of its conversations on the right, and a banner across the top saying whose it is. Nothing in it can be written to, pinned or marked read.
 */
export function ObservedAccount({ agent, chatKey, onOpen, onSwitch, onExit }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ViewFilter>("all");
  const list = useObservedChats(agent.id, query, filter);
  const items = useMemo(() => list.items.map(toItem), [list.items]);

  // One stream for this account: what is said in its conversations arrives as it is said, and moves them up the list.
  const feed = useMemo(() => new FeedClient((since, signal) => api.openGroupFeed(since, signal, agent.id)), [agent.id]);
  const { bump } = list;
  useEffect(() => {
    feed.start();
    const off = feed.subscribe((event) => {
      if (event.type !== "entry" || (event.entry.kind !== "message" && event.entry.kind !== "system")) return;
      const key = event.chat.startsWith("pair-") ? event.chat : `group-${event.chat}`;
      bump(key, previewOf(event.entry.text, event.entry.attachments).slice(0, 140), event.entry.sender, event.entry.at);
    });
    return () => {
      off();
      feed.stop();
    };
  }, [feed, bump]);

  // Back to the list when the filter or search changes what is open.
  const open = chatKey !== null;
  return (
    <div className="@container flex h-full min-h-0 w-full flex-col bg-background text-foreground">
      <ViewBanner name={agent.name} onSwitch={onSwitch} onExit={onExit} />
      <div className="grid min-h-0 flex-1 @4xl:grid-cols-[minmax(20rem,26rem)_minmax(0,1fr)]">
        <div className={cn("min-h-0 @4xl:border-r @4xl:border-border", open && "hidden @4xl:block")}>
          <ChatList
            title={`${agent.name}'s chats`}
            items={items}
            selectedKey={chatKey}
            onSelect={(item) => onOpen(item.key)}
            server={{
              query,
              onQuery: setQuery,
              filter,
              onFilter: (f) => setFilter(f === "unread" ? "all" : f),
              hasMore: list.hasMore,
              loading: list.loading,
              onLoadMore: list.loadMore,
              empty: `${agent.name} has no conversations yet.`,
            }}
          />
        </div>
        <div className={cn("min-h-0", !open && "hidden @4xl:block")}>
          {chatKey ? (
            <ObservedConversation key={`${agent.id}/${chatKey}`} agent={agent} chatKey={chatKey} feed={feed} onBack={() => onOpen(null)} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="flex size-16 items-center justify-center rounded-2xl bg-badge text-muted">
                <MessagesSquare className="icon-free size-8" aria-hidden />
              </div>
              <p className="text-base font-semibold text-foreground">{agent.name}&rsquo;s conversations</p>
              <p className="max-w-sm text-sm text-muted">Pick one to read it. You see what {agent.name} sees, and cannot write in it.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
