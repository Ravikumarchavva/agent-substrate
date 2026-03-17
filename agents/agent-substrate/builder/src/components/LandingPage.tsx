"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { PipelineOut, PipelineConfig } from "@/types";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  ArrowRight, BoxSelect, Search, Zap, Brain,
  ShieldAlert, CheckSquare, Plus, ArrowDown, Activity,
} from "lucide-react";
import styles from "./LandingPage.module.css";

/*  Template data  */

interface Template {
  name: string;
  description: string;
  config: PipelineConfig;
}

const TEMPLATES: Template[] = [
  {
    name: "Simple assistant",
    description: "Single agent with memory \u2014 the Hello World of agentic workflows",
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
        { id: "agent_1", node_type: "agent", label: "Researcher", position: { x: 250, y: 230 }, config: { model: "gpt-4o-mini", system_prompt: "You are a research assistant.", max_iterations: 15 } },
        { id: "tool_1", node_type: "tool", label: "Web search", position: { x: 500, y: 230 }, config: { tool_name: "web_search", risk: "safe" } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "guard_1", source_handle: "", target_handle: "", edge_type: "agent_guardrail", label: "" },
        { id: "e3", source: "agent_1", target: "tool_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
      ],
    },
  },
];

interface TemplateMeta { icon: ReactNode; color: string; tag: string }

const DEFAULT_META: TemplateMeta = {
  icon: <BoxSelect className="w-5 h-5" />,
  color: "#6366f1",
  tag: "Template",
};

const TEMPLATE_META: Record<string, TemplateMeta> = {
  "Simple assistant": { icon: <BoxSelect className="w-5 h-5" />, color: "#8b5cf6", tag: "Starter" },
  "Research agent":   { icon: <Search className="w-5 h-5" />,    color: "#22c55e", tag: "Research" },
};

const FEATURES: { icon: ReactNode; label: string; hint: string }[] = [
  { icon: <Zap size={16} />,         label: "Real-time",  hint: "Stream testing" },
  { icon: <Brain size={16} />,       label: "Memory",     hint: "Stateful context" },
  { icon: <ShieldAlert size={16} />, label: "Guardrails", hint: "Safety validation" },
  { icon: <CheckSquare size={16} />, label: "Approvals",  hint: "Human in loop" },
];

const PREVIEW_NODES = [
  { name: "Start",          color: "#22c55e" },
  { name: "Research agent", color: "#6366f1" },
  { name: "Web search",     color: "#22c55e" },
  { name: "Guardrail",      color: "#f59e0b" },
];

/*  Sub-components  */

function TemplateCard({ template, onClick }: { template: Template; onClick: () => void }) {
  const meta = TEMPLATE_META[template.name] ?? DEFAULT_META;
  return (
    <button className={`${styles.cardButton} ${styles.templateCard}`} onClick={onClick} type="button">
      <div className={styles.cardTop}>
        <div className={styles.cardIcon} style={{ background: `${meta.color}16`, color: meta.color }}>
          {meta.icon}
        </div>
        <span
          className={styles.cardTag}
          style={{ color: meta.color, background: `${meta.color}14`, border: `1px solid ${meta.color}28` }}
        >
          {meta.tag}
        </span>
      </div>
      <div className={styles.cardBody}>
        <div className={styles.cardTitle}>{template.name}</div>
        <p className={styles.cardText}>{template.description}</p>
        <div className={styles.cardMeta}>
          <span className={styles.metaPill}>{template.config.nodes.length} nodes</span>
          <span className={styles.metaPill}>{template.config.edges.length} edges</span>
        </div>
      </div>
      <div className={styles.cardFooter}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 13, fontWeight: 600 }}>
          Use template <ArrowRight size={14} />
        </span>
      </div>
    </button>
  );
}

function SavedWorkflowCard({ pipeline, onClick }: { pipeline: PipelineOut; onClick: () => void }) {
  return (
    <button className={`${styles.cardButton} ${styles.savedCard}`} onClick={onClick} type="button">
      <div className={styles.cardTop}>
        <div className={styles.cardIcon} style={{ background: "rgba(99,102,241,0.1)", color: "var(--accent)" }}>
          <Activity size={20} />
        </div>
        <span className={styles.cardTag} style={{ color: "var(--text-dim)", background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
          Saved
        </span>
      </div>
      <div className={styles.cardBody}>
        <div className={styles.cardTitle}>{pipeline.name}</div>
        <p className={styles.cardText}>
          Updated {new Date(pipeline.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
        </p>
      </div>
      <div className={styles.cardFooter}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 13, fontWeight: 600 }}>
          Open workflow <ArrowRight size={14} />
        </span>
      </div>
    </button>
  );
}

/*  Main page  */

export function LandingPage({ onCreateBlank, onLoadTemplate, onLoadPipeline }: {
  onCreateBlank: () => void;
  onLoadTemplate: (template: Template["config"]) => void;
  onLoadPipeline: (pipeline: PipelineOut) => void;
}) {
  const [saved, setSaved] = useState<PipelineOut[]>([]);
  const [search, setSearch] = useState("");
  const templatesRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    api.listPipelines().then(setSaved).catch(() => {});
  }, []);

  const filteredTemplates = useMemo(
    () =>
      TEMPLATES.filter(
        (t) =>
          !search ||
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase()),
      ),
    [search],
  );

  return (
    <div className={styles.page}>
      {/*  Header  */}
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.brand}>
            <div className={styles.brandMark}>
              <BoxSelect size={20} />
            </div>
            <div className={styles.brandCopy}>
              <div className={styles.brandRow}>
                <span className={styles.brandTitle}>Agent Builder</span>
                <span className={styles.beta}>Beta</span>
              </div>
              <span className={styles.brandSubtitle}>Design agent workflows with clean architecture</span>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      {/*  Main scrollable area  */}
      <main className={styles.main}>
        <div className={styles.container}>

          {/*  Hero  */}
          <div className={styles.hero}>
            <div className={styles.heroContent}>
              <div className={styles.eyebrow}>
                <span className={styles.eyebrowDot} />
                Visual workflow builder
              </div>

              <h1 className={styles.heroTitle}>
                Design agent{"\n"}workflows{" "}
                <span className={styles.heroTitleAccent}>visually</span>
              </h1>

              <p className={styles.heroText}>
                Wire up agents, tools, guardrails, and memory
                in a visual canvas. Pick a template or start blank.
              </p>

              <div className={styles.actions}>
                <button className={styles.primaryButton} onClick={onCreateBlank} type="button">
                  <Plus size={16} /> Create blank workflow
                </button>
                <button
                  className={styles.secondaryButton}
                  onClick={() => templatesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  type="button"
                >
                  Browse templates <ArrowDown size={14} />
                </button>
              </div>

              <div className={styles.featureGrid}>
                {FEATURES.map((f) => (
                  <div key={f.label} className={styles.featurePill}>
                    <div className={styles.featureIcon}>{f.icon}</div>
                    <div>
                      <div className={styles.featureLabel}>{f.label}</div>
                      <div className={styles.featureHint}>{f.hint}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/*  Preview card  */}
            <div className={styles.previewCard}>
              <div className={styles.previewGlow} />
              <div className={styles.previewContent}>
                <div className={styles.previewHeader}>
                  <div>
                    <div className={styles.previewOverline}>WORKFLOW PREVIEW</div>
                    <div className={styles.previewTitle}>Research Assistant Pipeline</div>
                  </div>
                  <span className={styles.previewBadge}>Ready to run</span>
                </div>

                <div className={styles.previewMetrics}>
                  {[
                    { l: "Nodes", v: "6" },
                    { l: "Latency", v: "~2.4s" },
                    { l: "Tools", v: "3" },
                    { l: "Guards", v: "1" },
                  ].map((m) => (
                    <div key={m.l} className={styles.previewMetric}>
                      <div className={styles.previewMetricLabel}>{m.l}</div>
                      <div className={styles.previewMetricValue}>{m.v}</div>
                    </div>
                  ))}
                </div>

                <div className={styles.previewFlow}>
                  <div className={styles.previewFlowTitle}>
                    <span className={styles.previewFlowDot} /> Visual execution path
                  </div>
                  <div className={styles.previewNodes}>
                    {PREVIEW_NODES.map((n, i) => (
                      <div key={i} className={styles.previewNodeRow}>
                        <div className={styles.previewNodeTrack}>
                          <div className={styles.previewNodePin} style={{ background: n.color }} />
                          {i < PREVIEW_NODES.length - 1 && <div className={styles.previewNodeLine} />}
                        </div>
                        <div className={styles.previewNodeCard}>
                          <div className={styles.previewNodeTitle}>{n.name}</div>
                          <div className={styles.previewNodeHint}>Configured and connected</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/*  Templates  */}
          <section ref={templatesRef} className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <h2 className={styles.sectionTitle}>Templates</h2>
                <p className={styles.sectionDescription}>
                  Pick a starting point or create from scratch.
                </p>
              </div>
              <div className={styles.sectionAction}>
                <div className={styles.searchWrap}>
                  <Search size={16} className={styles.searchIcon} />
                  <input
                    className={styles.searchInput}
                    placeholder="Search templates\u2026"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className={styles.templateGrid}>
              <button className={`${styles.cardButton} ${styles.blankCard}`} onClick={onCreateBlank} type="button">
                <div className={styles.cardTop}>
                  <div className={styles.cardIcon} style={{ background: "rgba(99,102,241,0.08)", color: "var(--accent)", border: "1px solid rgba(99,102,241,0.18)" }}>
                    <Plus size={22} />
                  </div>
                </div>
                <div className={styles.cardBody}>
                  <div className={styles.cardTitle}>Blank workflow</div>
                  <p className={styles.cardText}>Start from zero and add the exact nodes you need piece by piece.</p>
                </div>
                <div className={styles.cardFooter}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--accent)", fontSize: 13, fontWeight: 600 }}>
                    Start blank <ArrowRight size={14} />
                  </span>
                </div>
              </button>

              {filteredTemplates.map((t) => (
                <TemplateCard key={t.name} template={t} onClick={() => onLoadTemplate(t.config)} />
              ))}
            </div>

            {filteredTemplates.length === 0 && (
              <div className={styles.emptyState}>
                No templates found matching &ldquo;{search}&rdquo;.
              </div>
            )}
          </section>

          {/*  Saved workflows  */}
          {saved.length > 0 && (
            <section className={styles.section}>
              <div className={styles.sectionHeader}>
                <div>
                  <h2 className={styles.sectionTitle}>Saved workflows</h2>
                  <p className={styles.sectionDescription}>Pick up right where you left off.</p>
                </div>
              </div>
              <div className={styles.savedGrid}>
                {saved.map((pipeline) => (
                  <SavedWorkflowCard key={pipeline.id} pipeline={pipeline} onClick={() => onLoadPipeline(pipeline)} />
                ))}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

export { TEMPLATES };
export type { Template };