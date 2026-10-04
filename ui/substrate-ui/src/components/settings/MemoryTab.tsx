"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { Button, Input, confirmAction, toast } from "@/design";
import { api } from "@/lib/api";
import { reportError } from "@/lib/report-error";
import type { Memory } from "@/types";

const MAX_CHARS = 500;

/** What the assistant has been told to remember about you. You can add, reword and delete it; deleting is permanent. */
export function MemoryTab() {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setMemories(await api.getMemories());
    } catch (err) {
      setMemories([]);
      reportError("Couldn't load your memories", err);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server on open
    void load();
  }, [load]);

  const add = async () => {
    const content = draft.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const added = await api.addMemory(content);
      setMemories((current) => [...(current ?? []), added]);
      setDraft("");
    } catch (err) {
      reportError("Couldn't save that memory", err);
    } finally {
      setSaving(false);
    }
  };

  const saveEdit = async () => {
    if (!editing || !editing.text.trim()) return;
    try {
      const updated = await api.updateMemory(editing.id, editing.text.trim());
      setMemories((current) => (current ?? []).map((m) => (m.id === updated.id ? updated : m)));
      setEditing(null);
    } catch (err) {
      reportError("Couldn't change that memory", err);
    }
  };

  const remove = async (memory: Memory) => {
    const ok = await confirmAction({
      title: "Forget this?",
      description: `"${memory.content}" will be deleted permanently and the assistant will no longer know it.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteMemory(memory.id);
      setMemories((current) => (current ?? []).filter((m) => m.id !== memory.id));
      toast.success("Forgotten");
    } catch (err) {
      reportError("Couldn't delete that memory", err);
    }
  };

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Memory</h2>
        <p className="mt-1 text-sm text-muted">
          Things the assistant keeps in mind in every conversation, such as your preferences and facts about you. It adds some itself when you
          ask it to remember something; you can add, reword or delete them here.
        </p>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <Input
          size="lg"
          value={draft}
          maxLength={MAX_CHARS}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add something to remember, e.g. “I prefer metric units”"
          aria-label="New memory"
        />
        <Button type="submit" variant="primary" size="lg" disabled={!draft.trim() || saving}>
          <Plus /> Add
        </Button>
      </form>

      {memories === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : memories.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center">
          <p className="text-sm font-medium text-foreground">Nothing remembered yet</p>
          <p className="mt-1 text-xs text-muted">Add a preference above, or tell the assistant “remember that…” in a chat.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {memories.map((memory) => (
            <li key={memory.id} className="flex items-start gap-2 px-4 py-3">
              {editing?.id === memory.id ? (
                <>
                  <Input
                    value={editing.text}
                    maxLength={MAX_CHARS}
                    autoFocus
                    onChange={(e) => setEditing({ id: memory.id, text: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveEdit();
                      if (e.key === "Escape") setEditing(null);
                    }}
                    aria-label="Edit memory"
                  />
                  <Button size="icon" variant="primary" aria-label="Save" onClick={() => void saveEdit()}>
                    <Check />
                  </Button>
                  <Button size="icon" variant="ghost" aria-label="Cancel" onClick={() => setEditing(null)}>
                    <X />
                  </Button>
                </>
              ) : (
                <>
                  <p className="min-w-0 flex-1 py-1 text-sm text-foreground">{memory.content}</p>
                  <Button size="icon" variant="ghost" aria-label="Edit" onClick={() => setEditing({ id: memory.id, text: memory.content })}>
                    <Pencil />
                  </Button>
                  <Button size="icon" variant="danger" aria-label="Delete" onClick={() => void remove(memory)}>
                    <Trash2 />
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
