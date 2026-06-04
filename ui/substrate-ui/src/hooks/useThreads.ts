"use client";

import { useState, useCallback, useRef } from "react";
import type { Thread } from "@/types";
import { api } from "@/lib/api";

interface UseThreadsOptions {
  autoSelectFirstThread?: boolean;
}

export function useThreads(
  selectThread: (id: string | null, mode?: "replace" | "push") => void,
  currentThreadId: string | null,
  { autoSelectFirstThread = true }: UseThreadsOptions = {},
) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const hasAutoSelected = useRef(false);

  const loadThreads = useCallback(async () => {
    try {
      const fetchedThreads: Thread[] = await api.getThreads();
      setThreads(fetchedThreads);

      if (fetchedThreads.length === 0) {
        if (autoSelectFirstThread && currentThreadId) {
          selectThread(null);
        }
        hasAutoSelected.current = true;
        return;
      }

      const hasCurrentThread = currentThreadId
        ? fetchedThreads.some((thread: Thread) => thread.id === currentThreadId)
        : false;

      if (currentThreadId && !hasCurrentThread && autoSelectFirstThread && !hasAutoSelected.current) {
        selectThread(fetchedThreads[0].id);
      }
      hasAutoSelected.current = true;
    } catch (error) {
      console.error("Failed to load threads:", error);
    }
  }, [autoSelectFirstThread, currentThreadId, selectThread]);

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
      console.error("Failed to delete thread:", error);
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
      console.error("Failed to rename thread:", error);
    }
  }, []);

  return {
    threads,
    setThreads,
    loadThreads,
    handleNewChat,
    handleSelectThread,
    handleDeleteThread,
    handleRenameThread,
  };
}
