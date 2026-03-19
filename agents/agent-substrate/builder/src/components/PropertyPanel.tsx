/* ── PropertyPanel — right sidebar form for selected node ─────────────────
 *
 * Renders a contextual edit form based on the selected node‑type.
 * Writes back to Zustand `updateNodeData` on every field change so the
 * canvas nodes reflect live edits.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PanelRightClose } from "lucide-react";
import { usePipelineStore } from "@/store/pipeline-store";
import type { RegistryResponse } from "@/types";
import { Button, Field, FormLabel, Input, Textarea, Select, Switch } from "@/components/ui";
import styles from "./PropertyPanel.module.css";

/* ── Thin Label alias for the local section headers ──────────────────── */

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className={styles.label}>
      {children}
    </label>
  );
}

function SectionDivider({ children }: { children: React.ReactNode }) {
  return <span className={styles.sectionDivider}>{children}</span>;
}

function FormSection({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.formSection}>
      {title ? <SectionDivider>{title}</SectionDivider> : null}
      {children}
    </div>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className={styles.sliderRow}>
        <span className={styles.sliderLabel}>{label}</span>
        <span className={styles.sliderValue}>{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className={styles.sliderInput}
      />
    </div>
  );
}

function ToggleField({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={styles.toggleRow}>
      <div className={styles.toggleCopy}>
        <FormLabel className={styles.toggleLabel}>{label}</FormLabel>
        {description ? <p className={styles.toggleDescription}>{description}</p> : null}
      </div>
      <Switch checked={value} onCheckedChange={onChange} />
    </div>
  );
}

/* ── Per-node-type form sections ─────────────────────────────────────── */

interface FormProps {
  config: Record<string, unknown>;
  onUpdate: (patch: Record<string, unknown>) => void;
  registry: RegistryResponse | null;
}

function AgentForm({ config, onUpdate, registry }: FormProps) {
  const models = registry?.models ?? ["gpt-4o-mini", "gpt-4o", "o4-mini", "o3"];
  const [showAdvanced, setShowAdvanced] = useState(false);
  return (
    <>
      <FormSection>
        <Field label="Instructions">
          <Textarea
            value={(config.system_prompt as string) ?? ""}
            onChange={(v) => onUpdate({ system_prompt: v })}
            placeholder="You are a helpful assistant…"
            rows={5}
          />
        </Field>
        <ToggleField
          label="Include chat history"
          description="Use previous messages in context."
          value={(config.include_history as boolean) ?? true}
          onChange={(v) => onUpdate({ include_history: v })}
        />
        <Field label="Model">
          <Select
            value={(config.model as string) ?? "gpt-4o-mini"}
            onChange={(v) => onUpdate({ model: v })}
            options={models.map((m) => ({ value: m, label: m }))}
          />
        </Field>
        <div className={styles.toolsRow}>
          <span className={styles.label} style={{ marginBottom: 0 }}>Tools</span>
        </div>
        {/* Registry-based tool checkboxes grouped by risk */}
        {registry && registry.tools.length > 0 ? (
          <div className={styles.toolChecklist}>
            {(["critical", "sensitive", "safe"] as const).map((risk) => {
              const group = registry.tools.filter((t) => t.risk === risk);
              if (!group.length) return null;
              return (
                <div key={risk}>
                  <span className={styles.toolRiskLabel}>{risk.toUpperCase()}</span>
                  {group.map((tool) => {
                    const selected = ((config.tools as string[]) ?? []).includes(tool.name);
                    return (
                      <ToggleField
                        key={tool.name}
                        label={tool.name}
                        description={tool.description}
                        value={selected}
                        onChange={(v) => {
                          const tools = (config.tools as string[]) ?? [];
                          onUpdate({
                            tools: v
                              ? [...tools, tool.name]
                              : tools.filter((t) => t !== tool.name),
                          });
                        }}
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        ) : null}
        <Field label="Output format">
          <Select
            value={(config.output_format as string) ?? "text"}
            onChange={(v) => onUpdate({ output_format: v })}
            options={[
              { value: "text", label: "Text" },
              { value: "json", label: "JSON" },
              { value: "markdown", label: "Markdown" },
            ]}
          />
        </Field>
      </FormSection>

      <FormSection title="Model parameters">
        <SliderField
          label="Temperature"
          value={(config.temperature as number) ?? 1.0}
          min={0}
          max={2}
          step={0.01}
          onChange={(v) => onUpdate({ temperature: v })}
        />
        <SliderField
          label="Max tokens"
          value={(config.max_tokens as number) ?? 2048}
          min={256}
          max={4096}
          step={64}
          onChange={(v) => onUpdate({ max_tokens: v })}
        />
        <SliderField
          label="Top P"
          value={(config.top_p as number) ?? 1.0}
          min={0}
          max={1}
          step={0.01}
          onChange={(v) => onUpdate({ top_p: v })}
        />
      </FormSection>

      <FormSection title="Chat">
        <ToggleField
          label="Display response in chat"
          description="Show the response in test chat."
          value={(config.display_in_chat as boolean) ?? true}
          onChange={(v) => onUpdate({ display_in_chat: v })}
        />
        <ToggleField
          label="Show in-progress messages"
          description="Show partial streaming updates."
          value={(config.show_in_progress as boolean) ?? true}
          onChange={(v) => onUpdate({ show_in_progress: v })}
        />
        <ToggleField
          label="Show search sources"
          description="Show cited sources when available."
          value={(config.show_sources as boolean) ?? true}
          onChange={(v) => onUpdate({ show_sources: v })}
        />
      </FormSection>

      <FormSection title="Advanced">
        <ToggleField
          label="Continue on error"
          description="Keep the run going if this step fails."
          value={(config.continue_on_error as boolean) ?? false}
          onChange={(v) => onUpdate({ continue_on_error: v })}
        />
        {showAdvanced ? (
          <Field label="Max iterations">
            <Input
              type="number"
              value={(config.max_iterations as number) ?? 10}
              onChange={(v) => onUpdate({ max_iterations: parseInt(v) || 10 })}
            />
          </Field>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className={styles.ghostButton}
          onClick={() => setShowAdvanced((p) => !p)}
        >
          {showAdvanced ? "Show less" : "Show more"}
        </Button>
      </FormSection>
    </>
  );
}

function ToolForm({ config, onUpdate, registry }: FormProps) {
  const tools = registry?.tools ?? [];
  return (
    <FormSection>
      <Field label="Tool">
        <Select
          value={(config.tool_name as string) ?? ""}
          onChange={(v) => {
            const found = tools.find((t) => t.name === v);
            onUpdate({
              tool_name: v,
              risk: found?.risk ?? "safe",
              description: found?.description ?? "",
            });
          }}
          options={[
            { value: "", label: "— select tool —" },
            ...tools.map((t) => ({ value: t.name, label: t.name })),
          ]}
        />
      </Field>
      <Field label="HITL mode">
        <Select
          value={(config.hitl_mode as string) ?? "blocking"}
          onChange={(v) => onUpdate({ hitl_mode: v })}
          options={[
            { value: "blocking", label: "Blocking" },
            { value: "continue_on_timeout", label: "Continue on timeout" },
            { value: "fire_and_continue", label: "Fire & continue" },
          ]}
        />
      </Field>
      {typeof config.description === "string" && config.description.length > 0 && (
        <p className={styles.helperText}>{config.description}</p>
      )}
    </FormSection>
  );
}

/* ── McpForm ─────────────────────────────────────────────────────────── */

function McpForm({ config, onUpdate }: FormProps) {
  const transport = (config.transport as string) ?? "sse";
  return (
    <>
      <FormSection>
        <Field label="Server name">
          <Input
            value={(config.server_name as string) ?? ""}
            onChange={(v) => onUpdate({ server_name: v })}
            placeholder="My MCP Server"
          />
        </Field>
        <Field label="Transport">
          <Select
            value={transport}
            onChange={(v) => onUpdate({ transport: v })}
            options={[
              { value: "sse", label: "SSE (HTTP)" },
              { value: "stdio", label: "stdio (local process)" },
            ]}
          />
        </Field>
        {transport !== "stdio" && (
          <Field label="URL">
            <Input
              value={(config.url as string) ?? ""}
              onChange={(v) => onUpdate({ url: v })}
              placeholder="http://localhost:9000/sse"
            />
          </Field>
        )}
        {transport === "stdio" && (
          <>
            <Field label="Command">
              <Input
                value={(config.command as string) ?? ""}
                onChange={(v) => onUpdate({ command: v })}
                placeholder="python"
              />
            </Field>
            <Field label="Arguments (space-separated)">
              <Input
                value={((config.args as string[]) ?? []).join(" ")}
                onChange={(v) =>
                  onUpdate({
                    args: v
                      .split(" ")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="server.py --port 9000"
              />
            </Field>
          </>
        )}
      </FormSection>
      <FormSection title="Tool filter">
        <Field label="Enabled tools (comma-separated, empty = all)">
          <Input
            value={((config.enabled_tools as string[]) ?? []).join(", ")}
            onChange={(v) =>
              onUpdate({
                enabled_tools: v
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
            placeholder="tool_a, tool_b"
          />
        </Field>
      </FormSection>
    </>
  );
}

function SkillForm({ config, onUpdate, registry }: FormProps) {
  const skills = registry?.skills ?? [];
  return (
    <FormSection>
      <Field label="Skill">
        <Select
          value={(config.skill_name as string) ?? ""}
          onChange={(v) => {
            const found = skills.find((s) => s.name === v);
            onUpdate({ skill_name: v, version: found?.version ?? "" });
          }}
          options={[
            { value: "", label: "— select skill —" },
            ...skills.map((s) => ({ value: s.name, label: `${s.name} (${s.version})` })),
          ]}
        />
      </Field>
    </FormSection>
  );
}

interface GuardrailCheck {
  key: string;
  label: string;
  description: string;
}

const GUARDRAIL_CHECKS: GuardrailCheck[] = [
  { key: "pii", label: "Personally identifiable information", description: "Detects and redacts PII" },
  { key: "moderation", label: "Moderation", description: "Flags harmful or policy-violating content" },
  { key: "jailbreak", label: "Jailbreak", description: "Detects prompt injection / jailbreak attempts" },
  { key: "hallucination", label: "Hallucination", description: "Detects factual inconsistencies" },
  { key: "nsfw", label: "NSFW Text", description: "Detects adult or explicit content" },
  { key: "url_filter", label: "URL Filter", description: "Blocks or flags dangerous URLs" },
  { key: "prompt_injection", label: "Prompt Injection Detection", description: "Catches injected instructions" },
  { key: "custom_prompt_check", label: "Custom Prompt Check", description: "Use a custom system prompt" },
];

function GuardrailForm({ config, onUpdate }: FormProps) {
  const checks = (config.checks as Record<string, boolean>) ?? {};
  const showCustom = !!checks["custom_prompt_check"];

  const toggleCheck = (key: string, val: boolean) => {
    onUpdate({ checks: { ...checks, [key]: val } });
  };

  return (
    <>
      <FormSection>
        <Field label="Mode">
          <Select
            value={(config.guardrail_type as string) ?? "input"}
            onChange={(v) => onUpdate({ guardrail_type: v })}
            options={[
              { value: "input", label: "Input as text" },
              { value: "output", label: "Output as text" },
            ]}
          />
        </Field>
      </FormSection>

      <FormSection title="Checks">
        {GUARDRAIL_CHECKS.map(({ key, label, description }) => (
          <ToggleField
            key={key}
            label={label}
            description={description}
            value={checks[key] ?? false}
            onChange={(v) => toggleCheck(key, v)}
          />
        ))}
        {showCustom && (
          <Field label="Custom prompt">
            <Textarea
              value={(config.custom_prompt as string) ?? ""}
              onChange={(v) => onUpdate({ custom_prompt: v })}
              placeholder="Evaluate whether the content meets your criteria…"
              rows={4}
            />
          </Field>
        )}
      </FormSection>

      <FormSection title="Advanced">
        <ToggleField
          label="Continue on error"
          description="Do not stop if the guardrail errors."
          value={(config.continue_on_error as boolean) ?? false}
          onChange={(v) => onUpdate({ continue_on_error: v })}
        />
      </FormSection>
    </>
  );
}

function RouterForm({ config, onUpdate }: FormProps) {
  const routes = (config.routes as string[]) ?? [];
  const [newRoute, setNewRoute] = useState("");

  const addRoute = () => {
    const trimmed = newRoute.trim();
    if (trimmed && !routes.includes(trimmed)) {
      onUpdate({ routes: [...routes, trimmed] });
      setNewRoute("");
    }
  };

  const removeRoute = (r: string) => {
    onUpdate({ routes: routes.filter((x) => x !== r) });
  };

  return (
    <FormSection>
      <Field label="Routing key">
        <Input
          value={(config.routing_key as string) ?? "intent"}
          onChange={(v) => onUpdate({ routing_key: v })}
          placeholder="intent"
        />
      </Field>
      <Field label="Routes">
        <div className={styles.chips}>
          {routes.map((r) => (
            <span
              key={r}
              onClick={() => removeRoute(r)}
              className={styles.chip}
              title="Click to remove"
            >
              {r} ×
            </span>
          ))}
        </div>
        <div className={styles.inlineRow}>
          <Input
            value={newRoute}
            onChange={setNewRoute}
            placeholder="Add route…"
          />
          <Button onClick={addRoute} type="button" variant="outline" size="icon-sm" className={styles.miniActionButton}>
            +
          </Button>
        </div>
      </Field>
      <Field label="Routing fields">
        <Textarea
          value={
            Array.isArray(config.routing_fields)
              ? JSON.stringify(config.routing_fields, null, 2)
              : ""
          }
          onChange={(v) => {
            try {
              onUpdate({ routing_fields: JSON.parse(v) });
            } catch {
              /* let user finish typing */
            }
          }}
          placeholder='[{"name":"intent","type":"str","description":"…"}]'
          rows={4}
        />
      </Field>
    </FormSection>
  );
}

function MemoryForm({ config, onUpdate }: FormProps) {
  return (
    <FormSection>
      <Field label="Backend">
        <Select
          value={(config.backend as string) ?? "unbounded"}
          onChange={(v) => onUpdate({ backend: v })}
          options={[
            { value: "unbounded", label: "Unbounded (in-memory)" },
            { value: "redis", label: "Redis" },
          ]}
        />
      </Field>
      {(config.backend as string) === "redis" && (
        <>
          <Field label="Session TTL">
            <Input
              type="number"
              value={(config.ttl as number) ?? 3600}
              onChange={(v) => onUpdate({ ttl: parseInt(v) || 3600 })}
            />
          </Field>
          <Field label="Max messages">
            <Input
              type="number"
              value={(config.max_messages as number) ?? 200}
              onChange={(v) => onUpdate({ max_messages: parseInt(v) || 200 })}
            />
          </Field>
        </>
      )}
    </FormSection>
  );
}

/* ── NEW: Note form ──────────────────────────────────────────────────── */

function NoteForm({ config, onUpdate }: FormProps) {
  return (
    <FormSection>
      <Field label="Note">
        <Textarea
          value={(config.text as string) ?? ""}
          onChange={(v) => onUpdate({ text: v })}
          placeholder="Add a note…"
          rows={4}
        />
      </Field>
    </FormSection>
  );
}

/* ── NEW: Condition (If/else) form ───────────────────────────────────── */

interface ConditionEntry {
  expression: string;
  label: string;
}

function ConditionForm({ config, onUpdate }: FormProps) {
  const conditions = (config.conditions as ConditionEntry[]) ?? [];

  const updateCond = (idx: number, patch: Partial<ConditionEntry>) => {
    const next = conditions.map((c, i) => (i === idx ? { ...c, ...patch } : c));
    onUpdate({ conditions: next });
  };

  const addCond = () => {
    onUpdate({ conditions: [...conditions, { expression: "", label: `Branch ${conditions.length + 1}` }] });
  };

  const removeCond = (idx: number) => {
    onUpdate({ conditions: conditions.filter((_, i) => i !== idx) });
  };

  return (
    <>
      <FormSection title="Branches">
        <div className={styles.conditionGroup}>
          <div className={styles.conditionList}>
            {conditions.map((c, i) => (
              <div key={i} className={styles.conditionCard}>
                <div className={styles.conditionHeader}>
                  <span className={styles.conditionMeta}>{i === 0 ? "If" : `Else if ${i}`}</span>
                  {conditions.length > 1 && (
                    <Button
                      onClick={() => removeCond(i)}
                      type="button"
                      variant="ghost"
                      size="xs"
                      className={styles.inlineRemove}
                    >
                      Remove
                    </Button>
                  )}
                </div>
                <Input value={c.label} onChange={(v) => updateCond(i, { label: v })} placeholder="Case name (optional)" />
                <Input value={c.expression} onChange={(v) => updateCond(i, { expression: v })} placeholder='Enter condition, e.g. intent == "billing"' />
              </div>
            ))}
          </div>
          <Button
            onClick={addCond}
            type="button"
            variant="outline"
            className={styles.secondaryButton}
          >
            + Add branch
          </Button>
        </div>
        <p className={styles.helperText}>Else is added automatically.</p>
      </FormSection>
    </>
  );
}

/* ── NEW: Approval form ──────────────────────────────────────────────── */

function ApprovalForm({ config, onUpdate }: FormProps) {
  return (
    <FormSection>
      <Field label="Message">
        <Textarea
          value={(config.prompt as string) ?? ""}
          onChange={(v) => onUpdate({ prompt: v })}
          placeholder="Describe the message to show the user. E.g. ok to proceed?"
          rows={3}
        />
      </Field>
    </FormSection>
  );
}

/* ── While form ─────────────────────────────────────────────────── */

function WhileForm({ config, onUpdate }: FormProps) {
  return (
    <>
      <FormSection>
        <Field label="Expression">
          <Textarea
            value={(config.condition as string) ?? ""}
            onChange={(v) => onUpdate({ condition: v })}
            placeholder='e.g. \"DONE\" not in output'
            rows={3}
          />
        </Field>
        <p className={styles.helperText}>
          Uses `output` and `iteration`. Leave blank to loop until the cap is reached.
        </p>
      </FormSection>
      <FormSection title="Limits">
        <Field label="Max iterations">
          <Input
            type="number"
            value={(config.max_iterations as number) ?? 10}
            onChange={(v) => onUpdate({ max_iterations: parseInt(v) || 10 })}
          />
        </Field>
      </FormSection>
    </>
  );
}

/* ── Start / End forms ───────────────────────────────────────────────── */

function StartForm({ config, onUpdate }: FormProps) {
  return (
    <FormSection>
        <Field label="Input variable">
          <Input
            value={(config.input_key as string) ?? "input"}
            onChange={(v) => onUpdate({ input_key: v })}
            placeholder="input"
          />
        </Field>
        <Field label="Input type">
          <Select
            value={(config.input_type as string) ?? "text"}
            onChange={(v) => onUpdate({ input_type: v })}
            options={[
              { value: "text", label: "Text" },
              { value: "json", label: "JSON object" },
              { value: "file", label: "File" },
            ]}
          />
        </Field>
    </FormSection>
  );
}

function EndForm({ config, onUpdate }: FormProps) {
  return (
    <FormSection>
        <Field label="Output variable">
          <Input
            value={(config.output_key as string) ?? "output"}
            onChange={(v) => onUpdate({ output_key: v })}
            placeholder="output"
          />
        </Field>
        <ToggleField
          label="Return full conversation"
          description="Return message history instead of only the final output."
          value={(config.return_history as boolean) ?? false}
          onChange={(v) => onUpdate({ return_history: v })}
        />
    </FormSection>
  );
}

/* ── Lookup table ─────────────────────────────────────────────────────── */

const FORM_MAP: Record<string, React.ComponentType<FormProps>> = {
  agent: AgentForm,
  tool: ToolForm,
  skill: SkillForm,
  mcp: McpForm,
  guardrail: GuardrailForm,
  router: RouterForm,
  memory: MemoryForm,
  note: NoteForm,
  condition: ConditionForm,
  approval: ApprovalForm,
  while: WhileForm,
  start: StartForm,
  end: EndForm,
};

const NODE_COLORS: Record<string, string> = {
  agent: "var(--node-agent)",
  tool: "var(--node-tool)",
  skill: "var(--node-skill)",
  guardrail: "var(--node-guardrail)",
  router: "var(--node-router)",
  memory: "var(--node-memory)",
  start: "var(--success)",
  end: "var(--success)",
  note: "#c4a235",
  condition: "var(--success)",
  approval: "#f97316",
  while: "#f59e0b",
  mcp: "#a855f7",
};

const NODE_DESCRIPTIONS: Record<string, string> = {
  agent: "Call a model with instructions and workflow tools.",
  tool: "Run a tool with approval and risk controls.",
  skill: "Attach a reusable skill prompt to the agent.",
  mcp: "Connect an external MCP server and expose its tools.",
  guardrail: "Run moderation, safety, or output validation checks.",
  router: "Route to the next step based on a structured decision.",
  memory: "Persist conversation state across multiple turns.",
  note: "Document intent or implementation details for collaborators.",
  condition: "Create conditions to branch the workflow.",
  approval: "Pause for a human to approve or reject a step.",
  while: "Loop while a condition is true.",
  start: "Define the workflow inputs.",
  end: "End the run after the final step completes.",
};

/* ── Main component ──────────────────────────────────────────────────── */

interface PropertyPanelProps {
  registry: RegistryResponse | null;
  onCollapse?: () => void;
}

export function PropertyPanel({ registry, onCollapse }: PropertyPanelProps) {
  const nodes = usePipelineStore((s) => s.nodes);
  const selectedNodeId = usePipelineStore((s) => s.selectedNodeId);
  const updateNodeData = usePipelineStore((s) => s.updateNodeData);
  const removeNode = usePipelineStore((s) => s.removeNode);

  const node = nodes.find((n) => n.id === selectedNodeId);
  const nodeData = node?.data as Record<string, unknown> | undefined;
  const nodeType = (nodeData?.node_type as string) ?? node?.type ?? "";
  const config = (nodeData?.config as Record<string, unknown>) ?? {};
  const label = (nodeData?.label as string) ?? "";
  const nodeDescription = NODE_DESCRIPTIONS[nodeType] ?? "Configure this step for the workflow.";

  /* Keep a ref to the latest config so onUpdate stays stable across keystrokes */
  const configRef = useRef(config);
  configRef.current = config;

  const onUpdate = useCallback(
    (patch: Record<string, unknown>) => {
      if (!selectedNodeId) return;
      updateNodeData(selectedNodeId, {
        config: { ...configRef.current, ...patch },
      });
    },
    [selectedNodeId, updateNodeData]
  );

  const onLabelChange = useCallback(
    (v: string) => {
      if (!selectedNodeId) return;
      updateNodeData(selectedNodeId, { label: v });
    },
    [selectedNodeId, updateNodeData]
  );

  const FormComponent = FORM_MAP[nodeType];

  if (!node || !FormComponent) {
    return (
      <aside className={styles.emptyState}>
        <div className={styles.emptyCard}>
          <div className={styles.emptyIcon}>
            ✦
          </div>
          <p className={styles.emptyTitle}>
            Select a node to edit
          </p>
          <p className={styles.emptyDescription}>
            Configure prompts, tools, routing, memory, and validation from this panel.
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside className={styles.sidebar}>
      {/* header */}
      <div className={styles.header}>
        <div
          className={styles.iconBadge}
          style={{ background: NODE_COLORS[nodeType] ?? "var(--accent)" }}
        >
          {nodeType.charAt(0).toUpperCase()}
        </div>
        <div className={styles.headerCopy}>
          <span className={styles.eyebrow}>
            {nodeType}
          </span>
          <div className={styles.headerTitle}>{label || nodeType}</div>
          <div className={styles.headerDescription}>
            {nodeDescription}
          </div>
        </div>
        {onCollapse ? (
          <button
            onClick={onCollapse}
            className={styles.collapseButton}
            aria-label="Collapse node inspector"
            title="Collapse node inspector"
          >
            <PanelRightClose size={15} />
          </button>
        ) : null}
      </div>

      {/* form */}
      <div className={styles.formBody}>
        <Field label="Label">
          <Input value={label} onChange={onLabelChange} placeholder="Node name…" />
        </Field>

        {FormComponent && (
          <FormComponent config={config} onUpdate={onUpdate} registry={registry} />
        )}

        {/* delete */}
        <Button
          onClick={() => removeNode(node.id)}
          type="button"
          variant="danger"
          className={styles.dangerButton}
        >
          Delete Node
        </Button>
      </div>
    </aside>
  );
}
