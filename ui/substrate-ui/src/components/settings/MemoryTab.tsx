"use client";

import { useCallback, useEffect, useState } from "react";
import { BrainCircuit, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Memory } from "@/types";

export function MemoryTab() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMemories(await api.getMemories());
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Memory isn't available for this deployment.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async (memory: Memory) => {
    if (!confirm("Delete this memory permanently? This cannot be undone.")) return;
    setDeletingId(memory.id);
    try {
      await api.deleteMemory(memory.id);
      setMemories((prev) => prev.filter((m) => m.id !== memory.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">Memory</h2>
          <p className="max-w-3xl text-sm leading-6 text-(--muted)">
            Facts and preferences the assistant has learned about you across every
            conversation — not just this one. It uses these for personalization,
            not as unconditional instructions. Delete anything that&apos;s wrong or
            no longer relevant.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex cursor-pointer items-center gap-2 self-start rounded-xl bg-(--card) px-4 py-2 text-sm text-(--muted) transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          style={{ boxShadow: "var(--shadow-sm)" }}
          aria-label="Refresh memories"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <p className="rounded-[18px] bg-(--badge-bg) px-4 py-3 text-sm text-(--muted)">
          {error}
        </p>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
        </div>
      ) : memories.length === 0 ? (
        <div className="flex flex-col items-center rounded-xl border border-(--border) bg-(--card) px-6 py-10 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-(--card-hover) text-(--muted)">
            <BrainCircuit className="h-6 w-6" />
          </div>
          <h4 className="mt-4 text-lg font-semibold text-foreground">Nothing saved yet</h4>
          <p className="mt-1 max-w-sm text-sm text-(--muted)">
            When you tell the assistant something to always remember — a
            preference, a standing instruction — it shows up here.
          </p>
        </div>
      ) : (
        <div
          className="space-y-2 rounded-[24px] p-3"
          style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
        >
          {memories.map((memory) => (
            <div
              key={memory.id}
              className="flex items-center gap-3 rounded-[18px] bg-background px-4 py-3"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-(--badge-bg) text-(--muted)">
                <BrainCircuit className="h-4 w-4" />
              </div>
              <p className="min-w-0 flex-1 text-sm text-foreground">{memory.content}</p>
              <button
                onClick={() => void handleDelete(memory)}
                disabled={deletingId === memory.id}
                className="shrink-0 cursor-pointer rounded-lg p-2 text-(--muted) transition-colors hover:bg-rose-500/10 hover:text-rose-400 disabled:opacity-40"
                aria-label="Delete memory"
              >
                {deletingId === memory.id ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
