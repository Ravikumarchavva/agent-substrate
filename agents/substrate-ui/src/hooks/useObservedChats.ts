"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ViewChat, ViewFilter } from "@/lib/api/observe";
import { bumped, mergePage } from "@/components/observe/pages";
import { reportError } from "@/lib/report-error";

const SEARCH_DELAY_MS = 250;

/**
 * An agent's chat list, a page at a time: the search is typed here and run on the server (after a short pause), as is the filter, and each page
 * after the first is asked for by the list reaching its end. Changing the agent, the search or the filter starts the list again, and an answer
 * that arrives after that is ignored, so a slow page can never land in the wrong list.
 */
export function useObservedChats(agentId: string, q: string, kind: ViewFilter) {
  const [items, setItems] = useState<ViewChat[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const ticket = useRef(0);

  const load = useCallback(
    async (before: string | null, search: string, filter: ViewFilter) => {
      const mine = ++ticket.current;
      setLoading(true);
      try {
        const page = await api.getViewChats(agentId, { q: search, kind: filter, before });
        if (mine !== ticket.current) return;
        setItems((held) => (before ? mergePage(held, page.items) : page.items));
        setNext(page.next);
      } catch (err) {
        if (mine === ticket.current) reportError("Couldn't load its chats", err);
      } finally {
        if (mine === ticket.current) setLoading(false);
      }
    },
    [agentId],
  );

  useEffect(() => {
    setItems([]);
    setNext(null);
    const timer = setTimeout(() => void load(null, q.trim(), kind), q ? SEARCH_DELAY_MS : 0);
    return () => clearTimeout(timer);
  }, [load, q, kind]);

  const loadMore = useCallback(() => {
    if (next && !loading) void load(next, q.trim(), kind);
  }, [load, next, loading, q, kind]);

  /** Something was just said in one of the conversations: it moves to the top with the new line. */
  const bump = useCallback((key: string, preview: string, lastSender: string | null, at: string) => setItems((held) => bumped(held, key, preview, lastSender, at)), []);

  return { items, loading, hasMore: next !== null, loadMore, bump };
}
