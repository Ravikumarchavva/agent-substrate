"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { MessagesSquare } from "lucide-react";
import { cn, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent, ToolInfo } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { chatKey, MAX_PINS, setPinned, type ChatKind } from "@/lib/pins";
import { reportError } from "@/lib/report-error";
import { GroupChat } from "@/components/groups/GroupChat";
import { NewGroup } from "@/components/groups/NewGroup";
import { AgentDetail } from "./AgentDetail";
import { ChatList } from "./ChatList";
import { buildChatItems } from "./items";

export const NEW = "new";
/** What the address names: an agent (its conversation, or its profile with `tab: "info"`) or a group. */
export type ChatSelection = { kind: ChatKind; id: string | null; tab?: "info" | null } | null;

type Props = {
  agents: Agent[];
  groups: Group[];
  /** What the address says is open: an agent or a group, `new` for the form, or nothing. */
  selection: ChatSelection;
  onSelect: (kind: ChatKind, id: string | null, tab?: "info") => void;
  /** Open an agent's account, read only. */
  onViewAs: (agentId: string) => void;
  /** Agents or groups changed (made, edited, deleted, read): lists elsewhere should refresh. */
  onChanged: () => void;
  /** The open agent's conversation, drawn by the page (it owns the streaming state), shown where its profile would be. */
  conversation: ReactNode;
};

/**
 * The messenger: every agent and group as a chat list on the left, and whatever you opened on the right: a group's conversation, or an agent as a contact.
 * On a phone it is one or the other. Pinned chats are the ones the sidebar keeps; the rest are found here.
 */
export function ChatsPanel({ agents, groups, selection, onSelect, onViewAs, onChanged, conversation }: Props) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  useEffect(() => {
    api.getAgentTools().then(setTools).catch((err) => reportError("Couldn't load the tool list", err));
  }, []);

  const items = useMemo(() => buildChatItems(agents, groups), [agents, groups]);
  const selectedKey = selection?.id && selection.id !== NEW ? chatKey(selection.kind, selection.id) : null;
  const agent = selection?.kind === "agent" && selection.id ? (agents.find((a) => a.id === selection.id) ?? null) : null;
  const group = selection?.kind === "group" && selection.id ? (groups.find((g) => g.id === selection.id) ?? null) : null;
  const detailOpen = selection !== null && selection.id !== null;

  const pin = async (kind: ChatKind, id: string, pinned: boolean, name: string) => {
    try {
      if (!(await setPinned(kind, id, pinned))) toast.error(`You can pin up to ${MAX_PINS} chats. Unpin one to pin ${name}.`);
      onChanged();
    } catch (err) {
      reportError("Couldn't change the pin", err);
    }
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

  /** The agent's contact card beside its conversation, or the page for a new one. */
  const profile = () => (
    <AgentDetail
      key={agent?.id ?? NEW}
      agent={agent}
      others={agents.filter((a) => a.id !== agent?.id)}
      groups={agent ? groups.filter((g) => g.members.some((m) => m.agent_id === agent.id)) : []}
      pinned={!!agent?.pinned_at}
      onTogglePin={() => agent && void pin("agent", agent.id, !agent.pinned_at, agent.name)}
      onViewAs={() => agent && onViewAs(agent.id)}
      onOpenGroup={(id) => onSelect("group", id)}
      tools={tools}
      onBack={() => onSelect("agent", agent ? agent.id : null)}
      onSaved={(saved) => {
        onChanged();
        onSelect("agent", saved.id, agent ? "info" : undefined);
      }}
      onPictureChanged={onChanged}
      onDelete={() => agent && void removeAgent(agent)}
    />
  );

  return (
    <div className="@container h-full w-full bg-background text-foreground">
      <div className="grid h-full min-h-0 @4xl:grid-cols-[minmax(20rem,26rem)_minmax(0,1fr)]">
        <div className={cn("min-h-0 @4xl:border-r @4xl:border-border", detailOpen && "hidden @4xl:block")}>
          <ChatList
            items={items}
            selectedKey={selectedKey}
            onSelect={(i) => onSelect(i.kind, i.id)}
            onTogglePin={(i) => void pin(i.kind, i.id, !i.pinned, i.name)}
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
          ) : agent ? (
            <div className="@container flex h-full min-h-0 w-full">
              <div className={cn("relative flex min-w-0 flex-1 flex-col", selection?.tab === "info" && "hidden @3xl:flex")}>{conversation}</div>
              {selection?.tab === "info" && (
                <aside className="h-full min-h-0 w-full border-l border-border @3xl:w-96 @3xl:shrink-0">{profile()}</aside>
              )}
            </div>
          ) : selection?.kind === "agent" && selection.id === NEW ? (
            profile()
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
