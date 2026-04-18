"use client";

import { useState, useCallback } from "react";
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

  const loadThreads = useCallback(async () => {
    try {
      const fetchedThreads: Thread[] = await api.getThreads();
      setThreads(fetchedThreads);

      if (fetchedThreads.length === 0) {
        if (autoSelectFirstThread && currentThreadId) {
          selectThread(null);
        }
        return;
      }

      const hasCurrentThread = currentThreadId
        ? fetchedThreads.some((thread: Thread) => thread.id === currentThreadId)
        : false;

      if (!hasCurrentThread && autoSelectFirstThread) {
        selectThread(fetchedThreads[0].id);
      }
    } catch (error) {
      console.error("Failed to load threads:", error);
    }
  }, [autoSelectFirstThread, currentThreadId, selectThread]);

  const handleNewChat = useCallback(async (callbacks?: { onCreated?: () => void }) => {
    try {
      const newThread = await api.createThread("New Chat");
      setThreads((current) => [newThread, ...current]);
      selectThread(newThread.id, "push");
      callbacks?.onCreated?.();
    } catch (error) {
      console.error("Failed to create thread:", error);
    }
  }, [selectThread]);

  const handleSelectThread = useCallback((threadId: string, callbacks?: { onSelected?: () => void }) => {
    selectThread(threadId, "push");
    callbacks?.onSelected?.();
  }, [selectThread]);

  const handleDeleteThread = useCallback(async (threadId: string) => {
    try {
      await api.deleteThread(threadId);
      setThreads((current) => {
        const remaining = current.filter((thread) => thread.id !== threadId);
        if (currentThreadId === threadId) {
          selectThread(remaining[0]?.id ?? null);
        }
        return remaining;
      });
    } catch (error) {
      console.error("Failed to delete thread:", error);
    }
  }, [currentThreadId, selectThread]);

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
