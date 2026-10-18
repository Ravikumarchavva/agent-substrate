"use client";

import { useEffect, useState } from "react";
import { Coins, Pin, Trash2, X } from "lucide-react";
import { Badge, Button, Input, Meter, Section, Select, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, GroupFile, MemberMode } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { MAX_PINS, setPinned } from "@/lib/pins";
import { PictureEditor } from "@/components/PictureEditor";
import { Attachment } from "./Attachments";
import { Avatar } from "./Avatar";

const MODES = [
  { value: "mentions", label: "When addressed" },
  { value: "all", label: "Active" },
  { value: "muted", label: "Muted" },
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
  const isPinned = !!group.pinned_at;
  const [name, setName] = useState(group.name);
  const [cap, setCap] = useState(String(group.token_cap));
  const [dollars, setDollars] = useState(group.budget_usd ? String(group.budget_usd) : "");
  const [pauseAfter, setPauseAfter] = useState(String(group.breaker));
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
      <Section title="Picture">
        <PictureEditor
          name={group.name}
          src={group.avatar}
          className="flex items-center gap-4"
          save={async (file) => onChanged(await api.setGroupAvatar(group.id, file))}
          clear={async () => onChanged(await api.clearGroupAvatar(group.id))}
        />
      </Section>

      <Section title="Name">
        <div className="flex gap-2">
          <Input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Group name" />
          <Button disabled={!name.trim() || name.trim() === group.name} onClick={() => void run(() => api.renameGroup(group.id, name.trim()), "Couldn't rename the group")}>
            Save
          </Button>
        </div>
      </Section>

      <Section title="Members" description="Like people in a busy group: an agent answers when it is addressed and keeps up the conversation for a while. Active ones also chip in when they have something to add; muted ones only when named.">
        <ul className="space-y-3">
          {group.members.map((m) => (
            <li key={m.agent_id} className="flex items-center gap-3">
              <Avatar name={m.name} src={m.avatar} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{m.name}</p>
                <p className="truncate text-xs text-muted">{m.role || "No role set"}</p>
                {m.tokens_used > 0 && (
                  <p className="truncate text-2xs text-muted">
                    {m.tokens_used.toLocaleString()} tokens{m.cost_usd > 0 ? ` · $${m.cost_usd.toFixed(m.cost_usd < 0.01 ? 4 : 2)}` : ""}
                  </p>
                )}
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
        <div className="mt-4 flex items-center gap-3 border-t border-border pt-4">
          <p className="min-w-0 flex-1 text-sm text-foreground">
            {group.budget_usd ? `$${group.cost_usd.toFixed(2)} of $${group.budget_usd.toFixed(2)} spent` : `$${group.cost_usd.toFixed(2)} spent, no dollar limit`}
          </p>
        </div>
        <div className="mt-3 flex gap-2">
          <Input type="number" inputMode="decimal" min={0} step={0.5} value={dollars} placeholder="No limit" onChange={(e) => setDollars(e.target.value)} aria-label="Dollar limit" />
          <Button
            disabled={dollars === "" ? !group.budget_usd : !(Number(dollars) >= 0) || Number(dollars) === (group.budget_usd ?? 0)}
            onClick={() => void run(() => api.setGroupLimits(group.id, { budget_usd: Number(dollars || 0) }), "Couldn't change the limit")}
          >
            Set dollars
          </Button>
        </div>
      </Section>

      <Section title="Pause" description="If the agents keep talking to each other without you, the group pauses after this many messages in a row, until you write.">
        <div className="flex gap-2">
          <Input type="number" inputMode="numeric" min={4} max={200} value={pauseAfter} onChange={(e) => setPauseAfter(e.target.value)} aria-label="Pause after messages" />
          <Button
            disabled={!(Number(pauseAfter) >= 4 && Number(pauseAfter) <= 200) || Number(pauseAfter) === group.breaker}
            onClick={() => void run(() => api.setGroupLimits(group.id, { breaker: Math.round(Number(pauseAfter)) }), "Couldn't change that")}
          >
            Set
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
        onClick={() =>
          void setPinned("group", group.id, !isPinned)
            .then((ok) => (ok ? onChanged() : toast.error(`You can pin up to ${MAX_PINS} chats. Unpin one first.`)))
            .catch((err) => reportError("Couldn't change the pin", err))
        }
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
