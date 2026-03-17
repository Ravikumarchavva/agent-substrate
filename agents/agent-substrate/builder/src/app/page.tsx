/* ── Builder Canvas — main page ───────────────────────────────────────────
 *
 * Landing page → Canvas editor with React Flow.
 *
 * Layout (canvas mode):
 *   ┌──────────┬──────────────────────┬───────────┐
 *   │ Palette  │     Canvas Toolbar   │           │
 *   │  (left)  │──────────────────────│ Property  │
 *   │          │    React Flow        │  Panel    │
 *   │          │      Canvas          │  (right)  │
 *   │          │                      │           │
 *   │          ├──────────────────────┤           │
 *   │          │    Builder Chat      │           │
 *   └──────────┴──────────────────────┴───────────┘
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import {
  ReactFlow,
  MiniMap,
  Background,
  BackgroundVariant,
  type ReactFlowInstance,
  type Node,
} from "@xyflow/react";
import { Maximize2, Minimize2, PanelLeftOpen, PanelRightOpen, MessagesSquare } from "lucide-react";
import "@xyflow/react/dist/style.css";

import { usePipelineStore } from "@/store/pipeline-store";
import { nodeTypes } from "@/nodes";
import { NodePalette } from "@/components/NodePalette";
import { PropertyPanel } from "@/components/PropertyPanel";
import { CanvasToolbar } from "@/components/CanvasToolbar";
import { BuilderChat } from "@/components/BuilderChat";
import { LandingPage } from "@/components/LandingPage";
import type { AppItem } from "@/components/PipelineAppPanel";
import { api } from "@/lib/api";
import type { PipelineOut, PipelineConfig, RegistryResponse } from "@/types";
import styles from "./BuilderWorkspace.module.css";

/* ── Default configs per node type ──────────────────────────────────── */

const DEFAULT_CONFIGS: Record<string, Record<string, unknown>> = {
  agent: { model: "gpt-4o-mini", system_prompt: "", max_iterations: 10 },
  tool: { tool_name: "", risk: "safe", hitl_mode: "blocking" },
  skill: { skill_name: "", version: "" },
  guardrail: { guardrail_type: "input", schema_name: "", pass_field: "is_safe", tripwire: true, system_prompt: "" },
  router: { routing_key: "intent", routes: [], routing_fields: [] },
  memory: { backend: "unbounded", ttl: 3600, max_messages: 200 },
  start: {},
  end: {},
  note: { text: "" },
  condition: { conditions: [{ expression: "", label: "Branch 1" }] },
  approval: { prompt: "Does this look correct?" },
};

/* ── MiniMap node colours ────────────────────────────────────────────── */

const MINIMAP_COLORS: Record<string, string> = {
  agent: "#6366f1",
  tool: "#22c55e",
  skill: "#a855f7",
  guardrail: "#f59e0b",
  router: "#06b6d4",
  memory: "#ec4899",
  start: "#22c55e",
  end: "#22c55e",
  note: "#c4a235",
  condition: "#22c55e",
  approval: "#f97316",
};

/* ── Main component ──────────────────────────────────────────────────── */

export default function BuilderPage() {
  const rfInstance = useRef<ReactFlowInstance | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [registry, setRegistry] = useState<RegistryResponse | null>(null);
  const [view, setView] = useState<"landing" | "canvas">("landing");
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);
  const [focusMode, setFocusMode] = useState<"canvas" | "chat" | null>(null);

  /* ── MCP App panel state ──────────────────────────────────────────── */
  const [appItems, setAppItems] = useState<AppItem[]>([]);
  const [activeAppId, setActiveAppId] = useState<string | null>(null);
  const [appPanelCollapsed, setAppPanelCollapsed] = useState(false);

  const openApp = useCallback((item: AppItem) => {
    setAppItems((prev) => {
      const existing = prev.findIndex((p) => p.toolName === item.toolName);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = { ...item, id: prev[existing].id };
        return updated;
      }
      return [...prev, item];
    });
    setChatOpen(true);
    setActiveAppId(item.id);
    setAppPanelCollapsed(false);
  }, []);

  const closeApp = useCallback((id: string) => {
    setAppItems((prev) => {
      const filtered = prev.filter((i) => i.id !== id);
      if (activeAppId === id) {
        setActiveAppId(filtered.length > 0 ? filtered[filtered.length - 1].id : null);
      }
      return filtered;
    });
  }, [activeAppId]);

  /* store selectors */
  const nodes = usePipelineStore((s) => s.nodes);
  const edges = usePipelineStore((s) => s.edges);
  const onNodesChange = usePipelineStore((s) => s.onNodesChange);
  const onEdgesChange = usePipelineStore((s) => s.onEdgesChange);
  const onConnect = usePipelineStore((s) => s.onConnect);
  const addNode = usePipelineStore((s) => s.addNode);
  const setSelectedNodeId = usePipelineStore((s) => s.setSelectedNodeId);
  const selectedNodeId = usePipelineStore((s) => s.selectedNodeId);

  /* ── Fetch registry on mount ──────────────────────────────────────── */
  useEffect(() => {
    api.getRegistry().then(setRegistry).catch(console.error);
  }, []);

  /* ── Restore pipeline from URL on initial load ────────────────────── */
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (!id) return;
    api.getPipeline(id)
      .then((full) => {
        usePipelineStore.getState().loadPipeline({ ...full.config, name: full.name }, full.id);
        setView("canvas");
      })
      .catch(() => {
        // pipeline not found — stay on landing
        window.history.replaceState(null, "", "/");
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ── Keyboard shortcuts ───────────────────────────────────────────── */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        usePipelineStore.getState().undo();
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === "y" || (e.key === "z" && e.shiftKey))) {
        e.preventDefault();
        usePipelineStore.getState().redo();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  /* ── Create blank pipeline ─────────────────────────────────────────── */
  const handleCreateBlank = useCallback(() => {
    const startNode: Node = {
      id: "start_1",
      type: "start",
      position: { x: 200, y: 250 },
      data: { label: "Start", node_type: "start", config: {} },
    };
    usePipelineStore.getState().loadPipeline({
      id: "",
      name: "Untitled Pipeline",
      description: "",
      nodes: [{ id: "start_1", node_type: "start", label: "Start", position: { x: 200, y: 250 }, config: {} }],
      edges: [],
    });
    setView("canvas");
  }, []);

  /* ── Load a template config ────────────────────────────────────────── */
  const handleLoadTemplate = useCallback((config: PipelineConfig) => {
    usePipelineStore.getState().loadPipeline(config);
    setView("canvas");
  }, []);

  /* ── Load a saved pipeline ─────────────────────────────────────────── */
  const handleLoadPipeline = useCallback(async (pipeline: PipelineOut) => {
    try {
      const full = await api.getPipeline(pipeline.id);
      usePipelineStore.getState().loadPipeline({ ...full.config, name: full.name }, full.id);
      window.history.replaceState(null, "", `?id=${full.id}`);
      setView("canvas");
    } catch (err) {
      console.error("Load pipeline failed:", err);
    }
  }, []);

  /* ── Drop handler (from NodePalette drag) ─────────────────────────── */
  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/reactflow-type");
      if (!type || !rfInstance.current || !wrapperRef.current) return;

      const bounds = wrapperRef.current.getBoundingClientRect();
      const position = rfInstance.current.screenToFlowPosition({
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      });

      const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
      const node: Node = {
        id,
        type,
        position,
        data: {
          label: `${type.charAt(0).toUpperCase()}${type.slice(1)}`,
          node_type: type,
          config: { ...(DEFAULT_CONFIGS[type] ?? {}) },
        },
      };
      addNode(node);
    },
    [addNode],
  );

  /* ── Node selection → property panel ──────────────────────────────── */
  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => setSelectedNodeId(node.id),
    [setSelectedNodeId],
  );

  const onPaneClick = useCallback(
    () => setSelectedNodeId(null),
    [setSelectedNodeId],
  );

  /* ── Run handler → open chat ──────────────────────────────────────── */
  const handleRun = useCallback(() => {
    setChatOpen(true);
  }, []);

  const inspectorVisible = !!selectedNodeId && focusMode !== "canvas" && focusMode !== "chat";
  const showPalette = !paletteCollapsed && focusMode !== "canvas" && focusMode !== "chat";
  const showInspectorRail = !inspectorVisible && focusMode !== "canvas" && focusMode !== "chat";
  const showPaletteRail = paletteCollapsed && focusMode !== "canvas" && focusMode !== "chat";
  const showCanvas = focusMode !== "chat";
  const showChat = chatOpen && focusMode !== "canvas";

  /* ── Landing page ──────────────────────────────────────────────────── */
  if (view === "landing") {
    return (
      <LandingPage
        onCreateBlank={handleCreateBlank}
        onLoadTemplate={handleLoadTemplate}
        onLoadPipeline={handleLoadPipeline}
      />
    );
  }

  /* ── Canvas view ───────────────────────────────────────────────────── */
  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden" style={{ background: "var(--bg)" }}>
      {/* toolbar */}
      <CanvasToolbar onRun={handleRun} onBack={() => { window.history.replaceState(null, "", "/"); setView("landing"); }} />

      {/* main area — palette · canvas+chat · inspector */}
      <div className={styles.workspace}>

        {/* left palette */}
        {showPalette ? <NodePalette onCollapse={() => setPaletteCollapsed(true)} /> : null}
        {showPaletteRail ? (
          <aside className={styles.sideRail}>
            <button className={styles.railButton} onClick={() => setPaletteCollapsed(false)} aria-label="Expand workflow blocks">
              <PanelLeftOpen size={16} />
            </button>
            <span className={styles.railLabel}>Blocks</span>
          </aside>
        ) : null}

        {/* canvas + chat (centre column) */}
        <div className={`${styles.centerColumn} ${focusMode ? styles.centerColumnFullscreen : ""}`} ref={wrapperRef}>
          {showCanvas ? (
            <div className={`${styles.canvasCard} ${focusMode === "canvas" ? styles.canvasCardFullscreen : ""}`}>
              <div className={styles.canvasTopbar}>
                <div>
                  <div className={styles.canvasTopbarTitle}>Canvas</div>
                  <div className={styles.canvasTopbarHint}>Build and connect workflow nodes visually</div>
                </div>
                <div className={styles.canvasActions}>
                  {!chatOpen ? (
                    <button className={styles.canvasActionButton} onClick={() => setChatOpen(true)}>
                      <MessagesSquare size={15} />
                      Open test chat
                    </button>
                  ) : null}
                  <button
                    className={styles.canvasActionButton}
                    onClick={() => setFocusMode((prev) => (prev === "canvas" ? null : "canvas"))}
                  >
                    {focusMode === "canvas" ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
                    {focusMode === "canvas" ? "Exit fullscreen" : "Fullscreen"}
                  </button>
                </div>
              </div>
              <div className={styles.canvasBody}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  nodeTypes={nodeTypes}
                  onInit={(inst) => { rfInstance.current = inst; }}
                  onDragOver={onDragOver}
                  onDrop={onDrop}
                  onNodeClick={onNodeClick}
                  onPaneClick={onPaneClick}
                  fitView
                  deleteKeyCode={["Backspace", "Delete"]}
                  proOptions={{ hideAttribution: true }}
                  className="!bg-[var(--bg)]"
                >
                  <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border)" />
                  <MiniMap
                    nodeColor={(n) => MINIMAP_COLORS[n.type ?? "agent"] ?? "#6366f1"}
                    maskColor="rgba(33,33,33,0.75)"
                    style={{ background: "var(--bg-surface)" }}
                    pannable
                    zoomable
                  />
                </ReactFlow>
              </div>
            </div>
          ) : null}

          {/* test chat panel */}
          <BuilderChat
            open={showChat}
            onClose={() => {
              setChatOpen(false);
              setFocusMode((prev) => (prev === "chat" ? null : prev));
            }}
            isFullscreen={focusMode === "chat"}
            onToggleFullscreen={() => {
              setChatOpen(true);
              setFocusMode((prev) => (prev === "chat" ? null : "chat"));
            }}
            appItems={appItems}
            activeAppId={activeAppId}
            onSetActiveApp={setActiveAppId}
            onCloseApp={closeApp}
            onOpenApp={openApp}
            isAppPanelCollapsed={appPanelCollapsed}
            onToggleAppPanel={() => setAppPanelCollapsed((v) => !v)}
          />
        </div>

        {/* right panel — node configuration inspector */}
        {inspectorVisible ? (
          <div className={styles.inspectorWrap}>
            <PropertyPanel registry={registry} onCollapse={() => setSelectedNodeId(null)} />
          </div>
        ) : null}

        {showInspectorRail ? (
          <aside className={`${styles.sideRail} ${styles.sideRailRight}`}>
            <div className={styles.railButton} aria-hidden="true">
              <PanelRightOpen size={16} />
            </div>
            <span className={styles.railLabel}>Inspector</span>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
