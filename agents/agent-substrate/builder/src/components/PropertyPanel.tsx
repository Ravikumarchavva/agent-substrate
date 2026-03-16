/* ── PropertyPanel — right sidebar form for selected node ─────────────────
 *
 * Renders a contextual edit form based on the selected node‑type.
 * Writes back to Zustand `updateNodeData` on every field change so the
 * canvas nodes reflect live edits.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useCallback, useEffect, useState } from "react";
import { PanelRightClose } from "lucide-react";
import { usePipelineStore } from "@/store/pipeline-store";
import type { RegistryResponse } from "@/types";
import { Input, Textarea, Select } from "@/components/ui";
import styles from "./PropertyPanel.module.css";

/* ── Thin Label alias for the local section headers ──────────────────── */

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className={styles.label}>
      {children}
    </label>
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
  return (
    <>
      <div>
        <Label>Model</Label>
        <Select
          value={(config.model as string) ?? "gpt-4o-mini"}
          onChange={(v) => onUpdate({ model: v })}
          options={models.map((m) => ({ value: m, label: m }))}
        />
      </div>
      <div>
        <Label>System Prompt</Label>
        <Textarea
          value={(config.system_prompt as string) ?? ""}
          onChange={(v) => onUpdate({ system_prompt: v })}
          placeholder="You are a helpful assistant…"
          rows={5}
        />
      </div>
      <div>
        <Label>Max Iterations</Label>
        <Input
          type="number"
          value={(config.max_iterations as number) ?? 10}
          onChange={(v) => onUpdate({ max_iterations: parseInt(v) || 10 })}
        />
      </div>
    </>
  );
}

function ToolForm({ config, onUpdate, registry }: FormProps) {
  const tools = registry?.tools ?? [];
  return (
    <>
      <div>
        <Label>Tool</Label>
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
      </div>
      <div>
        <Label>HITL Mode</Label>
        <Select
          value={(config.hitl_mode as string) ?? "blocking"}
          onChange={(v) => onUpdate({ hitl_mode: v })}
          options={[
            { value: "blocking", label: "Blocking" },
            { value: "continue_on_timeout", label: "Continue on timeout" },
            { value: "fire_and_continue", label: "Fire & continue" },
          ]}
        />
      </div>
      {config.description && (
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-dim)" }}>
          {String(config.description)}
        </p>
      )}
    </>
  );
}

function SkillForm({ config, onUpdate, registry }: FormProps) {
  const skills = registry?.skills ?? [];
  return (
    <div>
      <Label>Skill</Label>
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
    </div>
  );
}

function GuardrailForm({ config, onUpdate, registry }: FormProps) {
  const schemas = registry?.guardrail_schemas ?? [];
  return (
    <>
      <div>
        <Label>Stage</Label>
        <Select
          value={(config.guardrail_type as string) ?? "input"}
          onChange={(v) => onUpdate({ guardrail_type: v })}
          options={[
            { value: "input", label: "Input" },
            { value: "output", label: "Output" },
          ]}
        />
      </div>
      <div>
        <Label>Schema</Label>
        <Select
          value={(config.schema_name as string) ?? ""}
          onChange={(v) => onUpdate({ schema_name: v })}
          options={[
            { value: "", label: "— select schema —" },
            ...schemas.map((s) => ({ value: s.name, label: s.name })),
          ]}
        />
      </div>
      <div>
        <Label>Pass Field</Label>
        <Input
          value={(config.pass_field as string) ?? "is_safe"}
          onChange={(v) => onUpdate({ pass_field: v })}
          placeholder="is_safe"
        />
      </div>
      <div>
        <Label>Tripwire</Label>
        <Select
          value={String(config.tripwire ?? "true")}
          onChange={(v) => onUpdate({ tripwire: v === "true" })}
          options={[
            { value: "true", label: "Yes — halt on failure" },
            { value: "false", label: "No — warn only" },
          ]}
        />
      </div>
      <div>
        <Label>Judge System Prompt</Label>
        <Textarea
          value={(config.system_prompt as string) ?? ""}
          onChange={(v) => onUpdate({ system_prompt: v })}
          placeholder="Evaluate whether the content is safe…"
          rows={4}
        />
      </div>
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
    <>
      <div>
        <Label>Routing Key</Label>
        <Input
          value={(config.routing_key as string) ?? "intent"}
          onChange={(v) => onUpdate({ routing_key: v })}
          placeholder="intent"
        />
      </div>
      <div>
        <Label>Routes</Label>
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
          <button
            onClick={addRoute}
            className={styles.miniAction}
          >
            +
          </button>
        </div>
      </div>
      <div>
        <Label>Routing Fields</Label>
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
      </div>
    </>
  );
}

function MemoryForm({ config, onUpdate }: FormProps) {
  return (
    <>
      <div>
        <Label>Backend</Label>
        <Select
          value={(config.backend as string) ?? "unbounded"}
          onChange={(v) => onUpdate({ backend: v })}
          options={[
            { value: "unbounded", label: "Unbounded (in-memory)" },
            { value: "redis", label: "Redis" },
          ]}
        />
      </div>
      {(config.backend as string) === "redis" && (
        <>
          <div>
            <Label>Session TTL (seconds)</Label>
            <Input
              type="number"
              value={(config.ttl as number) ?? 3600}
              onChange={(v) => onUpdate({ ttl: parseInt(v) || 3600 })}
            />
          </div>
          <div>
            <Label>Max Messages</Label>
            <Input
              type="number"
              value={(config.max_messages as number) ?? 200}
              onChange={(v) => onUpdate({ max_messages: parseInt(v) || 200 })}
            />
          </div>
        </>
      )}
    </>
  );
}

/* ── NEW: Note form ──────────────────────────────────────────────────── */

function NoteForm({ config, onUpdate }: FormProps) {
  return (
    <div>
      <Label>Note text</Label>
      <Textarea
        value={(config.text as string) ?? ""}
        onChange={(v) => onUpdate({ text: v })}
        placeholder="Add a note…"
        rows={4}
      />
    </div>
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
      <div>
        <Label>Conditions</Label>
        <div className={styles.conditionList}>
          {conditions.map((c, i) => (
            <div key={i} className={styles.conditionCard}>
              <div className={styles.conditionHeader}>
                <span className={styles.conditionMeta}>Branch {i + 1}</span>
                {conditions.length > 1 && (
                  <button
                    onClick={() => removeCond(i)}
                    className={styles.inlineRemove}
                  >
                    Remove
                  </button>
                )}
              </div>
              <Input value={c.label} onChange={(v) => updateCond(i, { label: v })} placeholder="Label…" />
              <Input value={c.expression} onChange={(v) => updateCond(i, { expression: v })} placeholder='e.g. intent == "billing"' />
            </div>
          ))}
        </div>
        <button
          onClick={addCond}
          className={styles.secondaryButton}
        >
          + Add branch
        </button>
      </div>
      <p className={styles.helperText}>
        An "Else" branch is added automatically for unmatched input.
      </p>
    </>
  );
}

/* ── NEW: Approval form ──────────────────────────────────────────────── */

function ApprovalForm({ config, onUpdate }: FormProps) {
  return (
    <div>
      <Label>Message</Label>
      <Textarea
        value={(config.prompt as string) ?? ""}
        onChange={(v) => onUpdate({ prompt: v })}
        placeholder="Describe the message to show the user. E.g. ok to proceed?"
        rows={3}
      />
    </div>
  );
}

/* ── NEW: Start / End (minimal) forms ────────────────────────────────── */

function StartForm() {
  return (
    <p className={styles.infoCard}>
      Entry point for the workflow. Connect this to the first node.
    </p>
  );
}

function EndForm() {
  return (
    <p className={styles.infoCard}>
      Termination point. The flow stops when it reaches this node.
    </p>
  );
}

/* ── Lookup table ─────────────────────────────────────────────────────── */

const FORM_MAP: Record<string, React.ComponentType<FormProps>> = {
  agent: AgentForm,
  tool: ToolForm,
  skill: SkillForm,
  guardrail: GuardrailForm,
  router: RouterForm,
  memory: MemoryForm,
  note: NoteForm,
  condition: ConditionForm,
  approval: ApprovalForm,
};

/* Minimal forms that don't need FormProps */
const STATIC_FORMS: Record<string, React.ComponentType> = {
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
};

const NODE_DESCRIPTIONS: Record<string, string> = {
  agent: "Call a model with instructions and workflow tools.",
  tool: "Run a tool with approval and risk controls.",
  skill: "Attach a reusable MCP or prompt skill to the flow.",
  guardrail: "Run moderation, safety, or output validation checks.",
  router: "Route to the next step based on a structured decision.",
  memory: "Persist conversation state across multiple turns.",
  note: "Document intent or implementation details for collaborators.",
  condition: "Create conditions to branch the workflow.",
  approval: "Pause for a human to approve or reject a step.",
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

  const onUpdate = useCallback(
    (patch: Record<string, unknown>) => {
      if (!selectedNodeId) return;
      updateNodeData(selectedNodeId, {
        config: { ...config, ...patch },
      });
    },
    [selectedNodeId, config, updateNodeData]
  );

  const onLabelChange = useCallback(
    (v: string) => {
      if (!selectedNodeId) return;
      updateNodeData(selectedNodeId, { label: v });
    },
    [selectedNodeId, updateNodeData]
  );

  const FormComponent = FORM_MAP[nodeType];
  const StaticForm = STATIC_FORMS[nodeType];

  if (!node || (!FormComponent && !StaticForm)) {
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
            {nodeType} node
          </span>
          <div className={styles.headerTitle}>{label || `${nodeType} node`}</div>
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
        <div>
          <Label>Label</Label>
          <Input value={label} onChange={onLabelChange} placeholder="Node name…" />
        </div>

        {FormComponent && (
          <FormComponent config={config} onUpdate={onUpdate} registry={registry} />
        )}
        {StaticForm && <StaticForm />}

        {/* delete */}
        <button
          onClick={() => removeNode(node.id)}
          className={styles.dangerButton}
        >
          Delete Node
        </button>
      </div>
    </aside>
  );
}
