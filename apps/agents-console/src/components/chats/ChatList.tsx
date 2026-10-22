"use client";

import { useEffect, useRef, useState } from "react";
import { Pin, Plus, Search } from "lucide-react";
import { Button, Input, Menu, MenuContent, MenuItem, MenuTrigger, cn } from "@/design";
import { Avatar } from "@/components/groups/Avatar";
import { typingLine } from "@/components/groups/text";
import { shortTime } from "@/lib/short-time";
import { filterChatItems, type ChatFilter, type ChatItem } from "./items";

const FILTERS: { value: ChatFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "unread", label: "Unread" },
  { value: "groups", label: "Groups" },
  { value: "agents", label: "Agents" },
];

/** A list the server searches, filters and pages (an agent's chat list, which can be long): the list shows what it is given and asks for more. */
export type ServerList = {
  query: string;
  onQuery: (query: string) => void;
  filter: ChatFilter;
  onFilter: (filter: ChatFilter) => void;
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
  /** Words for the empty list: it has none yet, or nothing matches. */
  empty: string;
};

type Props = {
  items: ChatItem[];
  selectedKey: string | null;
  onSelect: (item: ChatItem) => void;
  /** Without these the list is read only: no pinning and no new chat. */
  onTogglePin?: (item: ChatItem) => void;
  onNew?: (kind: "agent" | "group") => void;
  title?: string;
  server?: ServerList;
};

/** Your agents and groups as a chat list: search, filters, the pinned ones on top, and per row who said what last, who is typing and what you have not read. */
export function ChatList({ items, selectedKey, onSelect, onTogglePin, onNew, title = "Chats", server }: Props) {
  const [ownQuery, setOwnQuery] = useState("");
  const [ownFilter, setOwnFilter] = useState<ChatFilter>("all");
  const query = server ? server.query : ownQuery;
  const filter = server ? server.filter : ownFilter;
  const setQuery = server ? server.onQuery : setOwnQuery;
  const setFilter = server ? server.onFilter : setOwnFilter;
  const shown = server ? items : filterChatItems(items, filter, query);
  const filters = server ? FILTERS.filter((f) => f.value !== "unread") : FILTERS;
  // Reaching the end of what is loaded asks for the next page.
  const end = useRef<HTMLLIElement>(null);
  const more = server?.hasMore && !server.loading ? server.onLoadMore : null;
  useEffect(() => {
    const node = end.current;
    if (!node || !more) return;
    const watcher = new IntersectionObserver((seen) => seen.some((s) => s.isIntersecting) && more(), { rootMargin: "200px" });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [more, shown.length]);

  const hasPinned = shown.some((i) => i.pinned);

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="space-y-3 px-4 pb-3 pt-16 sm:pt-5">
        <div className="flex items-center justify-between">
          <h1 className="truncate text-2xl font-bold tracking-tight text-foreground">{title}</h1>
          {onNew && (
            <Menu>
              <MenuTrigger asChild>
                <Button variant="primary" size="icon" aria-label="New chat" className="rounded-full">
                  <Plus />
                </Button>
              </MenuTrigger>
              <MenuContent align="end">
                <MenuItem onSelect={() => onNew("group")}>New group</MenuItem>
                <MenuItem onSelect={() => onNew("agent")}>New agent</MenuItem>
              </MenuContent>
            </Menu>
          )}
        </div>
        <label className="flex h-control-lg items-center gap-2 rounded-full bg-card px-4 text-sm focus-within:ring-1 focus-within:ring-accent">
          <Search className="shrink-0 text-muted" aria-hidden />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={onNew ? "Search or start a new chat" : "Search chats"} aria-label="Search chats" className="h-auto border-0 bg-transparent p-0 focus:ring-0" />
        </label>
        <div role="group" aria-label="Show" className="flex gap-2 overflow-x-auto">
          {filters.map((f) => (
            <Button
              key={f.value}
              variant="ghost"
              size="sm"
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={cn("shrink-0 rounded-full px-3.5 font-medium", filter === f.value ? "bg-accent/15 text-accent hover:bg-accent/20" : "bg-card text-muted hover:bg-card-hover")}
            >
              {f.label}
            </Button>
          ))}
        </div>
      </div>
      <ul className="scroll-area min-h-0 flex-1 px-2 pb-4">
        {shown.length === 0 && (
          <li className="px-4 py-10 text-center text-sm text-muted">
            {server?.loading ? "Loading…" : server && !query.trim() && filter === "all" ? server.empty : items.length === 0 && !server ? "No chats yet. Make an agent to begin." : "Nothing matches."}
          </li>
        )}
        {shown.map((item, index) => {
          const open = item.key === selectedKey;
          const unread = item.unread > 0 && !open;
          const typing = item.typing.length > 0;
          const label = hasPinned && (index === 0 && item.pinned ? "Pinned" : !item.pinned && shown[index - 1]?.pinned ? "All chats" : null);
          return (
            <li key={item.key} className="group relative">
              {label && <p className="px-3 pb-1 pt-3 text-2xs font-semibold uppercase tracking-wider text-muted">{label}</p>}
              <Button
                variant="ghost"
                onClick={() => onSelect(item)}
                aria-current={open ? "page" : undefined}
                className={cn("h-auto! min-h-0 w-full justify-start gap-3 whitespace-normal rounded-lg px-3 py-3 text-left font-normal", open ? "bg-accent/12" : "hover:bg-card-hover")}
              >
                <span className="relative shrink-0">
                  <Avatar name={item.name} src={item.avatar} className="size-12 text-lg" />
                  {typing && <span className="absolute bottom-0 right-0 size-3 animate-pulse rounded-full border-2 border-background bg-success" aria-hidden />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className={cn("truncate text-base text-foreground", unread ? "font-bold" : "font-semibold")}>{item.name}</span>
                    <span className={cn("shrink-0 text-2xs", unread ? "font-semibold text-accent" : "text-muted")}>{shortTime(item.time)}</span>
                  </span>
                  <span className="mt-1 flex items-center justify-between gap-2">
                    {typing ? (
                      <span className="truncate text-sm font-medium text-success">{item.kind === "agent" ? "typing…" : typingLine(item.typing)}</span>
                    ) : (
                      <span className={cn("truncate text-sm", unread ? "font-medium text-foreground" : "text-muted")}>{item.preview}</span>
                    )}
                    <span className="flex shrink-0 items-center gap-1.5 group-focus-within:invisible group-hover:invisible">
                      {item.pinned && <Pin className="text-muted" aria-label="Pinned" />}
                      {unread && <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-2xs font-semibold leading-5 text-accent-foreground">{item.unread}</span>}
                    </span>
                  </span>
                </span>
              </Button>
              {onTogglePin && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={item.pinned ? `Unpin ${item.name}` : `Pin ${item.name}`}
                  onClick={() => onTogglePin(item)}
                  className="absolute bottom-2 right-3 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                >
                  <Pin />
                </Button>
              )}
            </li>
          );
        })}
        {server?.hasMore && (
          <li ref={end} className="p-2">
            <Button variant="ghost" className="w-full" disabled={server.loading} onClick={server.onLoadMore}>
              {server.loading ? "Loading…" : "Load more"}
            </Button>
          </li>
        )}
      </ul>
    </div>
  );
}
