"use client";

import { useEffect, useMemo, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { cn, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent, ToolInfo } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { chatKey, usePinnedChats, type ChatKind } from "@/lib/pins";
import { reportError } from "@/lib/report-error";
import { GroupChat } from "@/components/groups/GroupChat";
import { NewGroup } from "@/components/groups/NewGroup";
import { AgentDetail } from "./AgentDetail";
import { ChatList } from "./ChatList";
import { buildChatItems } from "./items";

export const NEW = "new";
export type ChatSelection = { kind: ChatKind; id: string | null } | null;

type Props = {
  agents: Agent[];
  groups: Group[];
  /** What the address says is open: an agent or a group, `new` for the form, or nothing. */
  selection: ChatSelection;
  onSelect: (kind: ChatKind, id: string | null) => void;
  /** Agents or groups changed (made, edited, deleted, read): lists elsewhere should refresh. */
  onChanged: () => void;
  /** Talk to an agent one to one, in its own conversation. */
  onMessageAgent: (agentId: string) => void;
};

/**
 * The messenger: every agent and group as a chat list on the left, and whatever you opened on the right: a group's conversation, or an agent as a contact.
 * On a phone it is one or the other. Pinned chats are the ones the sidebar keeps; the rest are found here.
 */
export function ChatsPanel({ agents, groups, selection, onSelect, onChanged, onMessageAgent }: Props) {
  const { pinned, toggle, isFull } = usePinnedChats();
  const [tools, setTools] = useState<ToolInfo[]>([]);
  useEffect(() => {
    api.getAgentTools().then(setTools).catch((err) => reportError("Couldn't load the tool list", err));
  }, []);

  const items = useMemo(() => buildChatItems(agents, groups, pinned), [agents, groups, pinned]);
  const selectedKey = selection?.id && selection.id !== NEW ? chatKey(selection.kind, selection.id) : null;
  const agent = selection?.kind === "agent" && selection.id ? (agents.find((a) => a.id === selection.id) ?? null) : null;
  const group = selection?.kind === "group" && selection.id ? (groups.find((g) => g.id === selection.id) ?? null) : null;
  const detailOpen = selection !== null && selection.id !== null;

  const pin = (key: string, name: string) => {
    if (toggle(key) === false) toast.error(`You can pin up to 5 chats. Unpin one to pin ${name}.`);
  };

  const removeAgent = async (a: Agent) => {
    if (!(await confirmAction({ title: `Delete ${a.name}?`, description: "Its files are deleted too. Conversations you had with it stay, as ordinary ones.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteAgent(a.id);
      onChanged();
      onSelect("agent", null);
    } catch (err) {
      reportError("Couldn't delete the agent", err);
    }
  };

  return (
    <div className="@container h-full w-full bg-background text-foreground">
      <div className="grid h-full min-h-0 @4xl:grid-cols-[minmax(20rem,26rem)_minmax(0,1fr)]">
        <div className={cn("min-h-0 @4xl:border-r @4xl:border-border", detailOpen && "hidden @4xl:block")}>
          <ChatList
            items={items}
            selectedKey={selectedKey}
            onSelect={(i) => onSelect(i.kind, i.id)}
            onTogglePin={(i) => pin(i.key, i.name)}
            onNew={(kind) => onSelect(kind, NEW)}
          />
        </div>
        <div className={cn("min-h-0", !detailOpen && "hidden @4xl:block")}>
          {selection?.kind === "group" && selection.id === NEW ? (
            <NewGroup
              agents={agents}
              onBack={() => onSelect("group", null)}
              onCreated={(g) => {
                onChanged();
                onSelect("group", g.id);
              }}
            />
          ) : group ? (
            <GroupChat
              key={group.id}
              group={group}
              agents={agents}
              onBack={() => onSelect("group", null)}
              onChanged={onChanged}
              onDeleted={() => {
                onChanged();
                onSelect("group", null);
              }}
            />
          ) : selection?.kind === "agent" && (selection.id === NEW || agent) ? (
            <AgentDetail
              key={agent?.id ?? NEW}
              agent={agent}
              others={agents.filter((a) => a.id !== agent?.id)}
              groups={agent ? groups.filter((g) => g.members.some((m) => m.agent_id === agent.id)) : []}
              pinned={!!agent && pinned.includes(chatKey("agent", agent.id)) }
              onTogglePin={() => agent && (isFull && !pinned.includes(chatKey("agent", agent.id)) ? toast.error("You can pin up to 5 chats. Unpin one first.") : pin(chatKey("agent", agent.id), agent.name))}
              onOpenGroup={(id) => onSelect("group", id)}
              tools={tools}
              onBack={() => onSelect("agent", null)}
              onSaved={(saved) => {
                onChanged();
                onSelect("agent", saved.id);
              }}
              onChat={() => agent && onMessageAgent(agent.id)}
              onDelete={() => agent && void removeAgent(agent)}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="flex size-16 items-center justify-center rounded-2xl bg-badge text-muted">
                <MessagesSquare className="icon-free size-8" aria-hidden />
              </div>
              <p className="text-base font-semibold text-foreground">Your agents and groups</p>
              <p className="max-w-sm text-sm text-muted">Pick a chat to open it. Pin the ones you use most and they stay in the sidebar.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
