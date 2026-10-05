"use client";

import { useState } from "react";
import { ArrowLeft, MessageSquare, Pin, Trash2 } from "lucide-react";
import { Button, Checkbox, Input, PageHeading, Pane, Section, Select, Textarea, toast } from "@/design";
import { CHAT_MODEL_OPTIONS } from "@/lib/model-preferences";
import { api } from "@/lib/api";
import type { Agent, AgentInput, ToolInfo } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { Avatar } from "@/components/groups/Avatar";
import { PictureEditor } from "@/components/PictureEditor";
import { ContactsSection } from "@/components/ContactsSection";
import { reportError } from "@/lib/report-error";

// The picker has no empty choice, so "the deployment's model" is a value of its own.
const DEFAULT_MODEL = "default";

const BLANK: AgentInput = { name: "", role: "", instructions: "", allowed_tools: null, model: null };

const same = (a: AgentInput, b: AgentInput) =>
  a.name === b.name && a.role === b.role && a.instructions === b.instructions && a.model === b.model && JSON.stringify(a.allowed_tools) === JSON.stringify(b.allowed_tools);


/** One agent as a contact: who it is, what it can do, the groups it is in and who it may message. Also where a new one is made. */
export function AgentDetail({ agent, others, groups, pinned, onTogglePin, onOpenGroup, tools, onBack, onSaved, onPictureChanged, onChat, onDelete }: { agent: Agent | null; others: Agent[]; groups: Group[]; pinned: boolean; onTogglePin: () => void; onOpenGroup: (groupId: string) => void; tools: ToolInfo[]; onBack: () => void; onSaved: (a: Agent) => void; onPictureChanged: () => void; onChat: () => void; onDelete: () => void }) {
  const initial: AgentInput = agent ? { name: agent.name, role: agent.role, instructions: agent.instructions, allowed_tools: agent.allowed_tools, model: agent.model } : BLANK;
  const [value, setValue] = useState<AgentInput>(initial);
  const [busy, setBusy] = useState(false);
  const dirty = !same(value, initial);
  const all = value.allowed_tools === null;

  const toggle = (name: string, on: boolean) => {
    const current = value.allowed_tools ?? [];
    setValue({ ...value, allowed_tools: on ? [...current, name] : current.filter((n) => n !== name) });
  };

  const save = async () => {
    setBusy(true);
    try {
      const body = { ...value, name: value.name.trim(), role: value.role.trim() };
      onSaved(agent ? await api.updateAgent(agent.id, body) : await api.createAgent(body));
      toast.success(agent ? "Saved" : "Agent created");
    } catch (err) {
      reportError("Couldn't save the agent", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Pane>
      {agent ? (
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <Button variant="ghost" size="icon" aria-label="Back" onClick={onBack} className="-ml-2 mt-1 @4xl:hidden">
              <ArrowLeft />
            </Button>
            <div className="flex min-w-0 flex-1 flex-col items-center gap-3 text-center @xl:flex-row @xl:text-left">
              <PictureEditor
                name={agent.name}
                src={agent.avatar}
                className="flex shrink-0 flex-col items-center gap-2"
                save={async (file) => void (await api.setAgentAvatar(agent.id, file), onPictureChanged())}
                clear={async () => void (await api.clearAgentAvatar(agent.id), onPictureChanged())}
              />
              <div className="min-w-0 flex-1">
                <h1 className="truncate text-2xl font-semibold tracking-tight text-foreground">{agent.name}</h1>
                <p className="mt-0.5 text-sm text-muted">{agent.role || "No role set"}</p>
                <p className="mt-0.5 text-xs text-muted">{groups.length === 0 ? "In no groups" : `In ${groups.length} group${groups.length === 1 ? "" : "s"}`}</p>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap justify-center gap-2 @xl:justify-start">
            <Button variant="primary" onClick={onChat}>
              <MessageSquare /> Message
            </Button>
            <Button variant="ghost" onClick={onTogglePin} aria-pressed={pinned}>
              <Pin /> {pinned ? "Unpin from sidebar" : "Pin to sidebar"}
            </Button>
            <Button variant="ghost" onClick={onDelete} className="text-danger">
              <Trash2 /> Delete
            </Button>
          </div>
        </div>
      ) : (
        <PageHeading title="New agent" subtitle="Give it a role, instructions and the tools it may use." onBack={onBack} backClassName="@4xl:hidden" />
      )}
      <Section title="Role">
        <div className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Name</span>
            <Input value={value.name} onChange={(e) => setValue({ ...value, name: e.target.value })} placeholder="Scout" maxLength={60} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Role</span>
            <Input value={value.role} onChange={(e) => setValue({ ...value, role: e.target.value })} placeholder="Researcher: finds and summarises sources" maxLength={160} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Instructions</span>
            <Textarea rows={6} value={value.instructions} onChange={(e) => setValue({ ...value, instructions: e.target.value })} placeholder="Always cite where a claim comes from. Keep answers under 200 words." />
          </label>
        </div>
      </Section>
      <Section
        title="Model"
        description={
          agent?.model
            ? agent.sees
              ? "It can see pictures that are shared with it."
              : "It reads text only: pictures shared with it are not seen."
            : "Leave it on the default, or give this agent a model of its own, in a chat with you and in groups."
        }
      >
        <Select
          className="w-72 max-w-full"
          aria-label="Model"
          value={value.model ?? DEFAULT_MODEL}
          onValueChange={(v) => setValue({ ...value, model: v === DEFAULT_MODEL ? null : v })}
          options={[{ value: DEFAULT_MODEL, label: "Default model" }, ...CHAT_MODEL_OPTIONS.map((o) => ({ value: o.id, label: o.label }))]}
        />
      </Section>
      <Section title="Tools it may use" description="Anything not ticked is off for this agent, whatever you ask it.">
        <div className="space-y-3">
          <Checkbox checked={all} onChange={(e) => setValue({ ...value, allowed_tools: e.target.checked ? null : [] })} label="All tools" />
          {!all && (
            <ul className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
              {tools.map((t) => (
                <li key={t.name}>
                  <Checkbox checked={value.allowed_tools?.includes(t.name) ?? false} onChange={(e) => toggle(t.name, e.target.checked)} label={t.name} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </Section>
      {agent && groups.length > 0 && (
        <Section title="Groups" description={`${agent.name} sees what is said in these.`}>
          <ul className="space-y-1">
            {groups.map((g) => (
              <li key={g.id}>
                <Button variant="ghost" onClick={() => onOpenGroup(g.id)} className="h-auto! w-full justify-start gap-3 px-2 py-2 text-left font-normal">
                  <Avatar name={g.name} className="size-9" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{g.name}</span>
                  <span className="shrink-0 text-xs text-muted">{g.members.length} members</span>
                </Button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {agent && <ContactsSection agent={agent} others={others} />}
      <div className="flex justify-end gap-2">
        {dirty && agent && <Button onClick={() => setValue(initial)}>Discard</Button>}
        <Button variant="primary" disabled={!value.name.trim() || !dirty || busy} onClick={() => void save()}>
          {busy ? "Saving…" : agent ? "Save changes" : "Create agent"}
        </Button>
      </div>
    </Pane>
  );
}
