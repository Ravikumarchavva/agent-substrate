"use client";

import { useCallback, useEffect, useState } from "react";
import { TaskList } from "@/types";
import { api } from "@/lib/api";

export function useTaskBoards(conversationId: string | null) {
  // agentId → TaskList
  const [boards, setBoards] = useState<Map<string, TaskList>>(new Map());

  // Seed from REST on thread change
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!conversationId) {
        if (!cancelled) setBoards(new Map());
        return;
      }
      const lists = await api.getBoards(conversationId);
      if (cancelled) return;
      const map = new Map<string, TaskList>();
      for (const tl of lists) map.set(tl.agent_id, tl);
      setBoards(map);
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const upsertBoard = useCallback((taskList: TaskList) => {
    setBoards((prev) => {
      const next = new Map(prev);
      next.set(taskList.agent_id, taskList);
      return next;
    });
  }, []);

  const clearBoards = useCallback(() => setBoards(new Map()), []);

  return { boards, upsertBoard, clearBoards };
}
