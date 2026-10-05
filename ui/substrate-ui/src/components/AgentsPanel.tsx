"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, MessageSquare, Plus, Trash2 } from "lucide-react";
import { Button, Checkbox, Input, Page, PageEmpty, PageHeading, Pane, Section, Textarea, cn, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent, AgentInput, ToolInfo } from "@/lib/api/agents";
import { ContactsSection } from "@/components/ContactsSection";
import { takeAgentToEdit } from "@/lib/agent-focus";
import { reportError } from "@/lib/report-error";

const BLANK: AgentInput = { name: "", role: "", instructions: "", allowed_tools: null };
const NEW = "new";

const same = (a: AgentInput, b: AgentInput) =>
  a.name === b.name && a.role === b.role && a.instructions === b.instructions && JSON.stringify(a.allowed_tools) === JSON.stringify(b.allowed_tools);

const toolsLabel = (tools: string[] | null) => (tools === null ? "all tools" : `${tools.length} tool${tools.length === 1 ? "" : "s"}`);

/**
 * The Agents page: saved roles you can start a conversation with, each with standing instructions, the tools it may use and a workspace of its own whose files
 * stay between conversations. The list is on the left and the agent you opened is edited on the right, so you can move between them without losing your place.
 */
export function AgentsPanel({ onStartChat }: { onStartChat: (agentId: string) => void }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(() => (typeof window === "undefined" ? null : takeAgentToEdit())); // an agent id, `NEW`, or nothing

  const load = useCallback(async () => {
    try {
      setAgents(await api.getAgents());
    } catch (err) {
      setAgents([]);
      reportError("Couldn't load your agents", err);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load on open
    void load();
    api.getAgentTools().then(setTools).catch((err) => reportError("Couldn't load the tool list", err));
  }, [load]);

  const current = useMemo(() => agents?.find((a) => a.id === selected) ?? null, [agents, selected]);

  const remove = async (agent: Agent) => {
    if (!(await confirmAction({ title: `Delete ${agent.name}?`, description: "Its files are deleted too. Conversations you had with it stay, as ordinary ones.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteAgent(agent.id);
      setAgents((all) => (all ?? []).filter((a) => a.id !== agent.id));
      setSelected(null);
    } catch (err) {
      reportError("Couldn't delete the agent", err);
    }
  };

  const saved = (agent: Agent) => {
    setAgents((all) => (all?.some((a) => a.id === agent.id) ? all.map((a) => (a.id === agent.id ? agent : a)) : [...(all ?? []), agent]));
    setSelected(agent.id);
  };

  if (agents !== null && agents.length === 0 && selected !== NEW) {
    return (
      <Page title="Agents" subtitle="Saved roles to start a conversation with. Each keeps its own files between conversations." actions={<NewButton onClick={() => setSelected(NEW)} />}>
        <PageEmpty icon={Bot} title="No agents yet">
          Make one for a job you do often, like “Researcher” or “Editor”, and pick it when you start a new chat.
        </PageEmpty>
      </Page>
    );
  }

  const editing = selected === NEW || current !== null;
  return (
    <div className="@container h-full w-full bg-background text-foreground">
      <div className="grid h-full min-h-0 @4xl:grid-cols-[minmax(20rem,24rem)_minmax(0,1fr)]">
        <div className={cn("min-h-0 @4xl:border-r @4xl:border-border", editing && "hidden @4xl:block")}>
          <Pane>
            <PageHeading title="Agents" subtitle="Saved roles. Each keeps its own files." actions={<NewButton onClick={() => setSelected(NEW)} />} />
            <ul className="space-y-1.5">
              {(agents ?? []).map((agent) => {
                const open = agent.id === selected;
                return (
                  <li key={agent.id}>
                    <Button
                      variant="ghost"
                      onClick={() => setSelected(agent.id)}
                      aria-current={open}
                      className={cn("h-auto! min-h-0 w-full justify-start gap-3 whitespace-normal rounded-xl border px-3 py-2.5 text-left font-normal", open ? "border-accent/40 bg-accent/12" : "border-transparent hover:bg-card-hover")}
                    >
                      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-badge text-muted">
                        <Bot className="size-4" aria-hidden />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-foreground">{agent.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {agent.role || "No role set"} · {toolsLabel(agent.allowed_tools)}
                        </span>
                      </span>
                    </Button>
                  </li>
                );
              })}
            </ul>
          </Pane>
        </div>
        <div className={cn("min-h-0", !editing && "hidden @4xl:block")}>
          {editing ? (
            <AgentEditor
              key={selected ?? NEW}
              agent={current}
              others={(agents ?? []).filter((a) => a.id !== current?.id)}
              tools={tools}
              onBack={() => setSelected(null)}
              onSaved={saved}
              onChat={() => current && onStartChat(current.id)}
              onDelete={() => current && void remove(current)}
            />
          ) : (
            <Pane className="items-center justify-center text-center">
              <Bot className="size-8 text-muted" aria-hidden />
              <p className="text-sm font-medium text-foreground">Pick an agent to see and change what it does</p>
            </Pane>
          )}
        </div>
      </div>
    </div>
  );
}

function NewButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="primary" onClick={onClick}>
      <Plus /> New agent
    </Button>
  );
}

function AgentEditor({ agent, others, tools, onBack, onSaved, onChat, onDelete }: { agent: Agent | null; others: Agent[]; tools: ToolInfo[]; onBack: () => void; onSaved: (a: Agent) => void; onChat: () => void; onDelete: () => void }) {
  const initial: AgentInput = agent ? { name: agent.name, role: agent.role, instructions: agent.instructions, allowed_tools: agent.allowed_tools } : BLANK;
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
      <PageHeading
        title={agent ? agent.name : "New agent"}
        subtitle={agent ? agent.role || "No role set" : "Give it a role, instructions and the tools it may use."}
        onBack={onBack}
        backClassName="@4xl:hidden"
        actions={
          <>
            {agent && (
              <>
                <Button variant="primary" onClick={onChat}>
                  <MessageSquare /> Chat
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Delete ${agent.name}`} onClick={onDelete}>
                  <Trash2 />
                </Button>
              </>
            )}
          </>
        }
      />
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
