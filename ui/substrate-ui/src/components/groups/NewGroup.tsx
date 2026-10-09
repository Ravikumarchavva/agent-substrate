"use client";

import { useState } from "react";
import { Bot } from "lucide-react";
import { Button, Checkbox, Input, Page, PageEmpty, Section } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";

type Props = { agents: Agent[]; onBack: () => void; onCreated: (group: Group) => void };

/** Make a group: a name, and which of your agents are in it. */
export function NewGroup({ agents, onBack, onCreated }: Props) {
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  if (agents.length === 0) {
    return (
      <Page title="New group" onBack={onBack}>
        <PageEmpty icon={Bot} title="Make an agent first">
          A group is you and your agents in one conversation. Create at least one agent, then come back.
        </PageEmpty>
      </Page>
    );
  }

  const create = async () => {
    setBusy(true);
    try {
      onCreated(await api.createGroup(name.trim(), chosen.map((agent_id) => ({ agent_id, mode: "mentions" }))));
    } catch (err) {
      reportError("Couldn't create the group", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="New group" subtitle="You and several of your agents in one conversation." onBack={onBack} layout="columns">
        <Section title="Name">
          <Input value={name} maxLength={60} placeholder="Trip planning" onChange={(e) => setName(e.target.value)} aria-label="Group name" />
        </Section>
        <Section title="Agents in it" description="Each one sees every message and decides whether to reply.">
          <ul className="space-y-2">
            {agents.map((a) => (
              <li key={a.id}>
                <Checkbox
                  checked={chosen.includes(a.id)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, a.id] : chosen.filter((id) => id !== a.id))}
                  label={a.role ? `${a.name} · ${a.role}` : a.name}
                />
              </li>
            ))}
          </ul>
        </Section>
        <div data-span="all" className="flex justify-end">
          <Button variant="primary" disabled={!name.trim() || chosen.length === 0 || busy} onClick={() => void create()}>
            {busy ? "Creating…" : "Create group"}
          </Button>
        </div>
    </Page>
  );
}
