"use client";

import { useState } from "react";
import { Pin, Plus, Search } from "lucide-react";
import { Button, Input, Menu, MenuContent, MenuItem, MenuTrigger, Segmented, cn } from "@/design";
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

type Props = {
  items: ChatItem[];
  selectedKey: string | null;
  onSelect: (item: ChatItem) => void;
  onTogglePin: (item: ChatItem) => void;
  onNew: (kind: "agent" | "group") => void;
};

/** Your agents and groups as a chat list: search, filters, the pinned ones on top, and per row who said what last, who is typing and what you have not read. */
export function ChatList({ items, selectedKey, onSelect, onTogglePin, onNew }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ChatFilter>("all");
  const shown = filterChatItems(items, filter, query);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-3 px-4 pb-2 pt-16 sm:pt-5">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Chats</h1>
          <Menu>
            <MenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="New chat">
                <Plus />
              </Button>
            </MenuTrigger>
            <MenuContent align="end">
              <MenuItem onSelect={() => onNew("group")}>New group</MenuItem>
              <MenuItem onSelect={() => onNew("agent")}>New agent</MenuItem>
            </MenuContent>
          </Menu>
        </div>
        <label className="flex h-control-lg items-center gap-2 rounded-full bg-card px-3.5 text-sm focus-within:ring-1 focus-within:ring-accent">
          <Search className="size-4 shrink-0 text-muted" aria-hidden />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search or start a new chat" aria-label="Search chats" className="h-auto border-0 bg-transparent p-0 focus:ring-0" />
        </label>
        <Segmented label="Show" value={filter} onChange={setFilter} options={FILTERS} className="w-full" />
      </div>
      <ul className="scroll-area min-h-0 flex-1 px-2 pb-4">
        {shown.length === 0 && <li className="px-4 py-10 text-center text-sm text-muted">{items.length === 0 ? "No chats yet. Make an agent to begin." : "Nothing matches."}</li>}
        {shown.map((item) => {
          const open = item.key === selectedKey;
          return (
            <li key={item.key} className="group relative">
              <Button
                variant="ghost"
                onClick={() => onSelect(item)}
                aria-current={open ? "page" : undefined}
                className={cn("h-auto! min-h-0 w-full justify-start gap-3 whitespace-normal rounded-xl px-3 py-2.5 text-left font-normal", open ? "bg-accent/12" : "hover:bg-card-hover")}
              >
                <Avatar name={item.name} className="size-12 text-base" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{item.name}</span>
                    <span className={cn("shrink-0 text-2xs", item.unread > 0 ? "font-medium text-accent" : "text-muted")}>{shortTime(item.time)}</span>
                  </span>
                  <span className="mt-0.5 flex items-center justify-between gap-2">
                    {item.typing.length > 0 ? (
                      <span className="truncate text-xs font-medium text-success">{item.kind === "agent" ? "typing…" : typingLine(item.typing)}</span>
                    ) : (
                      <span className="truncate text-xs text-muted">{item.preview}</span>
                    )}
                    <span className="flex shrink-0 items-center gap-1.5">
                      {item.pinned && <Pin className="size-3 text-muted" aria-label="Pinned" />}
                      {item.unread > 0 && !open && <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-2xs font-semibold leading-5 text-accent-foreground">{item.unread}</span>}
                    </span>
                  </span>
                </span>
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={item.pinned ? `Unpin ${item.name}` : `Pin ${item.name}`}
                onClick={() => onTogglePin(item)}
                className="absolute bottom-1.5 right-3 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
              >
                <Pin />
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
