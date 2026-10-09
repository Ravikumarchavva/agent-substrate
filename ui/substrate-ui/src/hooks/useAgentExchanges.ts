"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Agent, Exchange } from "@/lib/api/agents";
import { reportError } from "@/lib/report-error";

/** What this agent asked other agents and what they asked it, newest first; `null` until it has been read. Read again when the agent is next active. */
export function useAgentExchanges(agent: Agent | null): Exchange[] | null {
  const [found, setFound] = useState<{ id: string; items: Exchange[] } | null>(null);
  const id = agent?.id ?? null;
  const active = agent?.last_active ?? null;
  useEffect(() => {
    if (!id) return;
    let alive = true;
    api
      .getAgentExchanges(id)
      .then((items) => alive && setFound({ id, items }))
      .catch((err) => reportError("Couldn't load its conversations with other agents", err));
    return () => {
      alive = false;
    };
  }, [id, active]);
  return found && found.id === id ? found.items : null;
}
