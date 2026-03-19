/* ── Pipeline canvas store (Zustand) ─────────────────────────────────────
 *
 * Central state for the visual builder canvas.  Manages:
 *   - React Flow nodes & edges (synced with PipelineConfig)
 *   - Selection state (which node is selected for property panel)
 *   - Undo/redo stack
 *   - Pipeline metadata (name, id, saved status)
 *   - Run state (for the built-in chat panel)
 * ────────────────────────────────────────────────────────────────────────── */

import { create } from "zustand";
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  type Connection,
} from "@xyflow/react";

import type { NodeConfig, EdgeConfig, PipelineConfig, EdgeType } from "@/types";

/* ── Snapshot for undo/redo ────────────────────────────────────────────── */
interface Snapshot {
  nodes: Node[];
  edges: Edge[];
}

/* ── Chat messages for the run panel ───────────────────────────────────── */
export interface ChatMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

/* ── Store shape ─────────────────────────────────────────────────────────── */
interface PipelineStore {
  // -- Canvas state --
  nodes: Node[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;

  // -- Selection --
  selectedNodeId: string | null;
  setSelectedNodeId: (id: string | null) => void;

  // -- Pipeline metadata --
  pipelineId: string | null;
  pipelineName: string;
  setPipelineId: (id: string | null) => void;
  setPipelineName: (name: string) => void;

  // -- Undo / Redo --
  undoStack: Snapshot[];
  redoStack: Snapshot[];
  pushUndo: () => void;
  undo: () => void;
  redo: () => void;

  // -- Node CRUD --
  addNode: (node: Node) => void;
  removeNode: (id: string) => void;
  updateNodeData: (id: string, data: Record<string, unknown>) => void;

  // -- Bulk load (from DB / import) --
  loadPipeline: (config: PipelineConfig, id?: string) => void;

  // -- Export to PipelineConfig --
  toPipelineConfig: () => PipelineConfig;

  // -- Run state --
  isRunning: boolean;
  allChatMessages: Record<string, Record<string, ChatMessage[]>>;
  chatSessions: Record<string, ChatSession[]>;
  activeChatSessionIds: Record<string, string>;
  setIsRunning: (v: boolean) => void;
  addChatMessage: (msg: ChatMessage, sessionId?: string) => void;
  clearChat: () => void;
  getOrCreateSessionId: () => string;
  createChatSession: () => string;
  setActiveChatSession: (sessionId: string) => void;

  // -- Dirty flag --
  isDirty: boolean;
  markClean: () => void;
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function nodeConfigToFlowNode(nc: NodeConfig): Node {
  return {
    id: nc.id,
    type: nc.node_type,          // matches custom nodeTypes keys
    position: nc.position,
    data: { label: nc.label, config: nc.config, node_type: nc.node_type },
  };
}

function edgeConfigToFlowEdge(ec: EdgeConfig): Edge {
  return {
    id: ec.id,
    source: ec.source,
    target: ec.target,
    sourceHandle: ec.source_handle || undefined,
    targetHandle: ec.target_handle || undefined,
    label: ec.label || undefined,
    type: "default",
    data: { edge_type: ec.edge_type },
    animated: ec.edge_type === "router_route",
  };
}

function flowNodeToNodeConfig(n: Node): NodeConfig {
  const data = n.data as Record<string, unknown>;
  return {
    id: n.id,
    node_type: (data.node_type ?? n.type ?? "agent") as NodeConfig["node_type"],
    label: (data.label as string) ?? "",
    position: { x: n.position.x, y: n.position.y },
    config: (data.config as Record<string, unknown>) ?? {},
  };
}

function flowEdgeToEdgeConfig(e: Edge): EdgeConfig {
  const data = e.data as Record<string, unknown> | undefined;
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    source_handle: e.sourceHandle ?? "",
    target_handle: e.targetHandle ?? "",
    edge_type: ((data?.edge_type as string) ?? "agent_tool") as EdgeType,
    label: typeof e.label === "string" ? e.label : "",
  };
}

function createSessionId(pipelineId: string): string {
  return `builder-${pipelineId}-${Date.now()}-${crypto.randomUUID().slice(0, 6)}`;
}

function defaultSessionTitle(index: number): string {
  return `Session ${index}`;
}

function deriveSessionTitle(content: string): string {
  const compact = content.replace(/\s+/g, " ").trim();
  if (!compact) return "New session";
  return compact.length > 30 ? `${compact.slice(0, 30)}…` : compact;
}

/* ── Infer edge type from source/target node types ──────────────────────── */
function inferEdgeType(
  sourceNode: Node | undefined,
  targetNode: Node | undefined
): EdgeType {
  const sType = (sourceNode?.data as Record<string, unknown>)?.node_type ?? sourceNode?.type;
  const tType = (targetNode?.data as Record<string, unknown>)?.node_type ?? targetNode?.type;

  if (sType === "router") return "router_route";
  if (tType === "router") return "router_input";
  if (sType === "mcp") return "agent_mcp";
  if (tType === "tool") return "agent_tool";
  if (tType === "guardrail") return "agent_guardrail";
  if (tType === "skill") return "agent_skill";
  if (tType === "memory") return "agent_memory";
  return "agent_tool";
}

/* ── Store ───────────────────────────────────────────────────────────────── */

export const usePipelineStore = create<PipelineStore>((set, get) => ({
  // -- Canvas state --
  nodes: [],
  edges: [],

  onNodesChange: (changes) => {
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes), isDirty: true }));
  },

  onEdgesChange: (changes) => {
    set((s) => ({ edges: applyEdgeChanges(changes, s.edges), isDirty: true }));
  },

  onConnect: (connection: Connection) => {
    const state = get();
    const sourceNode = state.nodes.find((n) => n.id === connection.source);
    const targetNode = state.nodes.find((n) => n.id === connection.target);
    const edgeType = inferEdgeType(sourceNode, targetNode);

    state.pushUndo();
    set((s) => ({
      edges: addEdge(
        {
          ...connection,
          type: "default",
          animated: edgeType === "router_route",
          data: { edge_type: edgeType },
        },
        s.edges
      ),
      isDirty: true,
    }));
  },

  // -- Selection --
  selectedNodeId: null,
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  // -- Pipeline metadata --
  pipelineId: null,
  pipelineName: "Untitled Pipeline",
  setPipelineId: (id) => set({ pipelineId: id }),
  setPipelineName: (name) => set({ pipelineName: name, isDirty: true }),

  // -- Undo / Redo --
  undoStack: [],
  redoStack: [],

  pushUndo: () => {
    const { nodes, edges, undoStack } = get();
    const snapshot: Snapshot = {
      nodes: JSON.parse(JSON.stringify(nodes)),
      edges: JSON.parse(JSON.stringify(edges)),
    };
    set({ undoStack: [...undoStack.slice(-50), snapshot], redoStack: [] });
  },

  undo: () => {
    const { undoStack, nodes, edges } = get();
    if (undoStack.length === 0) return;
    const prev = undoStack[undoStack.length - 1];
    set((s) => ({
      undoStack: s.undoStack.slice(0, -1),
      redoStack: [
        ...s.redoStack,
        { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) },
      ],
      nodes: prev.nodes,
      edges: prev.edges,
      isDirty: true,
    }));
  },

  redo: () => {
    const { redoStack, nodes, edges } = get();
    if (redoStack.length === 0) return;
    const next = redoStack[redoStack.length - 1];
    set((s) => ({
      redoStack: s.redoStack.slice(0, -1),
      undoStack: [
        ...s.undoStack,
        { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) },
      ],
      nodes: next.nodes,
      edges: next.edges,
      isDirty: true,
    }));
  },

  // -- Node CRUD --
  addNode: (node) => {
    const state = get();
    state.pushUndo();
    set((s) => ({ nodes: [...s.nodes, node], isDirty: true }));
  },

  removeNode: (id) => {
    const state = get();
    state.pushUndo();
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      edges: s.edges.filter((e) => e.source !== id && e.target !== id),
      selectedNodeId: s.selectedNodeId === id ? null : s.selectedNodeId,
      isDirty: true,
    }));
  },

  updateNodeData: (id, data) => {
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...data } } : n
      ),
      isDirty: true,
    }));
  },

  // -- Load from DB --
  loadPipeline: (config, id) => {
    set({
      nodes: config.nodes.map(nodeConfigToFlowNode),
      edges: config.edges.map(edgeConfigToFlowEdge),
      pipelineId: id ?? config.id ?? null,
      pipelineName: config.name,
      undoStack: [],
      redoStack: [],
      isDirty: false,
      selectedNodeId: null,
    });
  },

  // -- Export to PipelineConfig --
  toPipelineConfig: () => {
    const { nodes, edges, pipelineName } = get();
    return {
      id: get().pipelineId ?? crypto.randomUUID(),
      name: pipelineName,
      description: "",
      nodes: nodes.map(flowNodeToNodeConfig),
      edges: edges.map(flowEdgeToEdgeConfig),
    };
  },

  // -- Run state --
  isRunning: false,
  allChatMessages: {},
  chatSessions: {},
  activeChatSessionIds: {},

  setIsRunning: (v) => set({ isRunning: v }),

  createChatSession: () => {
    const { pipelineId, chatSessions } = get();
    if (!pipelineId) return crypto.randomUUID();

    const id = createSessionId(pipelineId);
    const existing = chatSessions[pipelineId] ?? [];
    const session: ChatSession = {
      id,
      title: defaultSessionTitle(existing.length + 1),
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    set((s) => ({
      chatSessions: {
        ...s.chatSessions,
        [pipelineId]: [...(s.chatSessions[pipelineId] ?? []), session],
      },
      activeChatSessionIds: {
        ...s.activeChatSessionIds,
        [pipelineId]: id,
      },
      allChatMessages: {
        ...s.allChatMessages,
        [pipelineId]: {
          ...(s.allChatMessages[pipelineId] ?? {}),
          [id]: [],
        },
      },
    }));

    return id;
  },

  setActiveChatSession: (sessionId) => {
    const { pipelineId } = get();
    if (!pipelineId) return;
    set((s) => ({
      activeChatSessionIds: {
        ...s.activeChatSessionIds,
        [pipelineId]: sessionId,
      },
    }));
  },

  addChatMessage: (msg, targetSessionId) => {
    const { pipelineId } = get();
    if (!pipelineId) return;

    const sessionId = targetSessionId ?? get().getOrCreateSessionId();
    set((s) => ({
      allChatMessages: {
        ...s.allChatMessages,
        [pipelineId]: {
          ...(s.allChatMessages[pipelineId] ?? {}),
          [sessionId]: [...((s.allChatMessages[pipelineId] ?? {})[sessionId] ?? []), msg],
        },
      },
      chatSessions: {
        ...s.chatSessions,
        [pipelineId]: (s.chatSessions[pipelineId] ?? []).map((session) => {
          if (session.id !== sessionId) return session;

          const currentMessages = (s.allChatMessages[pipelineId] ?? {})[sessionId] ?? [];
          const shouldRename =
            msg.role === "user" &&
            currentMessages.length === 0 &&
            session.title.startsWith("Session ");

          return {
            ...session,
            title: shouldRename ? deriveSessionTitle(msg.content) : session.title,
            updatedAt: Date.now(),
          };
        }),
      },
    }));
  },

  clearChat: () => {
    const { pipelineId } = get();
    if (!pipelineId) return;
    const sessionId = get().getOrCreateSessionId();
    set((s) => ({
      allChatMessages: {
        ...s.allChatMessages,
        [pipelineId]: {
          ...(s.allChatMessages[pipelineId] ?? {}),
          [sessionId]: [],
        },
      },
      chatSessions: {
        ...s.chatSessions,
        [pipelineId]: (s.chatSessions[pipelineId] ?? []).map((session) =>
          session.id === sessionId
            ? { ...session, title: session.title.startsWith("Session ") ? session.title : "New session", updatedAt: Date.now() }
            : session
        ),
      },
    }));
  },

  getOrCreateSessionId: () => {
    const { pipelineId, activeChatSessionIds } = get();
    if (!pipelineId) return crypto.randomUUID();
    const existing = activeChatSessionIds[pipelineId];
    if (existing) return existing;
    return get().createChatSession();
  },

  // -- Dirty flag --
  isDirty: false,
  markClean: () => set({ isDirty: false }),
}));
