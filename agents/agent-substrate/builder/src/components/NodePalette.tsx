"use client";

import { type DragEvent } from "react";
import { PanelLeftClose } from "lucide-react";
import { PALETTE, type PaletteCategory, type PaletteItem } from "@/features/pipeline/node_palletes/node_palletes";


function onDragStart(e: DragEvent<HTMLDivElement>, nodeType: string) {
  e.dataTransfer.setData("application/reactflow-type", nodeType);
  e.dataTransfer.effectAllowed = "move";
}

function PaletteEntry({ item }: { item: PaletteItem }) {
  const Icon = item.icon;
  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, item.type)}
      title={item.desc}
      className="group flex items-center gap-3 p-1 mx-2 rounded-lg cursor-grab hover:bg-(--bg-elevated) transition-colors"
    >
      <div
        className="shrink-0 flex items-center justify-center w-8 h-8 rounded-lg"
        style={{ background: item.color + "22", color: item.color }}
      >
        <Icon size={15} />
      </div>
      <span className="text-sm font-medium text-(--text) truncate">{item.label}</span>
    </div>
  );
}

function Section({ cat }: { cat: PaletteCategory }) {
  return (
    <div>
      <div className="p-1 text-[10px] font-semibold tracking-widest uppercase text-(--text-dim)">
        {cat.heading}
      </div>
      <div className="flex flex-col">
        {cat.items.map((item) => (
          <PaletteEntry key={item.type} item={item} />
        ))}
      </div>
    </div>
  );
}

interface NodePaletteProps {
  onCollapse?: () => void;
}

export function NodePalette({ onCollapse }: NodePaletteProps) {
  return (
    <aside className="w-56 shrink-0 border-r border-(--border) bg-(--bg-surface) flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-3 h-8 border-b border-(--border)">
        <span className="text-sm font-semibold text-(--text)">Nodes</span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="p-1 text-(--text-dim) hover:bg-(--bg-elevated) rounded-lg transition-colors"
            aria-label="Collapse"
            title="Collapse"
          >
            <PanelLeftClose size={24} className="hover:cursor-pointer" />
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        {PALETTE.map((cat) => (
          <Section key={cat.heading} cat={cat} />
        ))}
      </div>
    </aside>
  );
}