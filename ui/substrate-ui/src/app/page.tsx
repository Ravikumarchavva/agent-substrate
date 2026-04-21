"use client";

import React, { Suspense, useState, useRef, useEffect, useCallback } from "react";
import { nanoid } from "nanoid";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { MessageBubble } from "@/components/MessageBubble";
import { RaviMark } from "@/components/RaviMark";
import { ToolApprovalCard } from "@/components/ToolApprovalCard";
import { HumanInputCard } from "@/components/HumanInputCard";
import { AppPanel } from "@/components/AppPanel";
import { Sidebar } from "@/components/Sidebar";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { SettingsPanel } from "@/components/SettingsPanel";
import type { SettingsTab } from "@/components/SettingsPanel";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { RealtimeVoicePanel } from "@/components/RealtimeVoicePanel";
import { Message, Task, TaskList, UploadedFile } from "@/types";
import { api } from "@/lib/api";
import {
  getPreferredChatModel,
  CHAT_MODEL_OPTIONS,
  CHAT_MODEL_STORAGE_KEY,
  MODEL_PREFERENCES_UPDATED_EVENT,
  writeStoredValue,
  groupModelOptions,
} from "@/lib/model-preferences";
import { parseChatPath, buildChatPath, buildSettingsPath } from "@/lib/chat-routes";
import {
  getAttachmentIcon,
  formatFileSize,
} from "@/lib/file-utils";
import { useAuth } from "@/contexts/AuthContext";
import { useThreads } from "@/hooks/useThreads";
import { useFileAttachments, type AttachedFilePreview } from "@/hooks/useFileAttachments";
import { useAppPanel } from "@/hooks/useAppPanel";
import { Send, Plus, Music2, Mail, ListTodo, Clock, BarChart2, StopCircle, Loader2, X, Radio, ChevronDown, Settings2, type LucideIcon } from "lucide-react";

const LAST_ACTIVE_THREAD_STORAGE_KEY = "ravi:last-active-thread";

function readLastActiveThreadId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.sessionStorage.getItem(LAST_ACTIVE_THREAD_STORAGE_KEY);
}

function writeLastActiveThreadId(threadId: string | null): void {
  if (typeof window === "undefined") {
    return;
  }

  if (threadId) {
    window.sessionStorage.setItem(LAST_ACTIVE_THREAD_STORAGE_KEY, threadId);
    return;
  }

  window.sessionStorage.removeItem(LAST_ACTIVE_THREAD_STORAGE_KEY);
}

function hasPersistentToolCall(toolCalls: Message["toolCalls"]): boolean {
  return Boolean(toolCalls?.some((tool) => tool._meta?.ui?.httpUrl));
}

function ChatPageContent() {
  const { isAuthenticated, isLoading: authLoading, isAdmin, loginWithGoogle } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const routeState = parseChatPath(pathname);
  const settingsPanelOpen = routeState.settingsTab !== null;
  const settingsPanelTab: SettingsTab = routeState.settingsTab === "admin" && !isAdmin
    ? "general"
    : routeState.settingsTab ?? "general";

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [lastActiveThreadId, setLastActiveThreadId] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const wasAuthenticatedRef = useRef(false);

  const updateLastActiveThreadId = useCallback((threadId: string | null) => {
    setLastActiveThreadId(threadId);
    writeLastActiveThreadId(threadId);
  }, []);

  const currentThreadId = routeState.threadId ?? lastActiveThreadId;

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

  // ── Realtime speech-to-speech panel ────────────────────────────────
  const [realtimeOpen, setRealtimeOpen] = useState(false);

  // ── Model selector + thinking level ────────────────────────────────
  const [selectedModel, setSelectedModel] = useState(() => getPreferredChatModel());
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [thinkingLevel, setThinkingLevel] = useState<string>("medium");
  const [showThinkingPicker, setShowThinkingPicker] = useState(false);
  const modelPickerRef = useRef<HTMLDivElement | null>(null);
  const thinkingPickerRef = useRef<HTMLDivElement | null>(null);

  // Reset thinking level if not compatible with the selected model
  useEffect(() => {
    const model = CHAT_MODEL_OPTIONS.find((m) => m.id === selectedModel);
    if (model?.thinkingLevels && !model.thinkingLevels.includes(thinkingLevel)) {
      setThinkingLevel(model.thinkingLevels[0] || "off");
    }
  }, [selectedModel, thinkingLevel]);

  // Keep selectedModel in sync with localStorage changes from settings
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { key: string; value: string | null } | undefined;
      if (detail?.key === CHAT_MODEL_STORAGE_KEY) {
        setSelectedModel(detail.value ?? getPreferredChatModel());
      }
    };
    window.addEventListener(MODEL_PREFERENCES_UPDATED_EVENT, handler);
    return () => window.removeEventListener(MODEL_PREFERENCES_UPDATED_EVENT, handler);
  }, []);

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target as Node)) {
        setShowModelPicker(false);
      }
      if (thinkingPickerRef.current && !thinkingPickerRef.current.contains(e.target as Node)) {
        setShowThinkingPicker(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    setLastActiveThreadId(readLastActiveThreadId());
  }, []);

  useEffect(() => {
    if (routeState.threadId !== null) {
      updateLastActiveThreadId(routeState.threadId);
    }
  }, [routeState.threadId, updateLastActiveThreadId]);

  const selectThread = useCallback(
    (threadId: string | null, mode: "replace" | "push" = "replace") => {
      updateLastActiveThreadId(threadId);
      const nextUrl = buildChatPath(threadId);
      if (mode === "push") {
        router.push(nextUrl, { scroll: false });
      } else {
        router.replace(nextUrl, { scroll: false });
      }
    },
    [router, updateLastActiveThreadId],
  );

  const openSettingsPanel = useCallback(
    (tab: SettingsTab = "general") => {
      router.push(buildSettingsPath(tab), { scroll: false });
    },
    [router],
  );

  const selectSettingsTab = useCallback(
    (tab: SettingsTab) => {
      router.replace(buildSettingsPath(tab), { scroll: false });
    },
    [router],
  );

  const closeSettingsPanel = useCallback(() => {
    router.push(buildChatPath(lastActiveThreadId), { scroll: false });
  }, [lastActiveThreadId, router]);

  useEffect(() => {
    if (settingsPanelOpen) {
      setDesktopSidebarOpen(true);
    }
  }, [settingsPanelOpen]);

  useEffect(() => {
    if (routeState.settingsTab === "admin" && !isAdmin) {
      router.replace(buildSettingsPath("general"), { scroll: false });
    }
  }, [isAdmin, routeState.settingsTab, router]);

  // ── Custom Hooks ────────────────────────────────────────
  const { threads, setThreads, loadThreads, handleNewChat: _handleNewChat, handleSelectThread: _handleSelectThread, handleDeleteThread, handleRenameThread } = useThreads(selectThread, currentThreadId, {
    autoSelectFirstThread: !settingsPanelOpen,
  });
  const { attachedFiles, uploadingFile, fileInputRef, clearAttachedFiles, handleFileSelected, handleRemoveFile } = useFileAttachments(currentThreadId, selectThread, setThreads);
  const { panelItems, setPanelItems, activePanelId, setActivePanelId, panelCollapsed, setPanelCollapsed, openInPanel, closePanelItem, closeAllPanels } = useAppPanel();

  useEffect(() => {
    if (authLoading) {
      return;
    }

    if (wasAuthenticatedRef.current && !isAuthenticated) {
      // Clear persisted thread so it doesn't leak into the next session
      updateLastActiveThreadId(null);
      setMessages([]);
      setTaskList(null);
      setPanelItems([]);
      setActivePanelId(null);
      kanbanPanelIdRef.current = null;
      setAuthNotice("You were signed out. Sign in again to reopen or start chats.");
      router.replace(buildChatPath(null), { scroll: false });
    }

    // Not authenticated → always show clean /chat (no stale thread IDs in URL)
    if (!isAuthenticated && !settingsPanelOpen && (routeState.threadId || pathname !== "/chat")) {
      router.replace(buildChatPath(null), { scroll: false });
    }

    if (isAuthenticated) {
      setAuthNotice(null);
    }

    wasAuthenticatedRef.current = isAuthenticated;
  }, [authLoading, isAuthenticated, pathname, router, routeState.threadId, setActivePanelId, setPanelItems, settingsPanelOpen, updateLastActiveThreadId]);

  const renderComposerAttachment = useCallback((file: AttachedFilePreview) => {
    const AttachmentIcon = getAttachmentIcon(file.previewKind);
    const previewSource = file.previewUrl || file.url || (currentThreadId
      ? `/api/backend/threads/${currentThreadId}/files/${file.id}/content`
      : "");

    return (
      <div key={file.id} className="attachment-card group/attach relative min-w-0 max-w-full p-2 sm:w-65">
        <button
          type="button"
          onClick={() => handleRemoveFile(file.id)}
          className="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-background/85 text-(--muted) opacity-100 shadow-sm transition-all sm:opacity-0 sm:group-hover/attach:opacity-100 cursor-pointer"
          aria-label={`Remove ${file.name}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>

        {file.previewKind === "image" && previewSource ? (
          <div className="attachment-card__preview">
            <Image
              src={previewSource}
              alt={file.name}
              fill
              unoptimized
              sizes="72px"
              className="object-cover transition-transform duration-200 group-hover/attach:scale-[1.03]"
            />
          </div>
        ) : (
          <div className="attachment-card__icon text-(--accent)">
            <AttachmentIcon className="h-5 w-5" />
          </div>
        )}

        <div className="min-w-0 flex-1 pr-7">
          <div className="truncate text-sm font-semibold text-foreground">{file.name}</div>
          <div className="mt-1 text-[11px] text-(--muted)">
            {formatFileSize(file.size)}
          </div>
        </div>
      </div>
    );
  }, [currentThreadId, handleRemoveFile]);

  // Wrap hook handlers to also manage local page state
  const handleNewChat = useCallback(async () => {
    await _handleNewChat({ onCreated: () => { setMessages([]); setMobileSidebarOpen(false); } });
  }, [_handleNewChat]);

  const handleSelectThread = useCallback((threadId: string) => {
    _handleSelectThread(threadId, { onSelected: () => { setTaskList(null); setMobileSidebarOpen(false); } });
  }, [_handleSelectThread]);

  useEffect(() => {
    if (pathname === "/") {
      router.replace(buildChatPath(isAuthenticated ? currentThreadId : null), { scroll: false });
    }
  }, [currentThreadId, isAuthenticated, pathname, router]);

  // Guard: don't load threads when not authenticated
  const canLoadData = isAuthenticated && !authLoading;

  // Load threads on mount (only when authenticated)
  useEffect(() => { if (canLoadData) void loadThreads(); }, [canLoadData, loadThreads]);

  // Load messages when thread changes, and reset panel/task state
  useEffect(() => {
    clearAttachedFiles();
    if (currentThreadId && canLoadData) {
      loadMessages(currentThreadId);
      setPanelItems([]);
      setActivePanelId(null);
      setTaskList(null);
      kanbanPanelIdRef.current = null;
      return;
    }

    setMessages([]);
    setPanelItems([]);
    setActivePanelId(null);
    setTaskList(null);
    kanbanPanelIdRef.current = null;
  }, [canLoadData, clearAttachedFiles, currentThreadId, setPanelItems, setActivePanelId]);

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
  }, [taskList, setPanelItems]);

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

  // HITL: respond to a tool approval or human input request via HTTP POST.
  // The Next.js route at /api/chat/respond/[requestId] proxies to the backend.
  function respondToHITL(
    requestId: string,
    data: Record<string, unknown>
  ) {
    api.respondToHitl(requestId, data).catch((err: unknown) => {
      console.error("HITL respond failed:", err);
    });
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

  /** Abort the active SSE stream and signal the backend to stop the agent. */
  function handleStop() {
    if (wsRef.current) {
      wsRef.current.abort();
      wsRef.current = null;
    }
    if (currentThreadId) {
      api.cancelChat(currentThreadId).catch((error: unknown) => {
        console.error("Failed to cancel active run:", error);
      });
    }
  }

  async function doSendMessage(text: string) {
    if (!text.trim() || loading) return;

    const currentInput = text;
    const currentFileIds = attachedFiles.map((f) => f.id);
    const requestedModel = currentFileIds.length > 0
      ? "google/gemini-2.5-flash"
      : selectedModel;
    const currentAttachments: UploadedFile[] = attachedFiles.map((file) => ({
      id: file.id,
      thread_id: file.thread_id,
      name: file.name,
      mime: file.mime,
      size: file.size,
      url: file.url,
    }));

    // Clear input and show user message immediately (optimistic — never blocked by async work)
    setInput("");
    clearAttachedFiles();
    setMessages((prev) => [
      ...prev,
      {
        id: nanoid(),
        role: "user" as const,
        content: currentInput,
        timestamp: new Date(),
        attachments: currentAttachments.length > 0 ? currentAttachments : undefined,
      },
    ]);
    setLoading(true);

    // Ensure a thread exists before opening the stream
    let threadId = currentThreadId;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        setThreads((prev) => [newThread, ...prev]);
        selectThread(newThread.id, "push");
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
      handleRenameThread(threadId!, name);
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

    function finalizeAssistantMessages(
      updateActive?: (message: Message) => Message,
    ) {
      setMessages((current) =>
        current
          .map((message) => {
            if (message.role !== "assistant") return message;

            const nextMessage =
              updateActive && message.id === msgState.activeAssistantId
                ? updateActive(message)
                : message;

            return { ...nextMessage, isToolExecuting: false };
          })
          .filter((message) => {
            if (message.role !== "assistant") return true;

            const contentStr = typeof message.content === "string" ? message.content : "";
            const reasoningStr = typeof message.reasoning === "string" ? message.reasoning : "";
            const hasContent = Boolean(contentStr.trim()) || Boolean(reasoningStr.trim());
            return hasContent || hasPersistentToolCall(message.toolCalls);
          })
      );
    }

    // ── Start SSE stream from backend ────────────────────────────────────
    const abortController = new AbortController();
    wsRef.current = abortController;

    // processEvent handles every server-sent event type.
    const processEvent = (data: Record<string, unknown>) => {
      // Guard: if a newer stream has taken ownership (user sent a new message
      // while this one was still draining), discard all further events from
      // this stream so they don't corrupt the new stream's message state.
      if (wsRef.current !== abortController) return;

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
          httpUrl: "/ui/kanban_board",
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
        // Handle LLM-level errors (e.g. provider returned no completion)
        if (data.finish_reason === "error") {
          const errorDetail = typeof data.content === "string" ? data.content : "";
          const errorMsg = errorDetail || "The AI model failed to generate a response. Please try again.";
          finalizeAssistantMessages((message) => ({
            ...message,
            content: (message.content ? message.content + "\n\n" : "") + "⚠️ " + errorMsg,
          }));
          setLoading(false);
          return;
        }

        const finalContent = Array.isArray(data.content)
          ? (data.content as Array<string | { text?: unknown }>).map((item) => {
              if (typeof item === "string") return item;
              if (
                item &&
                typeof item === "object" &&
                typeof item.text === "string"
              ) {
                return item.text;
              }
              return "";
            }).join("")
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

        const toolMarkupPattern = /^\s*<function\/[\s\S]+<\/function>\s*$/;

        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? {
                  ...msg,
                  content:
                    toolCalls.length > 0
                    && toolMarkupPattern.test(finalContent || "")
                    ? ""
                    : finalContent || msg.content,
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
          void loadThreads();
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
        finalizeAssistantMessages();
        setLoading(false);
        void loadThreads();
        return;
      }

      if (data.type === "agent.run_failed") {
        const errorMsg = String(data.error || "The agent encountered an error.");
        finalizeAssistantMessages((message) => ({
          ...message,
          content: message.content + "\n\n⚠️ " + errorMsg,
        }));
        setLoading(false);
        return;
      }

      if (data.type === "cancelled") {
        finalizeAssistantMessages();
        setLoading(false);
        return;
      }

      if (data.type === "error") {
        const errorMsg = String(data.error || data.message || "Unknown error");
        finalizeAssistantMessages((message) => ({
          ...message,
          content: message.content + "\n\n⚠️ " + errorMsg,
        }));
        setLoading(false);
        return;
      }
    }; // end processEvent

    // ── Fetch SSE and feed each event line into processEvent ──────────
    try {
      const response = await api.streamChat(
        {
          thread_id: threadId,
          messages: [{ role: "user", content: currentInput }],
          ...(currentFileIds.length ? { file_ids: currentFileIds } : {}),
          ...(() => {
            const base = localStorage.getItem("system_instructions_override")?.trim() ?? "";
            const tz = localStorage.getItem("user_timezone")?.trim();
            const tzNote = tz ? `User timezone: ${tz}. Always use this timezone when creating or interpreting calendar events and times.` : "";
            const combined = [tzNote, base].filter(Boolean).join("\n");
            return combined ? { system_instructions: combined } : {};
          })(),
          model: requestedModel,
        },
        abortController.signal,
      );

      const responseBody = response.body;
      if (!responseBody) throw new Error("No response body");
      const reader = responseBody.getReader();
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
      }
    } finally {
      // Only finalize state if this stream is still the active owner, or if
      // it was stopped via handleStop (wsRef already cleared to null).
      // If a newer doSendMessage has already set wsRef to a different
      // controller, leave the new stream's state untouched.
      const isOwnerOrStopped =
        wsRef.current === abortController || wsRef.current === null;
      if (wsRef.current === abortController) wsRef.current = null;
      if (isOwnerOrStopped) {
        finalizeAssistantMessages();
        setLoading(false);
      }
    }
  }

  function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    doSendMessage(input);
  }

  // ── Auth guards ───────────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <Loader2 className="w-6 h-6 animate-spin text-(--muted)" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background px-4">
        <div className="text-center space-y-6 max-w-sm w-full">
          <div className="w-14 h-14 mx-auto rounded-2xl flex items-center justify-center text-xl font-bold bg-foreground text-background">
            R
          </div>
          <div>
            <h1 className="text-2xl font-semibold">Welcome</h1>
            <p className="text-sm mt-2 text-(--muted)">
              Sign in to start chatting with your AI assistant
            </p>
          </div>
          {authNotice && (
            <div className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {authNotice}
            </div>
          )}
          <button
            onClick={loginWithGoogle}
            className="flex items-center gap-3 mx-auto px-6 py-3 bg-white text-gray-800 rounded-2xl text-sm font-semibold hover:bg-gray-50 transition-colors cursor-pointer"
            style={{ boxShadow: "var(--shadow-md)" }}
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
          <p className="text-xs text-(--muted)">
            Your conversations are private and secure
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-dvh min-h-dvh overflow-hidden bg-background text-foreground" suppressHydrationWarning>
      {/* Desktop Sidebar */}
      <div
        className={`hidden shrink-0 overflow-hidden transition-all duration-300 ease-in-out lg:block ${
          desktopSidebarOpen ? "lg:w-[13.5rem]" : "lg:w-0"
        }`}
      >
        <Sidebar
          threads={threads}
          currentThreadId={currentThreadId}
          onNewChat={handleNewChat}
          onSelectThread={handleSelectThread}
          onDeleteThread={handleDeleteThread}
          onRenameThread={handleRenameThread}
          onCollapse={() => setDesktopSidebarOpen(false)}
          onOpenSettings={openSettingsPanel}
          mode={settingsPanelOpen ? "settings" : "chat"}
          settingsTab={settingsPanelTab}
          onSelectSettingsTab={selectSettingsTab}
          onBackToChat={closeSettingsPanel}
        />
      </div>

      {/* Main Content */}
      <div className="flex min-w-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col">
          <div className="pointer-events-none absolute left-4 top-4 z-20 flex gap-2">
            <button
              type="button"
              onClick={() => setMobileSidebarOpen(true)}
              className="btn-icon pointer-events-auto flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-(--border) bg-(--card)/95 text-(--muted) shadow-sm backdrop-blur-sm transition-colors hover:bg-background hover:text-foreground lg:hidden"
              aria-label="Open sidebar"
            >
              <SidebarToggleIcon direction="open" className="h-4 w-4" />
            </button>
            {!desktopSidebarOpen && (
              <button
                type="button"
                onClick={() => setDesktopSidebarOpen(true)}
                className="btn-icon pointer-events-auto hidden h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-(--border) bg-(--card)/95 text-(--muted) shadow-sm backdrop-blur-sm transition-colors hover:bg-background hover:text-foreground lg:flex"
                aria-label="Open sidebar"
              >
                <SidebarToggleIcon direction="open" className="h-4 w-4" />
              </button>
            )}
          </div>

          {settingsPanelOpen ? (
            <div className="flex-1 overflow-y-auto">
              <SettingsPanel
                isOpen={settingsPanelOpen}
                initialTab={settingsPanelTab}
                onTabChange={selectSettingsTab}
              />
            </div>
          ) : (
            <>
              <div
                ref={containerRef}
                className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent"
              >
                {messages.length === 0 ? (
                  <div className="flex h-full items-center justify-center">
                    <div className="w-full max-w-2xl px-4 text-center sm:px-6">
                      <div className="space-y-5">
                        <RaviMark className="mx-auto h-10 w-10 text-foreground sm:h-12 sm:w-12" />
                        <div>
                          <h2 className="text-xl font-semibold sm:text-2xl">How can I help you today?</h2>
                          <p className="mt-2 text-sm text-(--muted)">
                            Ask me anything and I&apos;ll keep the working area clean and focused.
                          </p>
                        </div>

                        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {([
                            { icon: Music2, text: "Play Despacito on Spotify" },
                            { icon: Mail, text: "Summarize my recent 5 emails" },
                            { icon: ListTodo, text: "Plan tasks to organise a birthday party" },
                            { icon: Clock, text: "What's the current time?" },
                            { icon: BarChart2, text: "Show a data visualisation" },
                          ] as { icon: LucideIcon; text: string }[]).map(({ icon: Icon, text }, idx) => (
                            <button
                              key={idx}
                              onClick={() => doSendMessage(text)}
                              className="flex cursor-pointer items-center gap-3 rounded-2xl p-3 text-left text-sm text-(--muted) transition-colors hover:bg-(--card-hover) active:scale-[0.98] sm:p-3.5"
                              style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
                            >
                              <Icon className="h-4 w-4 shrink-0 text-foreground" />
                              <span>{text}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="mx-auto w-full space-y-8 py-8">
                    {messages.map((m) => {
                      if (m.role === "tool_approval" && m.metadata) {
                        return (
                          <div key={m.id} className="px-4 sm:px-6">
                            <div className="mx-auto max-w-180">
                              <ToolApprovalCard
                                requestId={m.metadata.requestId as string}
                                toolName={m.metadata.toolName as string}
                                arguments={m.metadata.arguments as Record<string, unknown>}
                                context={m.metadata.context as string | undefined}
                                onRespond={respondToHITL}
                              />
                            </div>
                          </div>
                        );
                      }

                      if (m.role === "human_input" && m.metadata) {
                        return (
                          <div key={m.id} className="px-4 sm:px-6">
                            <div className="mx-auto max-w-180">
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
                        );
                      }

                      if (m.role === "tool_result") {
                        return null;
                      }

                      if (m.role === "user" || m.role === "assistant") {
                        return (
                          <MessageBubble
                            key={m.id}
                            role={m.role}
                            content={m.content}
                            attachments={m.attachments}
                            reasoning={m.reasoning}
                            timestamp={m.timestamp}
                            toolCalls={m.toolCalls}
                            isToolExecuting={m.isToolExecuting}
                            isContinuation={m.isContinuation}
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

                    {loading && !messages.some((m) => m.role === "assistant" && m.id === messages[messages.length - 1]?.id) && (
                      <div className="px-4 py-2 sm:px-6">
                        <div className="mx-auto flex max-w-180 items-center gap-2 py-2">
                          <div className="flex items-center gap-1.5">
                            <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-(--muted)" style={{ animationDelay: "0ms" }} />
                            <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-(--muted)" style={{ animationDelay: "150ms" }} />
                            <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-(--muted)" style={{ animationDelay: "300ms" }} />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="bg-background pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 sm:pb-5">
                <div className="mx-auto w-full max-w-2xl px-3 sm:px-6">
                  <form
                    onSubmit={sendMessage}
                    className="flex flex-col overflow-hidden px-4 py-3"
                    style={{
                      borderRadius: "24px",
                      background: "var(--card)",
                      boxShadow: "var(--shadow-md)",
                    }}
                  >
                    {attachedFiles.length > 0 && (
                      <div className="flex flex-wrap gap-2 pb-3">
                        {attachedFiles.map((file) => renderComposerAttachment(file))}
                      </div>
                    )}

                    <div className="flex items-end gap-2.5">
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={handleFileSelected}
                        aria-hidden="true"
                      />

                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploadingFile}
                        className="btn-icon mb-0.5 shrink-0 cursor-pointer self-end rounded-xl p-2 text-(--muted) transition-colors hover:bg-(--card-hover) disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label="Attach file"
                      >
                        {uploadingFile ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                      </button>

                      <textarea
                        ref={textareaRef}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            sendMessage(e);
                          }
                        }}
                        rows={1}
                        className="max-h-48 flex-1 resize-none overflow-y-auto bg-transparent py-2 text-[15px] outline-none placeholder:text-(--muted)"
                        placeholder="Ask anything…"
                        disabled={loading}
                      />

                      <div className="mb-0.5 flex shrink-0 items-center gap-1 self-end">
                        {!loading && (
                          <VoiceRecorder
                            onTranscript={(text) => setInput((prev) => (prev ? prev + " " + text : text))}
                            disabled={loading}
                          />
                        )}
                        {!loading && (
                          <button
                            type="button"
                            onClick={() => setRealtimeOpen(true)}
                            className="btn-icon cursor-pointer rounded-xl p-2 text-(--muted) transition-colors hover:bg-(--card-hover)"
                            aria-label="Start speech-to-speech conversation"
                            title="Live voice conversation"
                          >
                            <Radio className="h-4 w-4" />
                          </button>
                        )}
                        {loading ? (
                          <button
                            type="button"
                            onClick={handleStop}
                            className="btn-icon cursor-pointer rounded-full bg-foreground p-2 text-background transition-colors"
                            aria-label="Stop"
                          >
                            <StopCircle className="h-4 w-4" />
                          </button>
                        ) : (
                          <button
                            type="submit"
                            disabled={!input.trim()}
                            className="btn-icon cursor-pointer rounded-full p-2 transition-all disabled:cursor-not-allowed disabled:opacity-20"
                            style={{
                              background: input.trim() ? "var(--foreground)" : "var(--badge-bg)",
                              color: input.trim() ? "var(--background)" : "var(--muted)",
                            }}
                            aria-label="Send"
                          >
                            <Send className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </div>
                  </form>

                  <div className="mt-2 flex flex-wrap items-center gap-2 px-1 sm:gap-3">
                    <div className="relative" ref={modelPickerRef}>
                      <button
                        type="button"
                        onClick={() => {
                          setShowModelPicker((v) => !v);
                          setShowThinkingPicker(false);
                        }}
                        className="btn-icon flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium text-(--muted) transition-colors hover:bg-(--card-hover)"
                      >
                        <Settings2 className="h-3 w-3" />
                        <span className="max-w-32 truncate">
                          {CHAT_MODEL_OPTIONS.find((m) => m.id === selectedModel)?.label ?? selectedModel.split("/").pop()}
                        </span>
                        <ChevronDown className="h-3 w-3" />
                      </button>
                      {showModelPicker && (
                        <div
                          className="absolute bottom-full left-0 z-50 mb-2 w-64 overflow-hidden rounded-2xl p-1 shadow-2xl"
                          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
                        >
                          <div className="px-3 py-2 text-[11px] font-bold uppercase tracking-widest text-(--muted)">
                            Model
                          </div>
                          <div className="flex flex-col gap-0.5">
                            {CHAT_MODEL_OPTIONS.filter(opt => !opt.disabled).map((opt) => {
                              const active = opt.id === selectedModel;
                              return (
                                <button
                                  key={opt.id}
                                  type="button"
                                  onClick={() => {
                                    setSelectedModel(opt.id);
                                    writeStoredValue(CHAT_MODEL_STORAGE_KEY, opt.id);
                                    setShowModelPicker(false);
                                  }}
                                  className={`flex w-full cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-left transition-all ${active ? "bg-(--card-hover) text-foreground ring-1 ring-(--border)" : "text-(--muted) hover:bg-(--card-hover) hover:text-foreground"}`}
                                >
                                  <div className="flex items-center gap-2">
                                    <span className="text-[13.5px] font-medium tracking-tight">{opt.label}</span>
                                    {(opt.id.includes("5.4") || opt.id.includes("3.3")) && (
                                      <span className="rounded-md bg-(--badge-bg) px-1.5 py-0.5 text-[9px] font-bold text-(--muted) uppercase">New</span>
                                    )}
                                  </div>
                                  {active && <div className="h-1.5 w-1.5 rounded-full bg-foreground" />}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>

                    <span className="text-(--border)">·</span>

                    <div className="relative" ref={thinkingPickerRef}>
                      <button
                        type="button"
                        onClick={() => {
                          setShowThinkingPicker((v) => !v);
                          setShowModelPicker(false);
                        }}
                        className="btn-icon flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium text-(--muted) transition-colors hover:bg-(--card-hover)"
                      >
                        <span>💭</span>
                        <span>{thinkingLevel === "off" ? "No thinking" : `Thinking: ${thinkingLevel}`}</span>
                        <ChevronDown className="h-3 w-3" />
                      </button>
                      {showThinkingPicker && (
                        <div
                          className="absolute bottom-full left-0 z-50 mb-2 w-56 overflow-hidden rounded-2xl p-1 shadow-2xl"
                          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
                        >
                          <div className="px-3 py-2 text-[11px] font-bold uppercase tracking-widest text-(--muted)">
                            Thinking
                          </div>
                          <div className="flex flex-col gap-0.5">
                            {(CHAT_MODEL_OPTIONS.find(m => m.id === selectedModel)?.thinkingLevels || ["off", "low", "medium", "high"]).map((level) => {
                              const labels: Record<string, string> = {
                                off: "No thinking",
                                low: "Fast Reasoning",
                                medium: "Balanced",
                                high: "Deep Thought",
                                xhigh: "Maximum Depth"
                              };
                              return (
                                <button
                                  key={level}
                                  type="button"
                                  onClick={() => {
                                    setThinkingLevel(level);
                                    setShowThinkingPicker(false);
                                  }}
                                  className={`flex w-full cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-left transition-all ${level === thinkingLevel ? "bg-(--card-hover) text-foreground ring-1 ring-(--border)" : "text-(--muted) hover:bg-(--card-hover) hover:text-foreground"}`}
                                >
                                  <span className="text-[13.5px] font-medium tracking-tight">{labels[level] || level}</span>
                                  {level === thinkingLevel && <div className="h-1.5 w-1.5 rounded-full bg-foreground" />}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  <RealtimeVoicePanel isOpen={realtimeOpen} onClose={() => setRealtimeOpen(false)} />
                  <p className="mt-2 text-center text-xs text-(--muted)">
                    AI can make mistakes. Verify important information.
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        {!settingsPanelOpen && (
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
        )}
      </div>

      {/* Mobile Sidebar Drawer */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileSidebarOpen(false)}
            aria-label="Close sidebar"
          />
          <div className="relative h-full w-[min(13.5rem,calc(100vw-1rem))] max-w-full">
            <Sidebar
              threads={threads}
              currentThreadId={currentThreadId}
              onNewChat={handleNewChat}
              onSelectThread={handleSelectThread}
              onDeleteThread={handleDeleteThread}
              onRenameThread={handleRenameThread}
              onCollapse={() => setMobileSidebarOpen(false)}
              onOpenSettings={openSettingsPanel}
              mode={settingsPanelOpen ? "settings" : "chat"}
              settingsTab={settingsPanelTab}
              onSelectSettingsTab={selectSettingsTab}
              onBackToChat={() => {
                setMobileSidebarOpen(false);
                closeSettingsPanel();
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-dvh items-center justify-center bg-background">
          <Loader2 className="w-6 h-6 animate-spin text-(--muted)" />
        </div>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}