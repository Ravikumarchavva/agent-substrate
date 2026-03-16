/* ── NodePalette — categorised draggable sidebar ─────────────────────────
 *
 * OpenAI-style palette with sections: Core · Tools · Logic · Data.
 * Users drag items onto the React Flow canvas via HTML5 DnD.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { type DragEvent, useState } from "react";
import { PanelLeftClose } from "lucide-react";
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
import styles from "./NodePalette.module.css";

/* ── Category schema ─────────────────────────────────────────────────── */

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

/* ── DnD helper ──────────────────────────────────────────────────────── */

function onDragStart(e: DragEvent<HTMLDivElement>, nodeType: string) {
  e.dataTransfer.setData("application/reactflow-type", nodeType);
  e.dataTransfer.effectAllowed = "move";
}

/* ── Single palette item ─────────────────────────────────────────────── */

function PaletteEntry({ item }: { item: PaletteItem }) {
  const Icon = item.icon;
  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, item.type)}
      className={styles.entry}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = `${item.color}55`;
        e.currentTarget.style.boxShadow = `0 12px 28px ${item.color}14`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--border)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      <div
        className={styles.iconWrap}
        style={{ background: `${item.color}20`, color: item.color }}
      >
        <Icon size={18} />
      </div>
      <div className={styles.copy}>
        <div className={styles.labelRow}>
          <div className={styles.label}>{item.label}</div>
          <span className={styles.badge}>{item.tag}</span>
        </div>
        <div className={styles.desc}>
          {item.desc}
        </div>
      </div>
    </div>
  );
}

/* ── Collapsible section ─────────────────────────────────────────────── */

function Section({ cat }: { cat: PaletteCategory }) {
  const [open, setOpen] = useState(true);
  return (
    <div className={styles.section}>
      <button
        onClick={() => setOpen(!open)}
        className={styles.sectionButton}
      >
        <span className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`}>›</span>
        {cat.heading}
      </button>
      {open && (
        <div className={styles.entries}>
          {cat.items.map((item) => (
            <PaletteEntry key={item.type} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Main component ──────────────────────────────────────────────────── */

interface NodePaletteProps {
  onCollapse?: () => void;
}

export function NodePalette({ onCollapse }: NodePaletteProps) {
  return (
    <aside
      className={styles.sidebar}
      style={{ borderColor: "var(--border)" }}
    >
      <div className={styles.header} style={{ borderColor: "var(--border)" }}>
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className={styles.eyebrow}>
              Workflow blocks
            </div>
            <p className={styles.description}>
              Drag blocks onto the canvas to build the flow.
            </p>
          </div>
          {onCollapse ? (
            <button
              onClick={onCollapse}
              className={styles.collapseButton}
              style={{ borderColor: "var(--border)" }}
              aria-label="Collapse workflow blocks"
              title="Collapse workflow blocks"
            >
              <PanelLeftClose size={15} />
            </button>
          ) : null}
        </div>
      </div>
      {PALETTE.map((cat) => (
        <Section key={cat.heading} cat={cat} />
      ))}
    </aside>
  );
}
