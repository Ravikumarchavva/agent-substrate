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
  WhileIcon,
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
      { type: "agent",    label: "Agent",    desc: "ReAct reasoning agent",   icon: AgentIcon,    color: "#a3a3a3", tag: "Core" },
      { type: "router",   label: "Classify", desc: "Route by classification", icon: ClassifyIcon, color: "#06b6d4", tag: "Core" },
      { type: "end",      label: "End",      desc: "Terminate flow",          icon: EndIcon,      color: "#22c55e", tag: "Core" },
      { type: "note",     label: "Note",     desc: "Sticky note annotation",  icon: NoteIcon,     color: "#c4a235", tag: "Utility" },
    ],
  },
  {
    heading: "Tools",
    items: [
      { type: "tool",      label: "Tool",       desc: "Callable tool / function",   icon: ToolIcon,      color: "#22c55e", tag: "Action" },
      { type: "guardrail", label: "Guardrail",  desc: "Input/output validation",    icon: GuardrailIcon, color: "#f59e0b", tag: "Safety" },
      { type: "skill",     label: "Skill",      desc: "Injected skill prompt",      icon: SkillIcon,     color: "#8b5cf6", tag: "Prompt" },
      { type: "mcp",       label: "MCP Server", desc: "External MCP tool server",   icon: McpIcon,       color: "#a855f7", tag: "App" },
    ],
  },
  {
    heading: "Logic",
    items: [
      { type: "condition", label: "If / else",    desc: "Expression-based routing",    icon: ConditionIcon, color: "#f59e0b", tag: "Logic" },
      { type: "while",     label: "While",        desc: "Repeat until condition false", icon: WhileIcon,     color: "#f59e0b", tag: "Logic" },
      { type: "approval",  label: "User approval",desc: "Approve / reject gate",       icon: ApprovalIcon,  color: "#f97316", tag: "Review" },
    ],
  },
  {
    heading: "Data",
    items: [
      { type: "memory", label: "Memory", desc: "Session memory backend", icon: MemoryIcon, color: "#ec4899", tag: "State" },
    ],
  },
];

export { PALETTE, type PaletteCategory, type PaletteItem };