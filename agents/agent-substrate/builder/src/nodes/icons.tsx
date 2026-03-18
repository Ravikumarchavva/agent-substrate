/* ── Shared icons for node components — powered by lucide-react ───────── */

import {
  Bot,
  Wrench,
  BookOpen,
  ShieldCheck,
  Layers,
  Database,
  Play,
  Square,
  StickyNote,
  GitBranch,
  RadioTower,
  UserCheck,
  FileSearch,
  Monitor,
  RefreshCw,
} from "lucide-react";

export function AgentIcon({ size = 16 }: { size?: number }) {
  return <Bot size={size} />;
}

export function ToolIcon({ size = 16 }: { size?: number }) {
  return <Wrench size={size} />;
}

export function SkillIcon({ size = 16 }: { size?: number }) {
  return <BookOpen size={size} />;
}

export function GuardrailIcon({ size = 16 }: { size?: number }) {
  return <ShieldCheck size={size} />;
}

export function RouterIcon({ size = 16 }: { size?: number }) {
  return <Layers size={size} />;
}

export function MemoryIcon({ size = 16 }: { size?: number }) {
  return <Database size={size} />;
}

export function StartIcon({ size = 16 }: { size?: number }) {
  return <Play size={size} />;
}

export function EndIcon({ size = 16 }: { size?: number }) {
  return <Square size={size} />;
}

export function NoteIcon({ size = 16 }: { size?: number }) {
  return <StickyNote size={size} />;
}

export function ConditionIcon({ size = 16 }: { size?: number }) {
  return <GitBranch size={size} />;
}

export function ClassifyIcon({ size = 16 }: { size?: number }) {
  return <RadioTower size={size} />;
}

export function ApprovalIcon({ size = 16 }: { size?: number }) {
  return <UserCheck size={size} />;
}

export function FileSearchIcon({ size = 16 }: { size?: number }) {
  return <FileSearch size={size} />;
}

export function McpIcon({ size = 16 }: { size?: number }) {
  return <Monitor size={size} />;
}

export function WhileIcon({ size = 16 }: { size?: number }) {
  return <RefreshCw size={size} />;
}
