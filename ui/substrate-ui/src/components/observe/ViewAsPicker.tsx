"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Button, Dialog, DialogContent, Input } from "@/design";
import { Avatar } from "@/components/groups/Avatar";
import type { Agent } from "@/lib/api/agents";

type Props = {
  agents: Agent[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (agent: Agent) => void;
};

/** Pick an agent whose account to open (read only). A search over all of them, since you may have many. */
export function ViewAsPicker({ agents, open, onOpenChange, onPick }: Props) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter((a) => !q || a.name.toLowerCase().includes(q) || a.role.toLowerCase().includes(q));
  }, [agents, query]);
  return (
    <Dialog open={open} onOpenChange={(next) => (setQuery(""), onOpenChange(next))}>
      <DialogContent title="View an agent's account" description="You see its chats as it sees them. It is read only: you cannot write in them.">
        <label className="mt-3 flex h-control-lg items-center gap-2 rounded-full bg-background px-4 text-sm focus-within:ring-1 focus-within:ring-accent">
          <Search className="shrink-0 text-muted" aria-hidden />
          <Input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search agents" aria-label="Search agents" className="h-auto border-0 bg-transparent p-0 focus:ring-0" />
        </label>
        <ul className="scroll-area mt-3 max-h-80 space-y-0.5">
          {shown.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">{agents.length === 0 ? "You have no agents yet." : "No agent matches."}</li>}
          {shown.map((a) => (
            <li key={a.id}>
              <Button variant="ghost" onClick={() => (onOpenChange(false), onPick(a))} className="h-auto! w-full justify-start gap-3 px-2 py-2 text-left font-normal">
                <Avatar name={a.name} src={a.avatar} className="size-10" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground">{a.name}</span>
                  <span className="block truncate text-xs text-muted">{a.role || "No role set"}</span>
                </span>
              </Button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
