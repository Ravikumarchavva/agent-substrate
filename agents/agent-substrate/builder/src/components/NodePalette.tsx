"use client";

import { type DragEvent, useState } from "react";
import { ChevronRight, PanelLeftClose } from "lucide-react";
import {
  AgentIcon,
  ToolIcon,
  SkillIcon,
  GuardrailIcon,
  ClassifyIcon,
  MemoryIcon,
  StartIcon,
  EndIcon,
  NoteIcon,
  ConditionIcon,
  ApprovalIcon,
  McpIcon,
} from "@/nodes/icons";

interface PaletteItem {
  type: string;
  label: string;
  desc: string;
  icon: React.FC<{ size?: number }>;
  color: string;
  tag: string;
}

interface PaletteCategory {
  heading: string;
  items: PaletteItem[];
}

const PALETTE: PaletteCategory[] = [
  {
    heading: "Core",
    items: [
      { type: "start",    label: "Start",    desc: "Entry point",             icon: StartIcon,    color: "#22c55e", tag: "Core" },
      { type: "agent",    label: "Agent",    desc: "ReAct reasoning agent",   icon: AgentIcon,    color: "#6366f1", tag: "Core" },
      { type: "router",   label: "Classify", desc: "Route by classification", icon: ClassifyIcon, color: "#06b6d4", tag: "Core" },
      { type: "end",      label: "End",      desc: "Terminate flow",          icon: EndIcon,      color: "#22c55e", tag: "Core" },
      { type: "note",     label: "Note",     desc: "Sticky note annotation",  icon: NoteIcon,     color: "#c4a235", tag: "Utility" },
    ],
  },
  {
    heading: "Tools",
    items: [
      { type: "tool",      label: "Tool",      desc: "Callable tool / function", icon: ToolIcon,      color: "#22c55e", tag: "Action" },
      { type: "guardrail", label: "Guardrail", desc: "Input/output validation",  icon: GuardrailIcon, color: "#f59e0b", tag: "Safety" },
      { type: "skill",     label: "MCP / Skill",desc: "Injected skill prompt",   icon: McpIcon,       color: "#a855f7", tag: "App" },
    ],
  },
  {
    heading: "Logic",
    items: [
      { type: "condition", label: "If / else",    desc: "Expression-based routing", icon: ConditionIcon, color: "#22c55e", tag: "Logic" },
      { type: "approval",  label: "User approval",desc: "Approve / reject gate",    icon: ApprovalIcon,  color: "#f97316", tag: "Review" },
    ],
  },
  {
    heading: "Data",
    items: [
      { type: "memory", label: "Memory", desc: "Session memory backend", icon: MemoryIcon, color: "#ec4899", tag: "State" },
    ],
  },
];

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
      className="group flex items-center gap-2.5 px-2.5 py-2 rounded-md border border-(--border) bg-(--bg-surface) cursor-grab hover:bg-(--bg-hover) transition-colors"
    >
      <div
        className="shrink-0 flex items-center justify-center w-7 h-7 rounded-md"
        style={{ background: item.color + "14", color: item.color }}
      >
        <Icon size={15} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-(--text) truncate">{item.label}</div>
        <div className="text-[10px] text-(--text-dim) truncate">{item.desc}</div>
      </div>
      <span className="shrink-0 text-[8px] font-medium tracking-wider uppercase text-(--text-dim) px-1.5 py-0.5 rounded bg-(--bg-elevated)">{item.tag}</span>
    </div>
  );
}

function Section({ cat }: { cat: PaletteCategory }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border-b border-(--border) last:border-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-[10px] font-semibold tracking-wider uppercase text-(--text-muted) hover:bg-(--bg-hover) transition-colors"
      >
        <ChevronRight className={`w-3.5 h-3.5 text-(--text-dim) transition-transform duration-200 ${open ? "rotate-90" : ""}`} />
        {cat.heading}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-1.5">
          {cat.items.map((item) => (
            <PaletteEntry key={item.type} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

interface NodePaletteProps {
  onCollapse?: () => void;
}

export function NodePalette({ onCollapse }: NodePaletteProps) {
  return (
    <aside className="w-64 shrink-0 border-r border-(--border) bg-(--bg) flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-(--border)">
        <span className="text-xs font-semibold text-(--text)">Blocks</span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="p-1 text-(--text-dim) hover:bg-(--bg-hover) rounded transition-colors"
            aria-label="Collapse"
            title="Collapse"
          >
            <PanelLeftClose size={14} />
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