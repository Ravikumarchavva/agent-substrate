"use client";

import { useCallback, useEffect, useState } from "react";
import { BrainCircuit, Check, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { Button, Input, Page, Section, SettingGroup, SettingRow, Textarea, confirmAction, toast } from "@/design";
import { schedulePreferencesPush } from "@/lib/preferences-sync";
import type { InstructionValidationResult } from "@/types";
import { api } from "@/lib/api";
import { reportError } from "@/lib/report-error";
import type { Memory } from "@/types";

const MAX_CHARS = 500;
export const CUSTOM_INSTRUCTIONS_KEY = "system_instructions_override";

/** What the assistant knows about you: standing instructions you write, and the memories it keeps. Memories can be added, reworded and deleted; deleting is permanent. */
export function PersonalizationTab() {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  // Custom instructions: kept in this browser for the composer and synced to the account; checked against the content policy before saving.
  const [instructions, setInstructions] = useState(() => (typeof window === "undefined" ? "" : (localStorage.getItem(CUSTOM_INSTRUCTIONS_KEY) ?? "")));
  const [savedInstructions, setSavedInstructions] = useState(instructions);
  const [editingInstructions, setEditingInstructions] = useState(false);
  const [checking, setChecking] = useState(false);
  const [instructionError, setInstructionError] = useState<string | null>(null);

  const saveInstructions = async (text: string) => {
    setInstructionError(null);
    const value = text.trim();
    if (value) {
      setChecking(true);
      try {
        const verdict = (await api.checkCustomInstructions(value)) as InstructionValidationResult;
        if (!verdict.allowed) {
          setInstructionError(verdict.reason ?? "These instructions can't be saved: they break the content policy.");
          return;
        }
      } catch {
        setInstructionError("Couldn't check the instructions. Please try again.");
        return;
      } finally {
        setChecking(false);
      }
      localStorage.setItem(CUSTOM_INSTRUCTIONS_KEY, value);
    } else {
      localStorage.removeItem(CUSTOM_INSTRUCTIONS_KEY);
    }
    schedulePreferencesPush();
    setInstructions(value);
    setSavedInstructions(value);
    setEditingInstructions(false);
    toast.success(value ? "Instructions saved" : "Instructions cleared");
  };

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
    <Page title="Personalization" subtitle="What the assistant knows about you and how it should answer." icon={BrainCircuit}>
      <SettingGroup title="Custom instructions">
        <SettingRow
          label="How should the assistant respond?"
          description={savedInstructions ? <span className="line-clamp-2 whitespace-pre-wrap">{savedInstructions}</span> : "Standing instructions for every new message, such as tone or format. Nothing set yet."}
        >
          {!editingInstructions && (
            <Button variant="secondary" onClick={() => setEditingInstructions(true)}>
              <Pencil /> {savedInstructions ? "Edit" : "Add"}
            </Button>
          )}
        </SettingRow>
        {editingInstructions && (
          <div className="space-y-3 px-4 py-3">
            <Textarea
              value={instructions}
              onChange={(e) => {
                setInstructions(e.target.value);
                setInstructionError(null);
              }}
              rows={6}
              maxLength={2000}
              autoFocus
              aria-label="Custom instructions"
              placeholder="e.g. Always respond in British English. Keep answers concise."
            />
            {instructionError && (
              <p role="alert" className="text-sm text-danger">
                {instructionError}
              </p>
            )}
            <div className="flex items-center gap-2">
              <Button variant="primary" disabled={checking} onClick={() => void saveInstructions(instructions)}>
                {checking && <Loader2 className="animate-spin" />}
                {checking ? "Checking…" : "Save"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setInstructions(savedInstructions);
                  setInstructionError(null);
                  setEditingInstructions(false);
                }}
              >
                Cancel
              </Button>
              {savedInstructions && (
                <Button variant="danger" className="ml-auto" onClick={() => void saveInstructions("")}>
                  Clear
                </Button>
              )}
            </div>
          </div>
        )}
      </SettingGroup>

      <Section title="Memory" description="Things the assistant keeps in mind in every conversation. It adds some itself when you ask it to remember something.">
        <div className="space-y-4">
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
      </Section>
    </Page>
  );
}
