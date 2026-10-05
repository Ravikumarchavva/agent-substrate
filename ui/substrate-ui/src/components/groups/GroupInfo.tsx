"use client";

import { useState } from "react";
import { Trash2, X } from "lucide-react";
import { Badge, Button, Input, Section, Select, confirmAction } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, MemberMode } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { Avatar } from "./Avatar";

const MODES = [
  { value: "all", label: "Every message" },
  { value: "mentions", label: "When addressed" },
  { value: "muted", label: "Only when named" },
];

type Props = {
  group: Group;
  /** Every agent the user has, to offer the ones not yet in the group. */
  agents: Agent[];
  onChanged: (group?: Group) => void;
  onDeleted: () => void;
};

/** Who is in the group and how closely each follows it; rename it, add or remove agents, or delete it. */
export function GroupInfo({ group, agents, onChanged, onDeleted }: Props) {
  const [name, setName] = useState(group.name);
  const outside = agents.filter((a) => !group.members.some((m) => m.agent_id === a.id));

  const run = async (action: () => Promise<Group | void>, failure: string) => {
    try {
      onChanged((await action()) ?? undefined);
    } catch (err) {
      reportError(failure, err);
    }
  };

  const remove = async () => {
    if (!(await confirmAction({ title: `Delete ${group.name}?`, description: "The conversation is deleted for everyone in it. The agents and their files stay.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteGroup(group.id);
      onDeleted();
    } catch (err) {
      reportError("Couldn't delete the group", err);
    }
  };

  return (
    <div className="space-y-6">
      <Section title="Name">
        <div className="flex gap-2">
          <Input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Group name" />
          <Button disabled={!name.trim() || name.trim() === group.name} onClick={() => void run(() => api.renameGroup(group.id, name.trim()), "Couldn't rename the group")}>
            Save
          </Button>
        </div>
      </Section>

      <Section title="Members" description="Each agent sees every message and chooses whether to reply. Quieter settings only change when it considers one.">
        <ul className="space-y-3">
          {group.members.map((m) => (
            <li key={m.agent_id} className="flex items-center gap-3">
              <Avatar name={m.name} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{m.name}</p>
                <p className="truncate text-xs text-muted">{m.role || "No role set"}</p>
              </div>
              <Select
                size="sm"
                className="w-36"
                aria-label={`How ${m.name} follows the group`}
                value={m.mode}
                options={MODES}
                onValueChange={(mode) => void run(() => api.setGroupMemberMode(group.id, m.agent_id, mode as MemberMode), "Couldn't change that")}
              />
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${m.name}`}
                disabled={group.members.length <= 1}
                onClick={() => void run(async () => {
                  await api.removeGroupMember(group.id, m.agent_id);
                }, "Couldn't remove the agent")}
              >
                <X />
              </Button>
            </li>
          ))}
        </ul>
        {outside.length > 0 && (
          <div className="mt-4 border-t border-border pt-4">
            <Select
              aria-label="Add an agent"
              placeholder="Add an agent…"
              value=""
              options={outside.map((a) => ({ value: a.id, label: a.name }))}
              onValueChange={(id) => void run(() => api.addGroupMember(group.id, id), "Couldn't add the agent")}
            />
          </div>
        )}
      </Section>

      {group.paused && (
        <Badge className="w-full justify-center whitespace-normal py-2 text-center">
          Paused: the agents talked a long time without you. Send a message to continue.
        </Badge>
      )}

      <Button variant="ghost" onClick={() => void remove()} className="w-full text-danger">
        <Trash2 /> Delete group
      </Button>
    </div>
  );
}
