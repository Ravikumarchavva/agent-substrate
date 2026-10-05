"use client";

import { MessagesSquare, Plus } from "lucide-react";
import { Button, Page, PageEmpty } from "@/design";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { shortTime } from "@/lib/short-time";
import { Avatar } from "./Avatar";
import { GroupChat } from "./GroupChat";
import { NewGroup } from "./NewGroup";

type Props = {
  groups: Group[];
  agents: Agent[];
  /** `null` for the list, `new` for the form, otherwise the group to open. */
  groupId: string | null;
  onOpen: (groupId: string | null) => void;
  onChanged: () => void;
};

/** The Groups page: the list (which on a phone is the way in), the form for a new one, or one group's conversation. */
export function GroupsPanel({ groups, agents, groupId, onOpen, onChanged }: Props) {
  if (groupId === "new") {
    return (
      <NewGroup
        agents={agents}
        onBack={() => onOpen(null)}
        onCreated={(g) => {
          onChanged();
          onOpen(g.id);
        }}
      />
    );
  }

  const open = groupId ? groups.find((g) => g.id === groupId) : undefined;
  if (open) {
    return (
      <GroupChat
        key={open.id}
        group={open}
        agents={agents}
        onBack={() => onOpen(null)}
        onChanged={onChanged}
        onDeleted={() => {
          onChanged();
          onOpen(null);
        }}
      />
    );
  }

  const newButton = (
    <Button variant="primary" onClick={() => onOpen("new")}>
      <Plus /> New group
    </Button>
  );
  return (
    <Page title="Groups" subtitle="You and several of your agents in one conversation." actions={newButton}>
      {groups.length === 0 ? (
        <PageEmpty icon={MessagesSquare} title="No groups yet">
          Put a few agents in a room with you. Each one sees every message and decides whether to reply, like people in a team chat.
        </PageEmpty>
      ) : (
        <ul className="w-full max-w-2xl space-y-1.5">
          {groups.map((g) => (
            <li key={g.id}>
              <Button variant="ghost" onClick={() => onOpen(g.id)} className="h-auto! min-h-0 w-full justify-start gap-3 whitespace-normal rounded-xl border border-transparent px-3 py-2.5 text-left font-normal hover:bg-card-hover">
                <Avatar name={g.name} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{g.name}</span>
                    <span className="shrink-0 text-2xs text-muted">{shortTime(g.updated_at)}</span>
                  </span>
                  <span className="block truncate text-xs text-muted">{g.last_message ? `${g.last_sender}: ${g.last_message}` : g.members.map((m) => m.name).join(", ")}</span>
                </span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}
