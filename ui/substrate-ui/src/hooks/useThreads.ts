"use client";

import { useState, useCallback, useRef } from "react";
import type { Thread } from "@/types";
import { api } from "@/lib/api";
import { THREAD_PAGE_SIZE } from "@/lib/api/threads";
import { reportError } from "@/lib/report-error";
import { toast } from "@/design";

interface UseThreadsOptions {
  autoSelectFirstThread?: boolean;
}

export function useThreads(
  selectThread: (id: string | null, mode?: "replace" | "push") => void,
  currentThreadId: string | null,
  { autoSelectFirstThread = true }: UseThreadsOptions = {},
) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [showArchived, setShowArchivedState] = useState(false);
  const hasAutoSelected = useRef(false);

  const loadThreads = useCallback(async () => {
    try {
      const fetchedThreads: Thread[] = await api.getThreads({ archived: showArchived });
      setThreads(fetchedThreads);
      setHasMore(fetchedThreads.length >= THREAD_PAGE_SIZE);

      // An agent's conversation is not in the list (it lives under its agent), so a page opened on one is not a stray id to redirect away from.
      const isAgentThread = async () => (currentThreadId ? !!(await api.getThread(currentThreadId).catch(() => null))?.agent_id : false);

      if (fetchedThreads.length === 0) {
        if (autoSelectFirstThread && currentThreadId && !(await isAgentThread())) {
          selectThread(null);
        }
        hasAutoSelected.current = true;
        return;
      }

      const hasCurrentThread = currentThreadId
        ? fetchedThreads.some((thread: Thread) => thread.id === currentThreadId)
        : false;

      if (currentThreadId && !hasCurrentThread && autoSelectFirstThread && !hasAutoSelected.current && !(await isAgentThread())) {
        selectThread(fetchedThreads[0].id);
      }
      hasAutoSelected.current = true;
    } catch (error) {
      reportError("Couldn't load your conversations", error);
    }
  }, [autoSelectFirstThread, currentThreadId, selectThread, showArchived]);

  const handleNewChat = useCallback((callbacks?: { onCreated?: () => void }) => {
    selectThread(null, "push");
    callbacks?.onCreated?.();
  }, [selectThread]);

  const handleSelectThread = useCallback((threadId: string, callbacks?: { onSelected?: () => void }) => {
    selectThread(threadId, "push");
    callbacks?.onSelected?.();
  }, [selectThread]);

  const handleDeleteThread = useCallback(async (threadId: string) => {
    try {
      await api.deleteThread(threadId);
      if (currentThreadId === threadId) {
        const remaining = threads.filter((t) => t.id !== threadId);
        selectThread(remaining[0]?.id ?? null);
      }
      setThreads((current) => current.filter((thread) => thread.id !== threadId));
    } catch (error) {
      reportError("Couldn't delete the conversation", error);
    }
  }, [currentThreadId, selectThread, threads]);

  const handleRenameThread = useCallback(async (threadId: string, newName: string) => {
    try {
      await api.updateThread(threadId, newName);
      setThreads((current) =>
        current.map((thread) =>
          thread.id === threadId
            ? { ...thread, name: newName, updated_at: new Date().toISOString() }
            : thread
        )
      );
    } catch (error) {
      reportError("Couldn't rename the conversation", error);
    }
  }, []);

  /** The next page, appended (a thread already listed, say after a rename, is not duplicated). */
  const loadMore = useCallback(async () => {
    try {
      const next = await api.getThreads({ offset: threads.length, archived: showArchived });
      setThreads((current) => [...current, ...next.filter((t) => !current.some((c) => c.id === t.id))]);
      setHasMore(next.length >= THREAD_PAGE_SIZE);
    } catch (error) {
      reportError("Couldn't load more conversations", error);
    }
  }, [threads.length, showArchived]);

  const setShowArchived = useCallback(async (archived: boolean) => {
    setShowArchivedState(archived);
    try {
      const fetched = await api.getThreads({ archived });
      setThreads(fetched);
      setHasMore(fetched.length >= THREAD_PAGE_SIZE);
    } catch (error) {
      reportError("Couldn't load your conversations", error);
    }
  }, []);

  /** Pin or unpin: a pinned conversation sorts first. */
  const handlePinThread = useCallback(async (threadId: string, pinned: boolean) => {
    try {
      const updated = await api.updateThread(threadId, { pinned });
      setThreads((current) => {
        const next = current.map((t) => (t.id === threadId ? { ...t, pinned_at: updated.pinned_at ?? null } : t));
        return [...next.filter((t) => t.pinned_at), ...next.filter((t) => !t.pinned_at)];
      });
    } catch (error) {
      reportError(pinned ? "Couldn't pin the conversation" : "Couldn't unpin the conversation", error);
    }
  }, []);

  /** Archive (hide from the main list, keep) or restore. The thread leaves the list being shown either way. */
  const handleArchiveThread = useCallback(
    async (threadId: string, archived: boolean) => {
      try {
        await api.updateThread(threadId, { archived });
        if (currentThreadId === threadId && archived) selectThread(null);
        setThreads((current) => current.filter((t) => t.id !== threadId));
        toast.success(archived ? "Archived" : "Restored", archived ? "Find it under Settings → Archived." : undefined);
      } catch (error) {
        reportError(archived ? "Couldn't archive the conversation" : "Couldn't restore the conversation", error);
      }
    },
    [currentThreadId, selectThread],
  );

  return {
    hasMore,
    loadMore,
    showArchived,
    setShowArchived,
    handlePinThread,
    handleArchiveThread,
    threads,
    setThreads,
    loadThreads,
    handleNewChat,
    handleSelectThread,
    handleDeleteThread,
    handleRenameThread,
  };
}
