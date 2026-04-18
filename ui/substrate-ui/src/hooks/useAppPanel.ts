"use client";

import { useState, useCallback } from "react";
import type { AppPanelItem } from "@/components/AppPanel";

export function useAppPanel() {
  const [panelItems, setPanelItems] = useState<AppPanelItem[]>([]);
  const [activePanelId, setActivePanelId] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  const openInPanel = useCallback((item: AppPanelItem) => {
    setPanelItems((prev) => {
      // Replace existing item with same toolName, or add new
      const existing = prev.findIndex((p) => p.toolName === item.toolName);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = { ...item, id: prev[existing].id };
        return updated;
      }
      return [...prev, item];
    });
    setActivePanelId(item.id);
    setPanelCollapsed(false);
  }, []);

  const closePanelItem = useCallback((id: string) => {
    setPanelItems((prev) => {
      const filtered = prev.filter((i) => i.id !== id);
      if (activePanelId === id && filtered.length > 0) {
        setActivePanelId(filtered[filtered.length - 1].id);
      } else if (filtered.length === 0) {
        setActivePanelId(null);
      }
      return filtered;
    });
  }, [activePanelId]);

  const closeAllPanels = useCallback(() => {
    setPanelItems([]);
    setActivePanelId(null);
  }, []);

  return {
    panelItems,
    setPanelItems,
    activePanelId,
    setActivePanelId,
    panelCollapsed,
    setPanelCollapsed,
    openInPanel,
    closePanelItem,
    closeAllPanels,
  };
}
