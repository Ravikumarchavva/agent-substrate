"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, MessageSquare, Pencil, Plus, Trash2 } from "lucide-react";
import { Button, Checkbox, Dialog, DialogContent, DialogFooter, Input, Page, PageEmpty, SettingGroup, SettingRow, Textarea, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent, AgentInput, ToolInfo } from "@/lib/api/agents";
import { reportError } from "@/lib/report-error";

const BLANK: AgentInput = { name: "", role: "", instructions: "", allowed_tools: null };

/** The Agents page: saved roles you can start a conversation with. Each has standing instructions, the tools it may use, and a workspace of its own whose files stay between conversations. */
export function AgentsPanel({ onStartChat }: { onStartChat: (agentId: string) => void }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [form, setForm] = useState<{ id: string | null; value: AgentInput } | null>(null);

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
  }, [load]);

  const remove = async (agent: Agent) => {
    if (!(await confirmAction({ title: `Delete ${agent.name}?`, description: "Its files are deleted too. Conversations you had with it stay, as ordinary ones.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteAgent(agent.id);
      setAgents((all) => (all ?? []).filter((a) => a.id !== agent.id));
    } catch (err) {
      reportError("Couldn't delete the agent", err);
    }
  };

  return (
    <Page
      title="Agents"
      subtitle="Saved roles to start a conversation with. Each keeps its own files between conversations."
      actions={
        <Button variant="primary" onClick={() => setForm({ id: null, value: BLANK })}>
          <Plus /> New agent
        </Button>
      }
    >
      {agents !== null && agents.length === 0 ? (
        <PageEmpty icon={Bot} title="No agents yet">
          Make one for a job you do often, like “Researcher” or “Editor”, and pick it when you start a new chat.
        </PageEmpty>
      ) : (
        agents !== null && (
          <SettingGroup title="Your agents">
            {agents.map((agent) => (
              <SettingRow key={agent.id} label={agent.name} description={`${agent.role || "No role set"} · ${agent.allowed_tools === null ? "all tools" : `${agent.allowed_tools.length} tool${agent.allowed_tools.length === 1 ? "" : "s"}`}`}>
                <Button size="sm" onClick={() => onStartChat(agent.id)}>
                  <MessageSquare /> Chat
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Edit ${agent.name}`} onClick={() => setForm({ id: agent.id, value: { name: agent.name, role: agent.role, instructions: agent.instructions, allowed_tools: agent.allowed_tools } })}>
                  <Pencil />
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Delete ${agent.name}`} onClick={() => void remove(agent)}>
                  <Trash2 />
                </Button>
              </SettingRow>
            ))}
          </SettingGroup>
        )
      )}
      <AgentDialog
        form={form}
        onClose={() => setForm(null)}
        onSaved={(saved) => {
          setForm(null);
          setAgents((all) => (all?.some((a) => a.id === saved.id) ? all.map((a) => (a.id === saved.id ? saved : a)) : [...(all ?? []), saved]));
        }}
      />
    </Page>
  );
}

function AgentDialog({ form, onClose, onSaved }: { form: { id: string | null; value: AgentInput } | null; onClose: () => void; onSaved: (a: Agent) => void }) {
  const [value, setValue] = useState<AgentInput>(BLANK);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!form) return;
    setValue(form.value);
    api.getAgentTools().then(setTools).catch((err) => reportError("Couldn't load the tool list", err));
  }, [form]);

  const all = value.allowed_tools === null;
  const toggle = (name: string, on: boolean) => {
    const current = value.allowed_tools ?? [];
    setValue({ ...value, allowed_tools: on ? [...current, name] : current.filter((n) => n !== name) });
  };

  const save = async () => {
    if (!form) return;
    setBusy(true);
    try {
      const body = { ...value, name: value.name.trim(), role: value.role.trim() };
      onSaved(form.id ? await api.updateAgent(form.id, body) : await api.createAgent(body));
      toast.success(form.id ? "Saved" : "Agent created");
    } catch (err) {
      reportError("Couldn't save the agent", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={form !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={form?.id ? "Edit agent" : "New agent"} className="max-w-xl">
        <div className="space-y-4">
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
            <Textarea rows={5} value={value.instructions} onChange={(e) => setValue({ ...value, instructions: e.target.value })} placeholder="Always cite where a claim comes from. Keep answers under 200 words." />
          </label>
          <div className="space-y-2">
            <span className="text-xs font-medium text-muted">Tools it may use</span>
            <Checkbox checked={all} onChange={(e) => setValue({ ...value, allowed_tools: e.target.checked ? null : [] })} label="All tools" />
            {!all && (
              <ul className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
                {tools.map((t) => (
                  <li key={t.name}>
                    <Checkbox checked={value.allowed_tools?.includes(t.name) ?? false} onChange={(e) => toggle(t.name, e.target.checked)} label={t.name} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!value.name.trim() || busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
