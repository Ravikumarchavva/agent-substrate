"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { foldEntries } from "@/components/groups/entries";
import { api } from "@/lib/api";
import type { GroupEntry } from "@/lib/api/groups";
import type { ViewChat } from "@/lib/api/observe";
import { reportError } from "@/lib/report-error";

/** What was said in one of an agent's conversations: the latest page, older ones on request, and new entries as they are pushed. */
export function useObservedMessages(agentId: string, key: string) {
  const [entries, setEntries] = useState<GroupEntry[]>([]);
  const [chat, setChat] = useState<ViewChat | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  // The oldest entry fetched (edits and reactions count, though they are not shown), which an older page is asked for before.
  const oldest = useRef(-1);

  useEffect(() => {
    let alive = true;
    setEntries([]);
    setChat(null);
    setHasMore(false);
    setLoading(true);
    api
      .getViewMessages(agentId, key)
      .then((page) => {
        if (!alive) return;
        oldest.current = page.entries.length > 0 ? page.entries[0].seq : -1;
        setEntries((held) => foldEntries(held, page.entries));
        setChat(page.chat);
        setHasMore(page.has_more);
      })
      .catch((err) => alive && reportError("Couldn't load that conversation", err))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [agentId, key]);

  const loadEarlier = useCallback(async () => {
    if (oldest.current <= 0) return;
    setLoading(true);
    try {
      const page = await api.getViewMessages(agentId, key, oldest.current);
      if (page.entries.length > 0) oldest.current = page.entries[0].seq;
      setEntries((held) => foldEntries(held, page.entries));
      setHasMore(page.has_more);
    } catch (err) {
      reportError("Couldn't load earlier messages", err);
    } finally {
      setLoading(false);
    }
  }, [agentId, key]);

  const add = useCallback((incoming: GroupEntry[]) => setEntries((held) => foldEntries(held, incoming)), []);

  return { entries, chat, hasMore, loading, loadEarlier, add };
}
