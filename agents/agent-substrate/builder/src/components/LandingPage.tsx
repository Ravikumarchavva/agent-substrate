/* ── LandingPage — Workflow builder home ─────────────────────────────── */
"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { PipelineOut, PipelineConfig } from "@/types";
import { ThemeToggle } from "@/components/ThemeToggle";
import styles from "./LandingPage.module.css";

/* ── Template definitions ────────────────────────────────────────────── */

interface Template {
  name: string;
  description: string;
  config: PipelineConfig;
}

const TEMPLATES = [
  {
    name: "Simple assistant",
    description: "Single agent with memory — the Hello World of agentic workflows",
    config: {
      id: "", name: "Simple assistant", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Assistant", position: { x: 250, y: 188 }, config: { model: "gpt-4o-mini", system_prompt: "You are a helpful assistant.", max_iterations: 10 } },
        { id: "memory_1", node_type: "memory", label: "Memory", position: { x: 500, y: 260 }, config: { backend: "unbounded" } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "memory_1", source_handle: "", target_handle: "", edge_type: "agent_memory", label: "" },
      ],
    },
  },
  {
    name: "Research agent",
    description: "Agent with web search tool and content safety guardrails",
    config: {
      id: "", name: "Research agent", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "guard_1", node_type: "guardrail", label: "Content filter", position: { x: 250, y: 100 }, config: { guardrail_type: "input", schema_name: "ContentSafetyJudge", pass_field: "is_safe", tripwire: true } },
        { id: "agent_1", node_type: "agent", label: "Researcher", position: { x: 250, y: 230 }, config: { model: "gpt-4o-mini", system_prompt: "You are a research assistant. Search the web for information and provide well-sourced answers.", max_iterations: 15 } },
        { id: "tool_1", node_type: "tool", label: "Web search", position: { x: 500, y: 230 }, config: { tool_name: "web_search", risk: "safe" } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "guard_1", source_handle: "", target_handle: "", edge_type: "agent_guardrail", label: "" },
        { id: "e3", source: "agent_1", target: "tool_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
      ],
    },
  },
  {
    name: "Customer service",
    description: "Classify intent then route to specialized sub-agents",
    config: {
      id: "", name: "Customer service", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 250 }, config: {} },
        { id: "guard_1", node_type: "guardrail", label: "Jailbreak guard", position: { x: 250, y: 200 }, config: { guardrail_type: "input", schema_name: "ContentSafetyJudge", pass_field: "is_safe", tripwire: true } },
        { id: "agent_1", node_type: "agent", label: "Classifier", position: { x: 470, y: 250 }, config: { model: "gpt-4o-mini", system_prompt: "Classify the user intent into: billing, technical, general.", max_iterations: 3 } },
        { id: "cond_1", node_type: "condition", label: "Condition", position: { x: 700, y: 200 }, config: { conditions: [{ expression: 'intent == "billing"', label: "Billing" }, { expression: 'intent == "technical"', label: "Technical" }] } },
        { id: "agent_2", node_type: "agent", label: "Billing agent", position: { x: 980, y: 120 }, config: { model: "gpt-4o-mini", system_prompt: "You help with billing inquiries.", max_iterations: 10 } },
        { id: "agent_3", node_type: "agent", label: "Tech support", position: { x: 980, y: 250 }, config: { model: "gpt-4o-mini", system_prompt: "You provide technical support.", max_iterations: 10 } },
        { id: "agent_4", node_type: "agent", label: "General agent", position: { x: 980, y: 380 }, config: { model: "gpt-4o-mini", system_prompt: "You handle general queries.", max_iterations: 10 } },
        { id: "end_1", node_type: "end", label: "End", position: { x: 250, y: 350 }, config: {} },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "guard_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "guard_1", target: "agent_1", source_handle: "pass", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "guard_1", target: "end_1", source_handle: "fail", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "agent_1", target: "cond_1", source_handle: "", target_handle: "", edge_type: "router_input", label: "" },
        { id: "e5", source: "cond_1", target: "agent_2", source_handle: "cond-0", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e6", source: "cond_1", target: "agent_3", source_handle: "cond-1", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e7", source: "cond_1", target: "agent_4", source_handle: "else", target_handle: "", edge_type: "router_route", label: "" },
      ],
    },
  },
  {
    name: "Data enrichment",
    description: "Pull together data to answer user questions with tools",
    config: {
      id: "", name: "Data enrichment", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Data agent", position: { x: 260, y: 188 }, config: { model: "gpt-4o-mini", system_prompt: "You enrich data by using available tools. Answer questions fully.", max_iterations: 15 } },
        { id: "tool_1", node_type: "tool", label: "Web search", position: { x: 520, y: 130 }, config: { tool_name: "web_search", risk: "safe" } },
        { id: "tool_2", node_type: "tool", label: "File search", position: { x: 520, y: 250 }, config: { tool_name: "file_search", risk: "safe" } },
        { id: "memory_1", node_type: "memory", label: "Memory", position: { x: 520, y: 340 }, config: { backend: "redis", ttl: 3600 } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "tool_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "agent_1", target: "tool_2", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "agent_1", target: "memory_1", source_handle: "", target_handle: "", edge_type: "agent_memory", label: "" },
      ],
    },
  },
  {
    name: "Planning helper",
    description: "Multi-turn agent for creating work plans with approval gates",
    config: {
      id: "", name: "Planning helper", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Planner", position: { x: 260, y: 188 }, config: { model: "gpt-4o", system_prompt: "You create detailed work plans. Always present the plan for approval before finalizing.", max_iterations: 20 } },
        { id: "approval_1", node_type: "approval", label: "Review plan", position: { x: 520, y: 170 }, config: { prompt: "Does this plan look good?" } },
        { id: "agent_2", node_type: "agent", label: "Finalizer", position: { x: 760, y: 130 }, config: { model: "gpt-4o-mini", system_prompt: "Format the approved plan into a clean document.", max_iterations: 5 } },
        { id: "end_1", node_type: "end", label: "End", position: { x: 760, y: 270 }, config: {} },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "approval_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "approval_1", target: "agent_2", source_handle: "approve", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "approval_1", target: "end_1", source_handle: "reject", target_handle: "", edge_type: "agent_tool", label: "" },
      ],
    },
  },
  {
    name: "Document comparison",
    description: "Analyze and highlight differences across uploaded documents",
    config: {
      id: "", name: "Document comparison", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Triage", position: { x: 260, y: 188 }, config: { model: "gpt-4o-mini", system_prompt: "Classify whether the user wants to compare or ask questions.", max_iterations: 5 } },
        { id: "cond_1", node_type: "condition", label: "If / else", position: { x: 480, y: 170 }, config: { conditions: [{ expression: 'task == "compare"', label: "Compare" }] } },
        { id: "agent_2", node_type: "agent", label: "Comparison agent", position: { x: 740, y: 130 }, config: { model: "gpt-4o", system_prompt: "Compare the provided documents and highlight key differences.", max_iterations: 15 } },
        { id: "agent_3", node_type: "agent", label: "Q&A agent", position: { x: 740, y: 280 }, config: { model: "gpt-4o-mini", system_prompt: "Answer questions about the documents.", max_iterations: 10 } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "cond_1", source_handle: "", target_handle: "", edge_type: "router_input", label: "" },
        { id: "e3", source: "cond_1", target: "agent_2", source_handle: "cond-0", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e4", source: "cond_1", target: "agent_3", source_handle: "else", target_handle: "", edge_type: "router_route", label: "" },
      ],
    },
  },
] as const satisfies readonly Template[];

/* ── Per-template icon mapping ────────────────────────────────────────── */

interface TemplateMeta {
  icon: ReactNode;
  color: string;
  tag: string;
}

const TEMPLATE_META: Record<string, TemplateMeta> = {
  "Simple assistant": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
    ),
    color: "#8b5cf6",
    tag: "Starter",
  },
  "Research agent": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
    ),
    color: "#22c55e",
    tag: "Research",
  },
  "Customer service": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 6.1A9 9 0 0 1 21 13v1a2 2 0 0 1-2 2h-1.2a2 2 0 0 1-1.8-1.1l-1-2A2 2 0 0 1 16.8 10H18"/>
        <path d="M7 17.9A9 9 0 0 1 3 11V10a2 2 0 0 1 2-2h1.2A2 2 0 0 1 8 9.1l1 2A2 2 0 0 1 7.2 14H6"/>
        <path d="M8 21h8"/>
      </svg>
    ),
    color: "#06b6d4",
    tag: "Routing",
  },
  "Data enrichment": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
      </svg>
    ),
    color: "#a855f7",
    tag: "Data",
  },
  "Planning helper": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
        <path d="M9 16l2 2 4-4"/>
      </svg>
    ),
    color: "#f97316",
    tag: "Planning",
  },
  "Document comparison": {
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
        <line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>
      </svg>
    ),
    color: "#ec4899",
    tag: "Analysis",
  },
};

const DEFAULT_META: TemplateMeta = {
  icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>
    </svg>
  ),
  color: "#6366f1",
  tag: "Template",
};

interface LandingPageProps {
  onCreateBlank: () => void;
  onLoadTemplate: (template: Template["config"]) => void;
  onLoadPipeline: (pipeline: PipelineOut) => void;
}

interface SectionHeaderProps {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}

function SectionHeader({ eyebrow, title, description, action }: SectionHeaderProps) {
  return (
    <div className={styles.sectionHeader}>
      <div>
        <p className={styles.sectionEyebrow}>
          {eyebrow}
        </p>
        <h2 className={styles.sectionTitle}>
          {title}
        </h2>
        <p className={styles.sectionDescription}>
          {description}
        </p>
      </div>
      {action ? <div className={styles.sectionAction}>{action}</div> : null}
    </div>
  );
}

function HeroPreviewCard() {
  return (
    <aside className={styles.previewCard} aria-label="Workflow preview">
      <div className={styles.previewGlow} />
      <div className={styles.previewContent}>
        <div className={styles.previewHeader}>
          <div>
            <p className={styles.previewOverline}>
              Workflow preview
            </p>
            <h3 className={styles.previewTitle}>
              Research assistant pipeline
            </h3>
          </div>
          <div className={styles.previewBadge}>
            Ready to run
          </div>
        </div>

        <div className={styles.previewMetrics}>
          {[
            { label: "Nodes", value: "6" },
            { label: "Latency", value: "~2.4s" },
            { label: "Tools", value: "3" },
            { label: "Guardrails", value: "1" },
          ].map((item) => (
            <div key={item.label} className={styles.previewMetric}>
              <div className={styles.previewMetricLabel}>{item.label}</div>
              <div className={styles.previewMetricValue}>{item.value}</div>
            </div>
          ))}
        </div>

        <div className={styles.previewFlow}>
          <div className={styles.previewFlowTitle}>
            <span className={styles.previewFlowDot} />
            Visual execution path
          </div>
          <div className={styles.previewNodes}>
            {[
              { name: "Start", color: "#22c55e" },
              { name: "Research agent", color: "#6366f1" },
              { name: "Web search", color: "#22c55e" },
              { name: "Guardrail", color: "#f59e0b" },
            ].map((node, index) => (
              <div key={node.name} className={styles.previewNodeRow}>
                <div className={styles.previewNodeTrack}>
                  <span className={styles.previewNodePin} style={{ background: node.color, boxShadow: `0 0 0 6px ${node.color}1f` }} />
                  {index < 3 && <span className={styles.previewNodeLine} />}
                </div>
                <div className={styles.previewNodeCard}>
                  <div className={styles.previewNodeTitle}>{node.name}</div>
                  <div className={styles.previewNodeHint}>Configured and connected</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
}

function TemplateCard({
  template,
  onClick,
}: {
  template: Template;
  onClick: () => void;
}) {
  const meta = TEMPLATE_META[template.name] ?? DEFAULT_META;

  return (
    <article className="h-full">
      <button
        onClick={onClick}
        className={`${styles.cardButton} ${styles.templateCard}`}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = meta.color;
          e.currentTarget.style.transform = "translateY(-2px)";
          e.currentTarget.style.boxShadow = `0 14px 38px ${meta.color}1f`;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--border)";
          e.currentTarget.style.transform = "translateY(0)";
          e.currentTarget.style.boxShadow = "none";
        }}
      >
        <div className={styles.cardTop}>
          <div className={styles.cardIcon} style={{ background: `${meta.color}18`, color: meta.color }}>
            {meta.icon}
          </div>
          <span className={styles.cardTag} style={{ background: `${meta.color}15`, color: meta.color }}>
            {meta.tag}
          </span>
        </div>

        <div className={styles.cardBody}>
          <h3 className={styles.cardTitle}>
            {template.name}
          </h3>
          <p className={styles.cardText}>
            {template.description}
          </p>
        </div>

        <div className={styles.cardMeta}>
          <span className={styles.metaPill}>
            {template.config.nodes.length} nodes
          </span>
          <span className={styles.metaPill}>
            {template.config.edges.length} edges
          </span>
        </div>

        <div className={styles.cardFooter}>
          <div className={styles.inlineButton}>
            <span>Use template</span>
            <span style={{ color: meta.color }}>→</span>
          </div>
        </div>
      </button>
    </article>
  );
}

function SavedWorkflowCard({ pipeline, onClick }: { pipeline: PipelineOut; onClick: () => void }) {
  return (
    <article className="h-full">
      <button
        onClick={onClick}
        className={`${styles.cardButton} ${styles.savedCard}`}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = "var(--accent)";
          e.currentTarget.style.transform = "translateY(-2px)";
          e.currentTarget.style.boxShadow = "0 14px 38px rgba(99,102,241,0.16)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--border)";
          e.currentTarget.style.transform = "translateY(0)";
          e.currentTarget.style.boxShadow = "none";
        }}
      >
        <div className={styles.cardTop}>
          <div className={styles.cardIcon} style={{ background: "rgba(99,102,241,0.14)", color: "var(--accent)" }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>
            </svg>
          </div>
          <span className={styles.cardTag} style={{ background: "var(--bg-elevated)", color: "var(--text-dim)" }}>
            Saved
          </span>
        </div>

        <div className={styles.cardBody}>
          <h3 className={styles.cardTitle}>
            {pipeline.name}
          </h3>
          <time dateTime={pipeline.updated_at} className={styles.cardText}>
            Updated {new Date(pipeline.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
          </time>
        </div>

        <div className={styles.cardFooter}>
          <div className={styles.inlineButton}>
            <span>Open workflow</span>
            <span style={{ color: "var(--accent)" }}>→</span>
          </div>
        </div>
      </button>
    </article>
  );
}

export function LandingPage({ onCreateBlank, onLoadTemplate, onLoadPipeline }: LandingPageProps) {
  const [saved, setSaved] = useState<PipelineOut[]>([]);
  const [search, setSearch] = useState("");
  const templatesRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    api.listPipelines().then(setSaved).catch(() => {});
  }, []);

  const filteredTemplates = useMemo(
    () =>
      TEMPLATES.filter(
        (template) =>
          !search ||
          template.name.toLowerCase().includes(search.toLowerCase()) ||
          template.description.toLowerCase().includes(search.toLowerCase())
      ),
    [search]
  );

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.brand}>
            <div className={styles.brandMark}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>
              </svg>
            </div>
            <div className={styles.brandCopy}>
              <div className={styles.brandRow}>
                <span className={styles.brandTitle}>Agent Builder</span>
                <span className={styles.beta}>Beta</span>
              </div>
              <p className={styles.brandSubtitle}>Design agent workflows with clean architecture</p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className={styles.main}>
        <div className={styles.container}>
          <section className={styles.hero}>
            <div className={styles.heroContent}>
              <div className={styles.eyebrow}>
                <span className={styles.eyebrowDot} />
                Visual workflow builder
              </div>

              <h1 className={styles.heroTitle}>
                Build agent systems that feel
                <span className={styles.heroTitleAccent}> production-ready</span>
              </h1>

              <p className={styles.heroText}>
                Start from a proven template or create a blank canvas. Compose agents, tools, memory, guardrails, routing, and approvals with a layout that scales from prototypes to real workflows.
              </p>

              <div className={styles.actions}>
                <button
                  onClick={onCreateBlank}
                  className={styles.primaryButton}
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  Create blank workflow
                </button>
                <button
                  onClick={() => templatesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  className={styles.secondaryButton}
                >
                  Browse templates
                  <span>↓</span>
                </button>
              </div>

              <div className={styles.featureGrid}>
                {[
                  { icon: "⚡", label: "Real-time runs", hint: "stream responses while testing" },
                  { icon: "🧠", label: "Memory aware", hint: "wire stateful memory into flows" },
                  { icon: "🛡️", label: "Guardrails", hint: "add validation and safety checks" },
                  { icon: "✅", label: "Approvals", hint: "insert human checkpoints" },
                ].map((item) => (
                  <div key={item.label} className={styles.featurePill}>
                    <div className={styles.featureIcon}>{item.icon}</div>
                    <div>
                      <div className={styles.featureLabel}>{item.label}</div>
                      <div className={styles.featureHint}>{item.hint}</div>
                    </div>
                  </div>
                ))}
              </div>

              <div className={styles.statsRow}>
                {[
                  { label: "Templates", value: String(TEMPLATES.length), hint: "ready-made starting points" },
                  { label: "Saved workflows", value: String(saved.length), hint: "continue where you left off" },
                  { label: "Core blocks", value: "11", hint: "agents, tools, routers, memory" },
                ].map((item) => (
                  <div key={item.label} className={styles.statCard}>
                    <div className={styles.statLabel}>{item.label}</div>
                    <div className={styles.statValue}>{item.value}</div>
                    <div className={styles.statHint}>{item.hint}</div>
                  </div>
                ))}
              </div>
            </div>

            <HeroPreviewCard />
          </section>

          <section className={styles.rail}>
            {[
              { title: "Start clean", text: "Use a blank canvas when you already know the architecture.", color: "rgba(99,102,241,0.14)" },
              { title: "Move faster", text: "Begin with curated templates for research, routing, memory, and approvals.", color: "rgba(34,197,94,0.14)" },
              { title: "Resume instantly", text: "Saved workflows stay one click away so iteration stays fast.", color: "rgba(249,115,22,0.14)" },
            ].map((card) => (
              <article key={card.title} className={styles.railCard} style={{ borderColor: "var(--border)", background: card.color }}>
                <h2 className={styles.railCardTitle}>{card.title}</h2>
                <p className={styles.railCardText}>{card.text}</p>
              </article>
            ))}
          </section>

          <section ref={templatesRef} className={styles.section} aria-labelledby="templates-heading">
            <SectionHeader
              eyebrow="Templates"
              title="Choose a strong starting point"
              description="Each template is designed around a real workflow pattern so you can focus on logic instead of setup."
              action={
                <div className={styles.searchWrap}>
                  <svg className={styles.searchIcon} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                  <input
                    type="search"
                    aria-label="Search templates"
                    placeholder="Search templates"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className={styles.searchInput}
                  />
                </div>
              }
            />

            <div className={styles.templateGrid}>
              <article className="h-full">
                <button
                  onClick={onCreateBlank}
                  className={`${styles.cardButton} ${styles.blankCard}`}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "var(--accent)";
                    e.currentTarget.style.boxShadow = "0 14px 38px rgba(99,102,241,0.14)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.boxShadow = "none";
                  }}
                >
                  <div className={styles.cardIcon} style={{ background: "var(--bg-elevated)", color: "var(--accent)" }}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                  </div>
                  <div className={styles.cardBody}>
                    <h3 className={styles.cardTitle}>Blank workflow</h3>
                    <p className={styles.cardText}>Start from scratch and model the exact system you want with no preset structure.</p>
                  </div>
                  <div className={styles.cardFooter}>
                    <div className={styles.inlineButton}>
                      <span>Start from scratch</span>
                      <span style={{ color: "var(--accent)" }}>→</span>
                    </div>
                  </div>
                </button>
              </article>

              {filteredTemplates.map((template) => (
                <TemplateCard key={template.name} template={template} onClick={() => onLoadTemplate(template.config)} />
              ))}
            </div>

            {filteredTemplates.length === 0 && (
              <div className={styles.emptyState}>
                No templates match “{search}”.
              </div>
            )}
          </section>

          {saved.length > 0 && (
            <section className={styles.section} aria-labelledby="saved-heading">
              <SectionHeader
                eyebrow="Saved workflows"
                title="Continue your latest work"
                description="Open an existing workflow and keep iterating without losing your context."
              />
              <div className={styles.savedGrid}>
                {saved.map((pipeline) => (
                  <SavedWorkflowCard key={pipeline.id} pipeline={pipeline} onClick={() => onLoadPipeline(pipeline)} />
                ))}
              </div>
            </section>
          )}

          <div className="h-16" />
        </div>
      </main>
    </div>
  );
}

export { TEMPLATES };
export type { Template };

