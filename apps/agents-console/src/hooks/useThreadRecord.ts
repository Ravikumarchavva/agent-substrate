"use client";

import { useEffect, useState } from "react";
import type { Thread } from "@/types";
import { api } from "@/lib/api";

/** The record of the open conversation: from the list when it is there, else fetched once. An agent's conversation is not in the list, so this is where
 *  what it is with (`agent_id`) and what it is called come from, before the agents themselves have loaded. */
export function useThreadRecord(threadId: string | null, listed: Thread[]): Thread | null {
  const [fetched, setFetched] = useState<Thread | null>(null);
  const inList = threadId ? (listed.find((t) => t.id === threadId) ?? null) : null;

  useEffect(() => {
    if (!threadId || inList) return;
    let cancelled = false;
    api
      .getThread(threadId)
      .then((t) => !cancelled && setFetched(t))
      .catch(() => !cancelled && setFetched(null));
    return () => {
      cancelled = true;
    };
  }, [threadId, inList]);

  if (inList) return inList;
  return fetched && fetched.id === threadId ? fetched : null;
}
