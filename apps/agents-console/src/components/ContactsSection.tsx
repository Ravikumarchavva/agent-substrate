"use client";

import { useEffect, useState } from "react";
import { Button, Checkbox, Input, Section, toast } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import { reportError } from "@/lib/report-error";

type Entry = { agent_id: string; note: string };

/** Which of your other agents this one may message directly, and what it should know about each. Without a contact it cannot reach that agent at all. */
export function ContactsSection({ agent, others }: { agent: Agent; others: Agent[] }) {
  const [saved, setSaved] = useState<Entry[] | null>(null);
  const [value, setValue] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getContacts(agent.id)
      .then((contacts) => {
        if (!alive) return;
        const entries = contacts.map((c) => ({ agent_id: c.agent_id, note: c.note }));
        setSaved(entries);
        setValue(entries);
      })
      .catch((err) => reportError("Couldn't load its contacts", err));
    return () => {
      alive = false;
    };
  }, [agent.id]);

  if (others.length === 0) return null;
  const dirty = JSON.stringify(saved) !== JSON.stringify(value);
  const entryOf = (id: string) => value.find((c) => c.agent_id === id);

  const save = async () => {
    setBusy(true);
    try {
      const contacts = await api.setContacts(agent.id, value);
      const entries = contacts.map((c) => ({ agent_id: c.agent_id, note: c.note }));
      setSaved(entries);
      setValue(entries);
      toast.success("Contacts saved");
    } catch (err) {
      reportError("Couldn't save its contacts", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Agents it can message" description={`${agent.name} can ask these agents for help, one to one. Anyone not ticked is out of reach.`}>
      <ul className="space-y-3">
        {others.map((other) => {
          const entry = entryOf(other.id);
          return (
            <li key={other.id} className="space-y-1.5">
              <Checkbox
                checked={!!entry}
                disabled={saved === null}
                onChange={(e) => setValue(e.target.checked ? [...value, { agent_id: other.id, note: "" }] : value.filter((c) => c.agent_id !== other.id))}
                label={other.role ? `${other.name} · ${other.role}` : other.name}
              />
              {entry && (
                <Input
                  value={entry.note}
                  maxLength={300}
                  placeholder={`When to ask ${other.name}`}
                  aria-label={`Note about ${other.name}`}
                  onChange={(e) => setValue(value.map((c) => (c.agent_id === other.id ? { ...c, note: e.target.value } : c)))}
                />
              )}
            </li>
          );
        })}
      </ul>
      {dirty && (
        <div className="mt-4 flex justify-end">
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save contacts"}
          </Button>
        </div>
      )}
    </Section>
  );
}
