"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { nanoid } from "nanoid";
import { MessageBubble } from "@/components/MessageBubble";
import { ToolApprovalCard } from "@/components/ToolApprovalCard";
import { HumanInputCard } from "@/components/HumanInputCard";
import { AppPanel, AppPanelItem } from "@/components/AppPanel";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { SettingsPanel, SettingsTab } from "@/components/SettingsPanel";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { RealtimeVoicePanel } from "@/components/RealtimeVoicePanel";
import { Thread, Message, Task, TaskList, TaskStatus, UploadedFile } from "@/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Send, Plus, Mic, Music2, ListTodo, Clock, BarChart2, StopCircle, Loader2, X, Radio, type LucideIcon } from "lucide-react";

export default function ChatPage() {
  const { isAuthenticated, isLoading: authLoading, loginWithGoogle } = useAuth();

  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // ── Settings Panel State ─────────────────────────────────
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [settingsPanelTab, setSettingsPanelTab] = useState<SettingsTab>("general");

  function openSettingsPanel(tab: SettingsTab = "general") {
    setSettingsPanelTab(tab);
    setSettingsPanelOpen(true);
  }

  // ── App Panel State ──────────────────────────────────────
  const [panelItems, setPanelItems] = useState<AppPanelItem[]>([]);
  const [activePanelId, setActivePanelId] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  // ── Task Board State ─────────────────────────────────────
  const [taskList, setTaskList] = useState<TaskList | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Tracks the active AbortController for the current SSE fetch so we can
  // cancel the stream when the user clicks Stop.
  const wsRef = useRef<AbortController | null>(null);
  // Tracks which AppPanel item holds the Kanban board so task updates can
  // patch its toolArguments and be pushed to the iframe via update_context.
  const kanbanPanelIdRef = useRef<string | null>(null);

  // ── File attachment state ────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<UploadedFile[]>([]);
  const [uploadingFile, setUploadingFile] = useState(false);

  // ── Realtime speech-to-speech panel ────────────────────────────────
  const [realtimeOpen, setRealtimeOpen] = useState(false);

  // Load threads on mount
  useEffect(() => {
    loadThreads();
  }, []);

  // Load messages when thread changes, and reset panel/task state
  useEffect(() => {
    if (currentThreadId) {
      loadMessages(currentThreadId);
      setPanelItems([]);
      setActivePanelId(null);
      setTaskList(null);
      kanbanPanelIdRef.current = null;
    }
  }, [currentThreadId]);

  // Keep the Kanban panel item's toolArguments in sync with taskList state so
  // the iframe receives live update_context messages from AppPanel.
  useEffect(() => {
    const id = kanbanPanelIdRef.current;
    if (!taskList || !id) return;
    setPanelItems((prev) =>
      prev.map((item) =>
        item.id === id ? { ...item, toolArguments: { task_list: taskList } } : item
      )
    );
  }, [taskList]);

  useEffect(() => {
    // Auto-scroll to bottom whenever messages change
    const el = containerRef.current;
    if (!el) return;
    // Use setTimeout to ensure DOM is updated
    setTimeout(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }, 100);
  }, [messages, loading]);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  }, [input]);

  async function loadThreads() {
    try {
      const fetchedThreads = await api.getThreads();
      setThreads(fetchedThreads);
      if (fetchedThreads.length > 0 && !currentThreadId) {
        setCurrentThreadId(fetchedThreads[0].id);
      }
    } catch (error) {
      console.error("Failed to load threads:", error);
    }
  }

  async function loadMessages(threadId: string) {
    try {
      const fetchedMessages = await api.getMessages(threadId);
      // Guard: don't overwrite optimistic messages while a stream is active.
      // Race: when doSendMessage creates a new thread and calls setCurrentThreadId,
      // the currentThreadId useEffect fires loadMessages. By then wsRef is already
      // set, so we preserve the optimistic user+assistant messages in flight.
      setMessages((current) => (wsRef.current ? current : fetchedMessages));
    } catch (error) {
      console.error("Failed to load messages:", error);
      setMessages((current) => (wsRef.current ? current : []));
    }
  }

  const handleNewChat = async () => {
    try {
      const newThread = await api.createThread("New Chat");
      setThreads([newThread, ...threads]);
      setCurrentThreadId(newThread.id);
      setMessages([]);
    } catch (error) {
      console.error("Failed to create thread:", error);
    }
  };

  const handleSelectThread = (threadId: string) => {
    setCurrentThreadId(threadId);
    setTaskList(null); // clear board when switching conversations
  };

  const handleDeleteThread = async (threadId: string) => {
    try {
      await api.deleteThread(threadId);
      setThreads(threads.filter((t) => t.id !== threadId));
      if (currentThreadId === threadId && threads.length > 1) {
        const remaining = threads.filter((t) => t.id !== threadId);
        setCurrentThreadId(remaining[0]?.id || null);
      }
    } catch (error) {
      console.error("Failed to delete thread:", error);
    }
  };

  const handleRenameThread = async (threadId: string, newName: string) => {
    try {
      await api.updateThread(threadId, newName);
      setThreads(
        threads.map((t) =>
          t.id === threadId ? { ...t, name: newName, updated_at: new Date().toISOString() } : t
        )
      );
    } catch (error) {
      console.error("Failed to rename thread:", error);
    }
  };

  // HITL: respond to a tool approval or human input request via HTTP POST.
  // The Next.js route at /api/chat/respond/[requestId] proxies to the backend.
  function respondToHITL(
    requestId: string,
    data: Record<string, unknown>
  ) {
    fetch(`/api/chat/respond/${requestId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }).catch((err) => console.error("HITL respond failed:", err));
  }

  // MCP App: handle context updates from interactive widgets
  async function handleMcpAppResult(toolName: string, result: unknown) {
    if (!currentThreadId) return;
    try {
      await api.updateMcpContext(currentThreadId, toolName, result);
    } catch (err) {
      console.error("Failed to update MCP context:", err);
    }
  }

  // ── App Panel Management ────────────────────────────────
  const openInPanel = useCallback((item: AppPanelItem) => {
    setPanelItems((prev) => {
      // Replace existing item with same toolName, or add new
      const existing = prev.findIndex((p) => p.toolName === item.toolName);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = { ...item, id: prev[existing].id };
        return updated;
      }
      return [...prev, item];
    });
    setActivePanelId(item.id);
    setPanelCollapsed(false);
  }, []);

  const closePanelItem = useCallback((id: string) => {
    setPanelItems((prev) => {
      const filtered = prev.filter((i) => i.id !== id);
      if (activePanelId === id && filtered.length > 0) {
        setActivePanelId(filtered[filtered.length - 1].id);
      } else if (filtered.length === 0) {
        setActivePanelId(null);
      }
      return filtered;
    });
  }, [activePanelId]);

  const closeAllPanels = useCallback(() => {
    setPanelItems([]);
    setActivePanelId(null);
  }, []);

  /** Abort the active SSE stream and signal the backend to stop the agent. */
  function handleStop() {
    if (wsRef.current) {
      wsRef.current.abort();
      wsRef.current = null;
    }
    if (currentThreadId) {
      const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
      fetch(`${apiBase}/chat/${currentThreadId}/cancel`, { method: "POST" }).catch(() => {});
    }
  }

  // ── File attachment handlers ─────────────────────────────────────────────

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    // Reset so the same file can be re-selected
    e.target.value = "";

    // Ensure we have a thread before uploading
    let threadId = currentThreadId;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        setThreads([newThread]);
        setCurrentThreadId(newThread.id);
        threadId = newThread.id;
      } catch {
        console.error("Failed to create thread for file upload");
        return;
      }
    }

    setUploadingFile(true);
    try {
      const uploaded = await Promise.all(
        files.map((f) => api.uploadFile(threadId!, f))
      );
      setAttachedFiles((prev) => [...prev, ...uploaded]);
    } catch (err) {
      console.error("File upload failed:", err);
    } finally {
      setUploadingFile(false);
    }
  }

  async function handleRemoveFile(fileId: string) {
    if (!currentThreadId) return;
    try {
      await api.deleteFile(currentThreadId, fileId);
    } catch {
      // Best-effort — remove from local state regardless
    }
    setAttachedFiles((prev) => prev.filter((f) => f.id !== fileId));
  }

  async function doSendMessage(text: string) {
    if (!text.trim() || loading) return;

    const currentInput = text;
    const currentFileIds = attachedFiles.map((f) => f.id);

    // Clear input and show user message immediately (optimistic — never blocked by async work)
    setInput("");
    setAttachedFiles([]);
    setMessages((prev) => [
      ...prev,
      { id: nanoid(), role: "user" as const, content: currentInput, timestamp: new Date() },
    ]);
    setLoading(true);

    // Ensure a thread exists before opening the stream
    let threadId = currentThreadId;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        setThreads((prev) => [newThread, ...prev]);
        setCurrentThreadId(newThread.id);
        threadId = newThread.id;
      } catch (error) {
        console.error("Failed to create thread:", error);
        setMessages((prev) => [
          ...prev,
          {
            id: nanoid(),
            role: "assistant" as const,
            content: "⚠️ Could not reach the backend. Is it running on port 8000?",
            timestamp: new Date(),
          },
        ]);
        setLoading(false);
        return;
      }
    }

    // Update thread name on the first message
    if (messages.length === 0) {
      const name = currentInput.slice(0, 50) + (currentInput.length > 50 ? "..." : "");
      handleRenameThread(threadId, name);
    }

    // ── Mutable bubble tracking ──────────────────────────────────────────
    // After a step that uses tools (and triggers HITL cards), the agent's
    // NEXT text response must appear BELOW those cards — not update the old
    // placeholder that sits above them. We track this with a plain mutable
    // object (not React state) so handlers can mutate it synchronously.
    const msgState = {
      activeAssistantId: nanoid() as string,
      needsNewBubble: false,
    };
    setMessages((m) => [
      ...m,
      { id: msgState.activeAssistantId, role: "assistant" as const, content: "", reasoning: "", timestamp: new Date() },
    ]);

    // Call before any handler that writes streaming content. If a tool step
    // just finished (needsNewBubble=true), inserts a fresh bubble at the
    // tail of the message list and updates activeAssistantId to point at it.
    function ensureActiveBubble() {
      if (!msgState.needsNewBubble) return;
      const newId = nanoid();
      msgState.activeAssistantId = newId;
      msgState.needsNewBubble = false;
      setMessages((m) => [
        ...m,
        { id: newId, role: "assistant" as const, content: "", reasoning: "", timestamp: new Date(), isContinuation: true },
      ]);
    }

    // ── Start SSE stream from backend ────────────────────────────────────
    const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const abortController = new AbortController();
    wsRef.current = abortController;

    // processEvent handles every server-sent event type.
    const processEvent = (data: Record<string, unknown>) => {
      // ── Keepalive / end marker ────────────────────────────────────────
      if (data.type === "pong" || data.type === "done") return;

      // ── HITL: Tool Approval Request ─────────────────────────────────
      if (data.type === "tool_approval_request") {
        const hitlId = nanoid();
        setMessages((m) => [
          ...m,
          {
            id: hitlId,
            role: "tool_approval" as const,
            content: "",
            timestamp: new Date(),
            metadata: {
              requestId: data.request_id,
              toolName: data.tool_name,
              arguments: data.arguments,
              context: data.context,
            },
          },
        ]);
        return;
      }

      // ── HITL: Human Input Request ───────────────────────────────────
      if (data.type === "human_input_request") {
        const hitlId = nanoid();
        setMessages((m) => [
          ...m,
          {
            id: hitlId,
            role: "human_input" as const,
            content: "",
            timestamp: new Date(),
            metadata: {
              requestId: data.request_id,
              question: data.question,
              context: data.context,
              options: data.options,
              allowFreeform: data.allow_freeform,
            },
          },
        ]);
        return;
      }

      // ── Tool result ─────────────────────────────────────────────────
      if (data.type === "tool_result") {
        // Task management results are shown in the Kanban panel
        if (data.tool_name === "manage_tasks") return;

        // For MCP App tools with app_data, merge the data into
        // the tool_call arguments so the iframe receives it
        if (data.has_app && data.app_data) {
          const panelId = (data.tool_call_id as string) || nanoid();
          const httpUrl = (data.http_url as string) || `/ui/${data.tool_name as string}`;
          openInPanel({
            id: panelId,
            httpUrl,
            toolName: data.tool_name as string,
            toolArguments: data.app_data as Record<string, unknown>,
            timestamp: Date.now(),
          });
          setMessages((m) =>
            m.map((msg) => {
              if (msg.role !== "assistant" || !msg.toolCalls) return msg;
              const updatedCalls = msg.toolCalls.map((tc) => {
                const matchById = data.tool_call_id && tc.id === data.tool_call_id;
                const matchByName = tc.name === data.tool_name;
                if (!matchById && !matchByName) return tc;
                const existingArgs =
                  typeof tc.arguments === "string" ? JSON.parse(tc.arguments) : tc.arguments;
                return {
                  ...tc,
                  arguments: { ...existingArgs, ...(data.app_data as object) },
                  result: (data.content as string) || tc.result,
                };
              });
              return { ...msg, toolCalls: updatedCalls };
            })
          );
          return;
        }

        // Skip rendering if an MCP App UI is already showing this tool's output
        if (data.has_app && !data.is_error) return;

        // Attach result to whichever assistant message owns this tool call.
        // Search all assistant messages (not just the current one) so results
        // from earlier steps still land in the correct bubble.
        setMessages((m) =>
          m.map((msg) => {
            if (msg.role !== "assistant" || !msg.toolCalls) return msg;
            const hasMatch = msg.toolCalls.some(
              (tc) =>
                (data.tool_call_id && tc.id === data.tool_call_id) ||
                tc.name === data.tool_name
            );
            if (!hasMatch) return msg;
            const updatedCalls = msg.toolCalls.map((tc) => {
              const matchById = data.tool_call_id && tc.id === data.tool_call_id;
              const matchByName = tc.name === data.tool_name;
              if (!matchById && !matchByName) return tc;
              return { ...tc, result: (data.content as string) || "", isError: !!data.is_error };
            });
            return { ...msg, toolCalls: updatedCalls };
          })
        );
        return;
      }

      // ── Task Board Events ───────────────────────────────────────────
      if (data.type === "task_list_created") {
        const tl = data.task_list as TaskList;
        setTaskList(tl);
        const kanbanId = `kanban-${tl.id}`;
        kanbanPanelIdRef.current = kanbanId;
        openInPanel({
          id: kanbanId,
          httpUrl: `${apiBase}/ui/kanban_board`,
          toolName: "manage_tasks",
          toolArguments: { task_list: tl },
          timestamp: Date.now(),
        });
        return;
      }
      if (data.type === "task_updated") {
        const updatedTask = data.task as Task;
        setTaskList((prev) => {
          if (!prev || prev.id !== data.task_list_id) return prev;
          return {
            ...prev,
            tasks: prev.tasks.map((t) => (t.id === updatedTask.id ? { ...t, ...updatedTask } : t)),
          };
        });
        return;
      }
      if (data.type === "task_added") {
        const newTask = data.task as Task;
        setTaskList((prev) => {
          if (!prev || prev.id !== data.task_list_id) return prev;
          if (prev.tasks.find((t) => t.id === newTask.id)) return prev;
          return { ...prev, tasks: [...prev.tasks, newTask] };
        });
        return;
      }
      if (data.type === "task_deleted") {
        setTaskList((prev) => {
          if (!prev || prev.id !== data.task_list_id) return prev;
          return { ...prev, tasks: prev.tasks.filter((t) => t.id !== data.task_id) };
        });
        return;
      }

      // ── Streaming text ──────────────────────────────────────────────
      if (data.type === "text_delta") {
        ensureActiveBubble();
        const textContent = String(data.content || "");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, content: msg.content + textContent, isToolExecuting: false }
              : msg
          )
        );
        return;
      }

      if (data.type === "reasoning_delta") {
        ensureActiveBubble();
        const reasoningContent = String(data.content || "");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, reasoning: (msg.reasoning || "") + reasoningContent }
              : msg
          )
        );
        return;
      }

      if (data.type === "completion") {
        const finalContent = Array.isArray(data.content)
          ? (data.content as string[]).join("")
          : String(data.content || "");
        const hasToolCalls = !!data.has_tool_calls;

        const toolCalls: import("@/types").ToolCall[] = ((data.tool_calls as unknown[]) ?? [])
          .filter((tc) => (tc as { name: string }).name !== "manage_tasks")
          .map((tc) => {
            const t = tc as { id: string; name: string; arguments: unknown; _meta?: import("@/types").ToolCallMeta; risk?: "safe" | "sensitive" | "critical"; color?: "green" | "yellow" | "red" };
            return {
              id: t.id,
              name: t.name,
              arguments: t.arguments as string | Record<string, unknown>,
              result: "Completed",
              _meta: t._meta,
              risk: t.risk,
              color: t.color,
            };
          });

        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? {
                  ...msg,
                  content: finalContent || msg.content,
                  role: (data.role as Message["role"]) ?? "assistant",
                  toolCalls: toolCalls.length > 0 ? toolCalls : msg.toolCalls,
                  isToolExecuting: hasToolCalls,
                }
              : msg
          )
        );

        if (hasToolCalls) {
          // Agent will continue after tool execution — next text should go
          // into a new bubble so it appears below any HITL cards.
          msgState.needsNewBubble = true;
        } else {
          setLoading(false);
          loadThreads();
        }
        return;
      }

      if (data.type === "tool_call") {
        if (data.tool_name === "manage_tasks") return;
        ensureActiveBubble();
        const toolCall = {
          id: (data.tool_call_id as string) || nanoid(),
          name: (data.tool_name as string) || "tool",
          arguments: JSON.stringify(data.arguments || {}),
          risk:  data.risk  as "safe" | "sensitive" | "critical" | undefined,
          color: data.color as "green" | "yellow" | "red" | undefined,
        };
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, toolCalls: [...(msg.toolCalls || []), toolCall], isToolExecuting: true }
              : msg
          )
        );
        return;
      }

      // ── Run-level terminal events ───────────────────────────────────
      if (data.type === "agent.run_completed") {
        // Safety net: if no completion event fired (e.g. tool-only runs), stop loading.
        setLoading(false);
        loadThreads();
        return;
      }

      if (data.type === "agent.run_failed") {
        const errorMsg = String(data.error || "The agent encountered an error.");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, content: msg.content + "\n\n⚠️ " + errorMsg, isToolExecuting: false }
              : msg
          )
        );
        setLoading(false);
        return;
      }

      if (data.type === "cancelled") {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId ? { ...msg, isToolExecuting: false } : msg
          )
        );
        setLoading(false);
        return;
      }

      if (data.type === "error") {
        const errorMsg = String(data.error || "Unknown error");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, content: msg.content + "\n\n⚠️ " + errorMsg, isToolExecuting: false }
              : msg
          )
        );
        setLoading(false);
        return;
      }
    }; // end processEvent

    // ── Fetch SSE and feed each event line into processEvent ──────────
    try {
      const response = await fetch(`${apiBase}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          thread_id: threadId,
          messages: [{ role: "user", content: currentInput }],
          ...(currentFileIds.length ? { file_ids: currentFileIds } : {}),
          ...(localStorage.getItem("system_instructions_override")?.trim()
            ? { system_instructions: localStorage.getItem("system_instructions_override")!.trim() }
            : {}),
          ...(localStorage.getItem("chat_model")?.trim()
            ? { model: localStorage.getItem("chat_model")!.trim() }
            : {}),
        }),
        signal: abortController.signal,
      });

      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop()!;
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const text = line.slice(6).trim();
          if (text === "[DONE]") { reader.cancel(); break outer; }
          let parsed: Record<string, unknown>;
          try { parsed = JSON.parse(text); } catch { continue; }
          processEvent(parsed);
        }
      }
    } catch (err: unknown) {
      if ((err as { name?: string }).name !== "AbortError") {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, content: msg.content + "\n\n⚠️ Connection error.", isToolExecuting: false }
              : msg
          )
        );
        setLoading(false);
      }
    } finally {
      wsRef.current = null;
    }
  }

  // ── Task Board callbacks ─────────────────────────────
  const handleTaskStatusChange = (_taskListId: string, taskId: string, status: TaskStatus) => {
    setTaskList((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        tasks: prev.tasks.map((t) =>
          t.id === taskId ? { ...t, status } : t
        ),
      };
    });
  };

  const handleTaskDelete = (_taskListId: string, taskId: string) => {
    setTaskList((prev) => {
      if (!prev) return prev;
      return { ...prev, tasks: prev.tasks.filter((t) => t.id !== taskId) };
    });
  };

  const handleTaskAdd = (_taskListId: string, title: string) => {
    // Optimistic: will be confirmed by SSE task_added event
    const tempTask: Task = {
      id: `temp-${Date.now()}`,
      title,
      status: "todo",
      order: taskList ? taskList.tasks.length : 0,
    };
    setTaskList((prev) => {
      if (!prev) return prev;
      return { ...prev, tasks: [...prev.tasks, tempTask] };
    });
  };

  function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    doSendMessage(input);
  }
  const currentThread = threads.find((t) => t.id === currentThreadId);

  // ── Auth guards ───────────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--muted)" }} />
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="flex h-screen items-center justify-center bg-background px-4">
        <div className="text-center space-y-6 max-w-sm w-full">
          <div
            className="w-16 h-16 mx-auto rounded-full flex items-center justify-center text-2xl font-bold text-white"
            style={{
              background:
                "linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 70%, #000))",
            }}
          >
            AI
          </div>
          <div>
            <h1 className="text-2xl font-bold">Welcome</h1>
            <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>
              Sign in to start chatting with your AI assistant
            </p>
          </div>
          <button
            onClick={loginWithGoogle}
            className="flex items-center gap-3 mx-auto px-6 py-3 bg-white text-gray-800 rounded-xl text-sm font-semibold hover:bg-gray-100 transition-colors shadow-lg cursor-pointer"
          >
            {/* Google G */}
            <svg className="w-5 h-5" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            Continue with Google
          </button>
          <p className="text-xs" style={{ color: "var(--muted)" }}>
            Your conversations are private and secure
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-background text-foreground" suppressHydrationWarning>
      {/* Settings Panel (portal-like overlay) */}
      <SettingsPanel
        isOpen={settingsPanelOpen}
        initialTab={settingsPanelTab}
        onClose={() => setSettingsPanelOpen(false)}
      />
      {/* Sidebar */}
      <div
        className={`shrink-0 overflow-hidden transition-all duration-300 ease-in-out ${
          sidebarOpen ? "w-64" : "w-0"
        }`}
      >
        <Sidebar
          threads={threads}
          currentThreadId={currentThreadId}
          onNewChat={handleNewChat}
          onSelectThread={handleSelectThread}
          onDeleteThread={handleDeleteThread}
          onRenameThread={handleRenameThread}
          onCollapse={() => setSidebarOpen(false)}
          onOpenSettings={openSettingsPanel}
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex min-w-0">
        {/* Chat Area */}
        <div className="flex-1 flex flex-col min-w-0">
        <Header
          onToggleSidebar={sidebarOpen ? undefined : () => setSidebarOpen(true)}
          sidebarOpen={sidebarOpen}
          threadName={currentThread?.name}
          onOpenSettings={openSettingsPanel}
        />

        {/* Messages Area */}
        <div
          ref={containerRef}
          className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent"
        >
          {messages.length === 0 ? (
            <div className="h-full flex items-center justify-center">
              <div className="text-center space-y-6 w-full max-w-2xl px-4">
                <div
                  className="w-14 h-14 mx-auto rounded-full flex items-center justify-center text-xl font-bold text-white"
                  style={{ background: "linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 70%, #000))" }}
                >
                  AI
                </div>
                <div>
                  <h2 className="text-2xl font-semibold">How can I help you today?</h2>
                  <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>
                    Ask me anything — I&apos;ll respond thoughtfully.
                  </p>
                </div>

                {/* Conversation Starters */}
                <div className="grid grid-cols-2 gap-2 mt-4">
                  {([
                    { icon: Music2, text: "Play Despacito on Spotify" },
                    { icon: ListTodo, text: "Plan tasks to organise a birthday party" },
                    { icon: Clock, text: "What's the current time?" },
                    { icon: BarChart2, text: "Show a data visualisation" },
                  ] as { icon: LucideIcon, text: string }[]).map(({ icon: Icon, text }, idx) => (
                    <button
                      key={idx}
                      onClick={() => doSendMessage(text)}
                      className="flex items-center gap-2.5 p-3 rounded-xl text-left text-sm transition-colors hover:bg-(--card-hover) cursor-pointer"
                      style={{ background: "var(--card)", color: "var(--muted)" }}
                    >
                      <span style={{ color: "var(--accent)" }}>
                        <Icon className="w-4 h-4 shrink-0" />
                      </span>
                      <span>{text}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto w-full px-4 py-2 space-y-2">
              {messages.map((m) => {
                if (m.role === "tool_approval" && m.metadata) {
                  return (
                    <div key={m.id} className="px-4 py-2">
                      <div className="max-w-3xl mx-auto">
                      <div className="flex gap-3">
                        <div className="w-7 shrink-0" />
                        <div className="flex-1 min-w-0">
                      <ToolApprovalCard
                        requestId={m.metadata.requestId as string}
                        toolName={m.metadata.toolName as string}
                        arguments={m.metadata.arguments as Record<string, unknown>}
                        context={m.metadata.context as string | undefined}
                        onRespond={respondToHITL}
                      />
                        </div>
                      </div>
                      </div>
                    </div>
                  );
                }

                if (m.role === "human_input" && m.metadata) {
                  return (
                    <div key={m.id} className="px-4 py-1">
                      <div className="max-w-3xl mx-auto">
                      <div className="flex gap-3">
                        <div className="w-7 shrink-0" />
                        <div className="flex-1 min-w-0">
                      <HumanInputCard
                        requestId={m.metadata.requestId as string}
                        question={m.metadata.question as string}
                        context={m.metadata.context as string | undefined}
                        options={
                          (m.metadata.options as
                            | { key: string; label: string; description?: string }[]
                            | undefined) || []
                        }
                        allowFreeform={m.metadata.allowFreeform as boolean | undefined}
                        onRespond={respondToHITL}
                      />
                        </div>
                      </div>
                      </div>
                    </div>
                  );
                }

                if (m.role === "tool_result" && m.metadata) {
                  const isErr = m.metadata.isError as boolean | undefined;
                  return (
                    <div
                      key={m.id}
                      className={`py-2 px-3 my-1 text-xs rounded-md border ${
                        isErr
                          ? "border-red-700 bg-red-950/40 text-red-300"
                          : "text-zinc-400"
                      }`}
                      style={isErr ? {} : { background: "var(--code-bg)", borderColor: "var(--border)" }}
                    >
                      <span className="font-semibold">
                        🔧 {(m.metadata.toolName as string) || "tool"}
                      </span>
                      {m.content && (
                        <pre className="mt-1 whitespace-pre-wrap text-[11px]">
                          {m.content}
                        </pre>
                      )}
                    </div>
                  );
                }

                // Only render MessageBubble for user/assistant messages
                if (m.role === "user" || m.role === "assistant") {
                  return (
                    <MessageBubble
                      key={m.id}
                      role={m.role}
                      content={m.content}
                      reasoning={m.reasoning}
                      timestamp={m.timestamp}
                      toolCalls={m.toolCalls}
                      isToolExecuting={m.isToolExecuting}
                      isContinuation={m.isContinuation}
                      onMcpAppResult={handleMcpAppResult}
                      onOpenInPanel={(tool) => {
                        const args = typeof tool.arguments === "string"
                          ? JSON.parse(tool.arguments)
                          : tool.arguments;
                        openInPanel({
                          id: tool.id,
                          httpUrl: tool._meta?.ui?.httpUrl || `/ui/${tool.name}`,
                          toolName: tool.name,
                          toolArguments: args,
                          timestamp: Date.now(),
                        });
                      }}
                    />
                  );
                }

                return null;
              })}
{/* Only show bounce dots when loading but no assistant message exists yet */}
              {loading && !messages.some((m) => m.role === 'assistant' && m.id === messages[messages.length - 1]?.id) && (
                <div className="py-2">
                  <div className="flex gap-3">
                    <div
                      className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold text-white"
                      style={{ background: "linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 70%, #000))" }}
                    >
                      AI
                    </div>
                    <div className="flex items-center gap-1 pt-2.5">
                      <div className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ background: "var(--muted)", animationDelay: "0ms" }} />
                      <div className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ background: "var(--muted)", animationDelay: "150ms" }} />
                      <div className="w-1.5 h-1.5 rounded-full animate-bounce" style={{ background: "var(--muted)", animationDelay: "300ms" }} />
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input Area */}
        <div className="bg-background pb-4 pt-2">
          <div className="max-w-3xl mx-auto px-4">
            <form
              onSubmit={sendMessage}
              className="flex flex-col border border-(--border) bg-(--input-bg) px-3 py-2 shadow-sm"
              style={{ borderRadius: "12px" }}
            >
              {/* File chips row — shown when files are attached */}
              {attachedFiles.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pb-2">
                  {attachedFiles.map((f) => (
                    <span
                      key={f.id}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-lg border border-(--border) bg-(--card)"
                      style={{ color: "var(--foreground)" }}
                    >
                      <span className="max-w-32 truncate">{f.name}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveFile(f.id)}
                        className="shrink-0 hover:opacity-70 transition-opacity cursor-pointer"
                        aria-label={`Remove ${f.name}`}
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="flex items-end gap-2">
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelected}
                aria-hidden="true"
              />

              {/* Left: attach/plus */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingFile}
                className="shrink-0 mb-0.5 p-1.5 rounded-full hover:bg-(--card-hover) transition-colors self-end disabled:opacity-40 cursor-pointer"
                style={{ color: "var(--muted)" }}
                aria-label="Attach file"
              >
                {uploadingFile ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
              </button>

              {/* Textarea */}
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage(e);
                  }
                }}
                rows={1}
                className="flex-1 resize-none bg-transparent text-sm outline-none py-1.5 max-h-48 overflow-y-auto"
                placeholder="Ask anything"
                disabled={loading}
              />

              {/* Right: voice + send */}
              <div className="flex items-center gap-1 shrink-0 self-end mb-0.5">
                {/* STT mic — transcribes speech into the text input */}
                {!loading && (
                  <VoiceRecorder
                    onTranscript={(text) => setInput((prev) => (prev ? prev + " " + text : text))}
                    disabled={loading}
                  />
                )}
                {/* Realtime speech-to-speech button — separate, opens full-duplex panel */}
                {!loading && (
                  <button
                    type="button"
                    onClick={() => setRealtimeOpen(true)}
                    className="p-1.5 rounded-full hover:bg-(--card-hover) transition-colors cursor-pointer"
                    style={{ color: "var(--muted)" }}
                    aria-label="Start speech-to-speech conversation"
                    title="Live voice conversation"
                  >
                    <Radio className="w-4 h-4" />
                  </button>
                )}
                {loading ? (
                  /* Stop button — visible while the agent is running */
                  <button
                    type="button"
                    onClick={handleStop}
                    className="p-1.5 rounded-full transition-colors cursor-pointer"
                    style={{ background: "var(--accent)", color: "#fff" }}
                    aria-label="Stop"
                  >
                    <StopCircle className="w-4 h-4" />
                  </button>
                ) : (
                  /* Send button — visible when idle */
                  <button
                    type="submit"
                    disabled={!input.trim()}
                    className="p-1.5 rounded-full transition-colors disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    style={{
                      background: input.trim() ? "var(--accent)" : "var(--card)",
                      color: input.trim() ? "#fff" : "var(--muted)",
                    }}
                    aria-label="Send"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                )}
              </div>
              </div>
            </form>
            {/* Realtime speech-to-speech modal */}
            <RealtimeVoicePanel
              isOpen={realtimeOpen}
              onClose={() => setRealtimeOpen(false)}
            />
            <p className="text-xs text-center mt-2" style={{ color: "var(--muted)" }}>
              AI can make mistakes. Verify important information.
            </p>
          </div>
        </div>
      </div>

        {/* App Side Panel */}
        <AppPanel
          items={panelItems}
          activeItemId={activePanelId}
          onSetActive={setActivePanelId}
          onClose={closePanelItem}
          onClosePanel={closeAllPanels}
          isCollapsed={panelCollapsed}
          onToggleCollapse={() => setPanelCollapsed((c) => !c)}
          onResult={handleMcpAppResult}
        />
      </div>

      {/* Overlay for mobile sidebar - kept for safety on narrow viewports */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-10 sm:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
    </div>
  );
}