"use client";

import { useState, useCallback } from "react";
import type { AppPanelItem } from "@/components/AppPanel";

export function useAppPanel() {
  const [panelItems, setPanelItems] = useState<AppPanelItem[]>([]);
  const [activePanelId, setActivePanelId] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  const openInPanel = useCallback((item: AppPanelItem) => {
    setPanelItems((prev) => {
      // Dedup: MCP apps by toolName; file artifacts by their file path
      // (fileUrl) — all files share the generic "code_interpreter" toolName,
      // so re-opening the SAME file replaces its tab (and updates fileUrl so
      // the viewer remounts on a changed version), while a DIFFERENT file
      // opens a new tab.
      const existing =
        item.kind === "file"
          ? prev.findIndex((p) => p.kind === "file" && p.fileName === item.fileName)
          : prev.findIndex((p) => p.kind !== "file" && p.toolName === item.toolName);
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
