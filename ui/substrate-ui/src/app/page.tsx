"use client";

import React, { Suspense, useState, useRef, useEffect, useCallback } from "react";
import { nanoid } from "nanoid";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { MessageBubble } from "@/components/MessageBubble";
import { RaviMark } from "@/components/RaviMark";
import { ToolApprovalCard } from "@/components/ToolApprovalCard";
import { HumanInputCard } from "@/components/HumanInputCard";
import { MaxIterationsCard } from "@/components/MaxIterationsCard";
import { AppPanel } from "@/components/AppPanel";
import { Sidebar } from "@/components/Sidebar";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { SettingsPanel } from "@/components/SettingsPanel";
import { ModelEffortPicker } from "@/components/ModelEffortPicker";
import type { SettingsTab } from "@/components/SettingsPanel";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { RealtimeVoicePanel } from "@/components/RealtimeVoicePanel";
import { Message, UploadedFile } from "@/types";
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
import type { WireEvent } from "@/protocol";
import { wireEventToLegacy } from "@/lib/stream/legacyAdapter";
import { useFileAttachments, type AttachedFilePreview } from "@/hooks/useFileAttachments";
import { useAppPanel } from "@/hooks/useAppPanel";
import { Send, Plus, Music2, Mail, ListTodo, Clock, BarChart2, StopCircle, Loader2, X, Radio, ChevronDown, Settings2, AudioLines, ArrowUp, type LucideIcon } from "lucide-react";

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

  const containerRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Tracks the active AbortController for the current SSE fetch so we can
  // cancel the stream when the user clicks Stop.
  const wsRef = useRef<AbortController | null>(null);
  // Tracks the threadId of the currently active stream, useful for stopping a run
  // on a brand new thread before it has been persisted to the URL state.
  const activeStreamThreadIdRef = useRef<string | null>(null);
  // Prevents the currentThreadId useEffect from wiping the UI when we officially
  // update the URL to the new thread ID at the end of the first message stream.
  const isNavigatingToNewThread = useRef(false);

  // ── Realtime speech-to-speech panel ────────────────────────────────
  const [realtimeOpen, setRealtimeOpen] = useState(false);

  // ── Model selector + thinking level ────────────────────────────────
  const [selectedModel, setSelectedModel] = useState(() => getPreferredChatModel());
  const [thinkingLevel, setThinkingLevel] = useState<string>("medium");

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
        const i = document.createElement("iframe");
        i.style.display = "none";
        document.body.appendChild(i);
        i.contentWindow?.history.pushState.call(window.history, null, "", nextUrl);
        document.body.removeChild(i);
      } else {
        const i = document.createElement("iframe");
        i.style.display = "none";
        document.body.appendChild(i);
        i.contentWindow?.history.replaceState.call(window.history, null, "", nextUrl);
        document.body.removeChild(i);
      }
    },
    [updateLastActiveThreadId],
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

  type ManifestEntry = { tool_name: string; http_url: string; resource_uri: string };
  const [mcpManifest, setMcpManifest] = useState<ManifestEntry[]>([]);
  useEffect(() => {
    fetch("/api/backend/mcp-apps/manifest")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: ManifestEntry[]) => setMcpManifest(data))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (authLoading) {
      return;
    }

    if (wasAuthenticatedRef.current && !isAuthenticated) {
      // Clear persisted thread so it doesn't leak into the next session
      updateLastActiveThreadId(null);
      setMessages([]);
      setPanelItems([]);
      setActivePanelId(null);
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
    _handleSelectThread(threadId, { onSelected: () => { setMobileSidebarOpen(false); } });
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

  // Load messages when thread changes, and reset panel/task state.
  // When doSendMessage creates a new thread the AbortController is already
  // assigned before React runs this effect, so wsRef.current is non-null —
  // we use that to skip the panel wipe during an active stream.
  useEffect(() => {
    if (isNavigatingToNewThread.current) {
      // We just officially entered the thread we created.
      // Do not wipe anything or load messages from DB, because we already have them!
      isNavigatingToNewThread.current = false;
      return;
    }

    clearAttachedFiles();
    if (currentThreadId && canLoadData) {
      // loadMessages already guards against overwriting optimistic messages
      // while a stream is active (it checks wsRef.current internally).
      loadMessages(currentThreadId);
      // Only reset the panel when there is no active stream; this preserves
      // the kanban/MCP panel when doSendMessage creates a thread on first send.
      if (!wsRef.current) {
        setPanelItems([]);
        setActivePanelId(null);
      }
      return;
    }

    if (!wsRef.current) {
      setMessages([]);
      setPanelItems([]);
      setActivePanelId(null);
    }
  }, [canLoadData, clearAttachedFiles, currentThreadId, setPanelItems, setActivePanelId]);

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

  // MCP App: handle context updates and ui/message triggers from interactive widgets
  async function handleMcpAppResult(toolName: string, result: unknown) {
    // If a widget (e.g. kanban Retry button) sends ui/message with role:"user",
    // submit it as a regular user chat message so the agent can act on it.
    if (result && typeof result === "object") {
      const r = result as { type?: string; role?: string; content?: string };
      if (r.type === "message" && r.role === "user" && r.content) {
        doSendMessage(r.content);
        return;
      }
    }
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
    const cancelId = currentThreadId || activeStreamThreadIdRef.current;
    if (cancelId) {
      api.cancelChat(cancelId).catch((error: unknown) => {
        console.error("Failed to cancel active run:", error);
      });
    }
  }

  async function doSendMessage(text: string) {
    if (!text.trim() || loading) return;

    const currentInput = text;
    const currentFileIds = attachedFiles.map((f) => f.id);
    const requestedModel = selectedModel;
    const currentAttachments: UploadedFile[] = attachedFiles.map((file) => ({
      id: file.id,
      thread_id: file.thread_id,
      name: file.name,
      mime: file.mime,
      size: file.size,
      url: file.url,
    }));

    // ── Mutable bubble tracking ──────────────────────────────────────────
    // After a step that uses tools (and triggers HITL cards), the agent's
    // NEXT text response must appear BELOW those cards — not update the old
    // placeholder that sits above them. We track this with a plain mutable
    // object (not React state) so handlers can mutate it synchronously.
    const msgState = {
      activeAssistantId: nanoid() as string,
      needsNewBubble: false,
    };

    // Clear input and show user message AND assistant placeholder immediately!
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
      { 
        id: msgState.activeAssistantId, 
        role: "assistant" as const, 
        content: "", 
        reasoning: "", 
        timestamp: new Date() 
      }
    ]);
    setLoading(true);

    // Ensure a thread exists before opening the stream
    let threadId = currentThreadId;
    let isNewThread = false;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        threadId = newThread.id;
        isNewThread = true;
        // DO NOT call selectThread or setThreads here! We want to keep the UI perfectly 
        // stable without triggering route transitions or sidebar layout shifts during stream start.
        // We will update the URL and sidebar when the stream is completed.
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

    activeStreamThreadIdRef.current = threadId;

    // Update thread name on the first message
    if (messages.length === 0) {
      const name = currentInput.slice(0, 50) + (currentInput.length > 50 ? "..." : "");
      handleRenameThread(threadId, name);
    }

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

      // ── Interactive UI (MCP Apps) — the narrow waist ────────────────
      // ANY rich tool UI (kanban, chart, form, map, …) arrives as one event.
      // Open/update a sandboxed iframe for `uri`, keyed by the resource so
      // repeat events coalesce onto the same panel and stream fresh data in.
      if (data.type === "ui_resource") {
        const uri = data.uri as string;
        const name = uri.replace(/^ui:\/\//, "");
        openInPanel({
          id: `ui-${uri}`,
          httpUrl: `/ui/${name}`,
          toolName: name,
          toolArguments: (data.structured_content as Record<string, unknown>) || {},
          timestamp: Date.now(),
        });
        setPanelCollapsed(false);
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

        const rawAttachments = (data.attachments as unknown[]) ?? [];
        const attachments = rawAttachments.map((a) => {
          const fileObj = a as { id: string; thread_id: string; name: string; mime?: string; size?: number; url?: string };
          return {
            id: fileObj.id,
            thread_id: fileObj.thread_id,
            name: fileObj.name,
            mime: fileObj.mime || "application/octet-stream",
            size: fileObj.size || 0,
            url: fileObj.url || `/api/backend/threads/${fileObj.thread_id}/files/${fileObj.id}/content`,
          };
        });

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
                attachments: attachments.length > 0 ? attachments : msg.attachments,
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
          if (isNewThread && !currentThreadId) {
            isNavigatingToNewThread.current = true;
            selectThread(threadId, "replace");
            isNewThread = false;
          }
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
          risk: data.risk as "safe" | "sensitive" | "critical" | undefined,
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
      if (data.type === "max_iterations") {
        finalizeAssistantMessages();
        setLoading(false);
        void loadThreads();
        if (isNewThread && !currentThreadId) {
          isNavigatingToNewThread.current = true;
          selectThread(threadId, "replace");
          isNewThread = false;
        }
        const cardId = nanoid();
        setMessages((m) => [
          ...m,
          { id: cardId, role: "max_iterations" as const, content: "", timestamp: new Date() },
        ]);
        return;
      }

      if (data.type === "agent.run_completed") {
        // Safety net: if no completion event fired (e.g. tool-only runs), stop loading.
        finalizeAssistantMessages();
        setLoading(false);
        void loadThreads();
        if (isNewThread && !currentThreadId) {
          isNavigatingToNewThread.current = true;
          selectThread(threadId, "replace");
          isNewThread = false;
        }
        return;
      }

      if (data.type === "agent.run_failed") {
        const errorMsg = String(data.error || "The agent encountered an error.");
        finalizeAssistantMessages((message) => ({
          ...message,
          content: message.content + "\n\n⚠️ " + errorMsg,
        }));
        setLoading(false);
        if (isNewThread && !currentThreadId) {
          isNavigatingToNewThread.current = true;
          selectThread(threadId, "replace");
          isNewThread = false;
        }
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
          let parsed: WireEvent;
          try { parsed = JSON.parse(text) as WireEvent; } catch { continue; }
          // New wire protocol → the UI's existing event shapes (single adapter).
          for (const legacy of wireEventToLegacy(parsed)) processEvent(legacy);
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
          <div className="ravi-fade-up w-14 h-14 mx-auto rounded-2xl flex items-center justify-center text-xl font-bold bg-foreground text-background">
            R
          </div>
          <div className="ravi-fade-up" style={{ '--stagger': 1 } as React.CSSProperties}>
            <h1 className="text-2xl font-semibold">Welcome</h1>
            <p className="text-sm mt-2 text-(--muted)">
              Sign in to start chatting with your AI assistant
            </p>
          </div>
          {authNotice && (
            <div className="ravi-fade-up rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200" style={{ '--stagger': 2 } as React.CSSProperties}>
              {authNotice}
            </div>
          )}
          <button
            onClick={loginWithGoogle}
            className="ravi-fade-up ravi-press flex items-center gap-3 mx-auto px-6 py-3 bg-white text-gray-800 rounded-2xl text-sm font-semibold hover:bg-gray-50 transition-colors cursor-pointer"
            style={{ '--stagger': 3, boxShadow: "var(--shadow-md)" } as React.CSSProperties}
          >
            {/* Google G */}
            <svg className="w-5 h-5" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Continue with Google
          </button>
          <p className="ravi-fade-up text-xs text-(--muted)" style={{ '--stagger': 4 } as React.CSSProperties}>
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
        className={`hidden shrink-0 overflow-hidden transition-all duration-300 ease-in-out lg:block ${desktopSidebarOpen ? "lg:w-[20rem]" : "lg:w-0"
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
                        <RaviMark className="ravi-fade-up mx-auto h-10 w-10 text-foreground sm:h-12 sm:w-12" />
                        <div className="ravi-fade-up" style={{ '--stagger': 1 } as React.CSSProperties}>
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
                              className="ravi-pop-in ravi-press flex cursor-pointer items-center gap-3 rounded-2xl p-3 text-left text-sm text-(--muted) transition-colors hover:bg-(--card-hover) sm:p-3.5"
                              style={{ '--stagger': idx + 2, background: "var(--card)", boxShadow: "var(--shadow-sm)" } as React.CSSProperties}
                            >
                              <Icon className="h-4 w-4 shrink-0 text-foreground" />
                              <span>{text}</span>
                            </button>
                          ))}
                        </div>

                        {/* MCP App launchers — direct open, no agent needed */}
                        {mcpManifest.length > 0 && (
                          <div className="mt-3">
                            <p className="mb-2 text-xs text-(--muted) font-medium uppercase tracking-wider">Apps</p>
                            <div className="flex flex-wrap gap-2">
                              {mcpManifest.map((entry) => {
                                const label = entry.tool_name.replace(/_/g, " ");
                                return (
                                  <button
                                    key={entry.resource_uri}
                                    onClick={() => {
                                      const name = entry.resource_uri.replace(/^ui:\/\//, "");
                                      openInPanel({
                                        id: `ui-${entry.resource_uri}`,
                                        httpUrl: `/ui/${name}`,
                                        toolName: name,
                                        toolArguments: {},
                                        timestamp: Date.now(),
                                      });
                                      setPanelCollapsed(false);
                                    }}
                                    className="ravi-press flex items-center gap-1.5 rounded-xl border border-(--border) bg-(--card) px-3 py-1.5 text-xs text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
                                    style={{ boxShadow: "var(--shadow-sm)" }}
                                  >
                                    <span className="h-1.5 w-1.5 rounded-full bg-(--accent) opacity-60" />
                                    {label}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="mx-auto w-full space-y-8 py-8">
                    {messages.map((m) => {
                      if (m.role === "tool_approval" && m.metadata) {
                        return (
                          <div key={m.id} className="px-4 sm:px-6">
                            <div className="mx-auto max-w-(--chat-width)">
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
                            <div className="mx-auto max-w-(--chat-width)">
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

                      if (m.role === "max_iterations") {
                        return (
                          <div key={m.id} className="px-4 sm:px-6">
                            <div className="mx-auto max-w-(--chat-width)">
                              <MaxIterationsCard
                                onContinue={() =>
                                  doSendMessage(
                                    "Continue completing the remaining tasks. Check the existing task board and proceed with any unfinished tasks."
                                  )
                                }
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
                        <div className="mx-auto flex max-w-(--chat-width) items-center gap-2 py-2">
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
                <div className="mx-auto w-full max-w-(--chat-width) px-3 sm:px-6">
                  <form
                    onSubmit={sendMessage}
                    className="flex flex-col overflow-hidden rounded-[20px] px-3.5 py-2.5 sm:rounded-[24px]"
                    style={{
                      background: "var(--card)",
                      boxShadow: "var(--shadow-md)",
                    }}
                  >
                    {attachedFiles.length > 0 && (
                      <div className="flex flex-wrap gap-2 pb-3 pt-1">
                        {attachedFiles.map((file) => renderComposerAttachment(file))}
                      </div>
                    )}

                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      className="hidden"
                      onChange={handleFileSelected}
                      aria-hidden="true"
                    />

                    {/* Textarea — full width on top */}
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
                      className="max-h-48 w-full resize-none overflow-y-auto bg-transparent px-2 py-2.5 text-[15px] outline-none placeholder:text-(--muted)"
                      placeholder="Ask anything"
                      disabled={loading}
                    />

                    {/* ── Bottom bar: [+] left · [model][mic][audio/send] right ── */}
                    <div className="flex items-center gap-1.5 px-1 pb-0.5 pt-0.5">
                      {/* Attach — left */}
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploadingFile}
                        className="btn-icon flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full text-(--muted) transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                        style={{ background: "var(--card-hover)" }}
                        aria-label="Attach file"
                      >
                        {uploadingFile ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-5 w-5" />}
                      </button>

                      {/* Right group */}
                      <div className="ml-auto flex items-center gap-1.5">
                        <ModelEffortPicker
                          models={CHAT_MODEL_OPTIONS}
                          selectedModel={selectedModel}
                          onSelectModel={(id) => { setSelectedModel(id); writeStoredValue(CHAT_MODEL_STORAGE_KEY, id); }}
                          thinkingLevel={thinkingLevel}
                          onSelectThinking={setThinkingLevel}
                        />

                        {input.trim() || loading ? (
                          loading ? (
                            <button
                              type="button"
                              onClick={handleStop}
                              className="btn-icon ravi-press flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-foreground text-background transition-colors"
                              aria-label="Stop"
                            >
                              <StopCircle className="h-4 w-4" />
                            </button>
                          ) : (
                            <button
                              type="submit"
                              disabled={!input.trim()}
                              className="btn-icon ravi-press flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-foreground text-background transition-all disabled:cursor-not-allowed disabled:opacity-10"
                              aria-label="Send"
                            >
                              <ArrowUp className="h-5 w-5" />
                            </button>
                          )
                        ) : (
                          <>
                            <div className="rounded-full" style={{ background: "var(--card-hover)" }}>
                              <VoiceRecorder
                                onTranscript={(text) => setInput((prev) => (prev ? prev + " " + text : text))}
                                disabled={loading}
                                className="h-9 w-9"
                              />
                            </div>
                            <button
                              type="button"
                              onClick={() => setRealtimeOpen(true)}
                              className="btn-icon flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-foreground text-background transition-transform active:scale-95"
                              aria-label="Start speech-to-speech conversation"
                              title="Live voice conversation"
                            >
                              <AudioLines className="h-4 w-4" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>{/* end bottom bar */}
                  </form>

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
        <div className="ravi-fade-in fixed inset-0 z-40 lg:hidden">
          {/* Backdrop */}
          <button
            type="button"
            className="absolute inset-0 cursor-pointer"
            style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)" }}
            onClick={() => setMobileSidebarOpen(false)}
            aria-label="Close sidebar"
          />
          {/* Sidebar sheet — slides in from the left */}
          <div
            className="ravi-slide-in-left relative flex h-full flex-col"
            style={{ width: "min(360px, 88vw)" }}
          >
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
