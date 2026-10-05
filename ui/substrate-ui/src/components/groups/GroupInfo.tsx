"use client";

import { useEffect, useState } from "react";
import { Coins, Pin, Trash2, X } from "lucide-react";
import { Badge, Button, Input, Meter, Section, Select, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, GroupFile, MemberMode } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { chatKey, usePinnedChats } from "@/lib/pins";
import { Attachment } from "./Attachments";
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
  const { pinned, toggle } = usePinnedChats();
  const isPinned = pinned.includes(chatKey("group", group.id));
  const [name, setName] = useState(group.name);
  const [cap, setCap] = useState(String(group.token_cap));
  const [files, setFiles] = useState<GroupFile[] | null>(null);
  // Everything the group shares, including what its agents made: refreshed when the group's last message changes.
  useEffect(() => {
    let alive = true;
    api.getGroupFiles(group.id).then((f) => alive && setFiles(f)).catch(() => alive && setFiles([]));
    return () => {
      alive = false;
    };
  }, [group.id, group.last_message, group.updated_at]);
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

      <Section title="Files" description="Shared with everyone in the group. The agents are shown the text of what you share; pictures they cannot read.">
        {files === null ? (
          <p className="text-xs text-muted">Loading…</p>
        ) : files.length === 0 ? (
          <p className="text-xs text-muted">Nothing shared yet. Attach a file to a message, or drop one into the chat.</p>
        ) : (
          <ul className="space-y-1.5">
            {files.map((f) => (
              <li key={f.key}>
                <Attachment file={f} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Budget" description="The most the agents may use in this group in all. When it is reached they stop replying until you raise it.">
        <div className="flex items-center gap-3">
          <Meter icon={Coins} label="Group budget" used={group.tokens_used} limit={group.token_cap} />
          <p className="min-w-0 flex-1 text-sm text-foreground">
            {group.tokens_used.toLocaleString()} of {group.token_cap.toLocaleString()} tokens used
          </p>
        </div>
        <div className="mt-3 flex gap-2">
          <Input type="number" inputMode="numeric" min={1000} step={50000} value={cap} onChange={(e) => setCap(e.target.value)} aria-label="Token limit" />
          <Button
            disabled={!(Number(cap) >= 1000) || Number(cap) === group.token_cap}
            onClick={() => void run(() => api.setGroupTokenCap(group.id, Math.round(Number(cap))), "Couldn't change the limit")}
          >
            Set limit
          </Button>
        </div>
      </Section>

      {group.paused && (
        <Badge className="w-full justify-center whitespace-normal py-2 text-center">
          Paused: the agents talked a long time without you. Send a message to continue.
        </Badge>
      )}

      <Button
        variant="ghost"
        aria-pressed={isPinned}
        onClick={() => {
          if (toggle(chatKey("group", group.id)) === false) toast.error("You can pin up to 5 chats. Unpin one first.");
        }}
        className="w-full"
      >
        <Pin /> {isPinned ? "Unpin from sidebar" : "Pin to sidebar"}
      </Button>

      <Button variant="ghost" onClick={() => void remove()} className="w-full text-danger">
        <Trash2 /> Delete group
      </Button>
    </div>
  );
}
