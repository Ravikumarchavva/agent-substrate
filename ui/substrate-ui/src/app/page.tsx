"use client";

import React, { Suspense, useState, useRef, useEffect, useCallback, useMemo } from "react";
import { nanoid } from "nanoid";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { MessageBubble } from "@/components/MessageBubble";
import { SubstrateMark } from "@/components/SubstrateMark";
import { ToolApprovalCard } from "@/components/ToolApprovalCard";
import { HumanInputCard } from "@/components/HumanInputCard";
import { MaxIterationsCard } from "@/components/MaxIterationsCard";
import { CircularProgress } from "@/components/CircularProgress";
import { AppPanel } from "@/components/AppPanel";
import { Sidebar } from "@/components/Sidebar";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import { SettingsPanel } from "@/components/SettingsPanel";
import { ScheduledPanel } from "@/components/ScheduledPanel";
import { ModelEffortPicker } from "@/components/ModelEffortPicker";
import type { SettingsTab } from "@/components/SettingsPanel";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { RealtimeVoicePanel } from "@/components/RealtimeVoicePanel";
import { Message, UploadedFile, TaskList, CitationSource } from "@/types";
import { api } from "@/lib/api";
import { ChatConflictError } from "@/lib/api/chat";
import { getMessageAttachments, buildWorkspaceFileUrl } from "@/lib/api/_client";
import { mergeSources, parseCitations } from "@/lib/citations";
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
import {
  useFileAttachments,
  type AttachedFilePreview,
  getAttachmentProcessingState,
  computeSimulatedProgress,
} from "@/hooks/useFileAttachments";
import { useAppPanel } from "@/hooks/useAppPanel";
import { useTaskBoards } from "@/hooks/useTaskBoards";
import { PlanCardStack } from "@/components/PlanCard";
import { Send, Plus, Music2, Mail, ListTodo, Clock, BarChart2, StopCircle, Loader2, X, Radio, ChevronDown, Settings2, AudioLines, ArrowUp, SquarePen, type LucideIcon } from "lucide-react";

const LAST_ACTIVE_THREAD_STORAGE_KEY = "substrate:last-active-thread";

// Paste-to-document: a paste this long or longer becomes an attached
// document (chunked+embedded via the same staging pipeline a manual
// upload uses — see EXTRACTABLE_CONTENT_TYPES in agent-substrate) instead
// of dumping raw text into the composer. Matches ChatGPT/Claude.ai's own
// paste-to-artifact threshold — roughly one screen of text.
const PASTE_TO_DOCUMENT_THRESHOLD = 2000;

function buildPastedDocumentFile(text: string): File {
  const name = `pasted-${Date.now()}.md`;
  return new File([text], name, { type: "text/markdown" });
}

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

// The first non-image `sandbox:` file ref in an assistant message — the "main
// artifact" to auto-open in the side panel (Claude-style). Images use the
// `![](sandbox:)` form and render inline, so the leading-`!` case is excluded.
const ARTIFACT_LINK_RE = /(^|[^!])\[[^\]]*\]\(sandbox:([^)\s]+)\)/;
function firstArtifactRef(content: string): string | null {
  const m = content.match(ARTIFACT_LINK_RE);
  return m ? m[2].replace(/^\.?\//, "") : null;
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
  // True while a send is queued behind still-processing attachments (see
  // doSendMessage) — the button shows a spinner but stays in "send" mode,
  // not "stop" (loading, which drives that, isn't set yet since no stream
  // has actually started).
  const [sendQueued, setSendQueued] = useState(false);
  // True while the run is suspended waiting on a HITL card (ask_human / approval).
  // The run is parked server-side (zero compute) — the UI must show a calm
  // "your turn" state, not the active "running" spinners.
  const [hitlPending, setHitlPending] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [lastActiveThreadId, setLastActiveThreadId] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const wasAuthenticatedRef = useRef(false);

  const [scheduledPanelOpen, setScheduledPanelOpen] = useState(false);
  const [scheduledCount, setScheduledCount] = useState(0);

  const updateLastActiveThreadId = useCallback((threadId: string | null) => {
    setLastActiveThreadId(threadId);
    writeLastActiveThreadId(threadId);
  }, []);

  const currentThreadId = routeState.threadId ?? lastActiveThreadId;

  // Poll for active scheduled tasks count
  useEffect(() => {
    if (!isAuthenticated || authLoading) return;

    const updateCount = async () => {
      try {
        const tasks = await api.getScheduledTasks();
        const activeCount = tasks.filter(t => t.status === "active").length;
        setScheduledCount(activeCount);
      } catch (err) {
        console.error("Failed to fetch scheduled count:", err);
      }
    };

    updateCount();
    const interval = setInterval(updateCount, 30000);
    return () => clearInterval(interval);
  }, [isAuthenticated, authLoading]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  // Tracks whether the user is currently near the bottom of the scroll
  // container. Read (not state) so it doesn't trigger re-renders on scroll.
  const isNearBottomRef = useRef(true);
  const autoScrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Tracks the active AbortController for the current SSE fetch so we can
  // cancel the stream when the user clicks Stop.
  const wsRef = useRef<AbortController | null>(null);
  const isSubmittingRef = useRef(false);
  // Tracks the threadId of the currently active stream, useful for stopping a run
  // on a brand new thread before it has been persisted to the URL state.
  const activeStreamThreadIdRef = useRef<string | null>(null);
  // After a stream ends, holds the thread that was streamed so loadMessages can
  // skip overwriting the in-memory messages (which are more current than the DB).
  // Cleared when the user navigates to a different thread.
  const streamedThreadRef = useRef<string | null>(null);
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
      setScheduledPanelOpen(false);
    }
  }, [routeState.threadId, updateLastActiveThreadId]);

  const selectThread = useCallback(
    (threadId: string | null, mode: "replace" | "push" = "replace") => {
      updateLastActiveThreadId(threadId);
      setScheduledPanelOpen(false);
      const nextUrl = buildChatPath(threadId);
      // A real Next.js navigation here (router.push/replace) changes the
      // optional-catch-all slug and REMOUNTS this whole page component,
      // wiping `messages` back to [] for a tick before loadMessages'
      // fetch resolves — the "no conversation for a second" flash when
      // switching threads from the sidebar. Raw History API updates the
      // URL bar with no remount; the currentThreadId-change effect below
      // still picks up the new thread (via lastActiveThreadId, updated
      // above) and calls loadMessages normally. Same technique
      // promoteThreadUrl already uses for the brand-new-thread case.
      if (mode === "push") {
        window.history.pushState(window.history.state, "", nextUrl);
      } else {
        window.history.replaceState(window.history.state, "", nextUrl);
      }
    },
    [updateLastActiveThreadId],
  );

  // Promote a freshly-created thread into the URL. Called immediately after
  // thread creation (the thread durably exists server-side by then — a run
  // can suspend indefinitely waiting on ask_human before any
  // turn.completed/run.completed event fires, so deferring until "stream
  // completed" left a stale /chat URL, and a refresh mid-suspend, for as
  // long as the human hadn't answered yet).
  // We deliberately use history.replaceState instead of router.replace: a real
  // Next.js navigation from /chat → /chat/{id} changes the optional-catch-all
  // slug and REMOUNTS this page, which wipes the in-flight (and not-yet-persisted)
  // assistant message. history.replaceState updates the URL bar with no remount.
  const promoteThreadUrl = useCallback(
    (threadId: string) => {
      // Mark this thread as owning the current in-memory messages so the
      // thread-change effect's loadMessages won't overwrite them with a stale
      // (or empty) DB snapshot before persistence catches up.
      streamedThreadRef.current = threadId;
      isNavigatingToNewThread.current = true;
      updateLastActiveThreadId(threadId);
      window.history.replaceState(window.history.state, "", buildChatPath(threadId));
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

  const handleOpenScheduled = useCallback(() => {
    setScheduledPanelOpen(true);
    setMobileSidebarOpen(false);
    if (settingsPanelOpen) {
      closeSettingsPanel();
    }
  }, [settingsPanelOpen, closeSettingsPanel]);

  const handleCloseScheduled = useCallback(() => {
    setScheduledPanelOpen(false);
  }, []);

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
  const { attachedFiles, uploadingFile, fileInputRef, clearAttachedFiles, handleFileSelected, handleFilesPasted, handleRemoveFile, waitForAttachmentsReady } = useFileAttachments(currentThreadId, promoteThreadUrl, setThreads);
  const { panelItems, setPanelItems, activePanelId, setActivePanelId, panelCollapsed, setPanelCollapsed, openInPanel, closePanelItem, closeAllPanels } = useAppPanel();
  const { boards, upsertBoard, clearBoards, settleBoards } = useTaskBoards(currentThreadId);
  // Tracks which assistant messages we've already auto-opened an artifact for.
  const autoOpenedArtifactRef = useRef<Set<string>>(new Set());

  // Open a code-interpreter file (a `sandbox:` ref) in the side-panel artifact
  // viewer. The cache-bust (`&v=`) makes the panel remount the viewer when the
  // same file is re-opened after a change.
  const openArtifact = useCallback(
    (
      path: string,
      fileName?: string,
      threadOverride?: string | null,
      page?: number | null,
    ) => {
      const tid = threadOverride ?? currentThreadId;
      if (!tid) return;
      const cleanPath = path.replace(/^sandbox:/, "").replace(/^\.?\//, "");
      const name = fileName || cleanPath.split("/").pop() || cleanPath;
      // A citation open uses a STABLE cache-bust (not Date.now()) so
      // re-clicking a different page of the same PDF hits the browser cache
      // instead of re-downloading it — the panel still remounts because
      // AppPanel keys the viewer on `id:fileUrl`, and the #page= fragment
      // (appended last, after &v=) is part of that fileUrl. A regular
      // code-interpreter artifact open keeps Date.now(): that file's bytes
      // may have just changed and must never serve a stale cached copy.
      const bust = page ? "cite" : Date.now();
      const base = `${buildWorkspaceFileUrl(tid, cleanPath)}&v=${bust}`;
      openInPanel({
        id: `file-${cleanPath}`,
        kind: "file",
        httpUrl: "",
        toolName: "code_interpreter",
        toolArguments: {},
        timestamp: Date.now(),
        fileUrl: page ? `${base}#page=${page}` : base,
        fileName: name,
        mime: name.toLowerCase().endsWith(".pdf") ? "application/pdf" : undefined,
      });
      // Collapse the left thread rail so the artifact gets more room.
      setDesktopSidebarOpen(false);
    },
    [currentThreadId, openInPanel],
  );

  // Opens a grounded citation's source file at its cited page. sessionPath
  // falls back to fileName (agent-substrate's build_citations does the same
  // fallback server-side) so a citation still opens something sensible even
  // when the underlying upload predates session_path being recorded.
  const openSource = useCallback(
    (source: CitationSource) => {
      openArtifact(
        source.sessionPath || source.fileName,
        source.fileName,
        source.threadId ?? currentThreadId,
        source.page ?? null,
      );
    },
    [openArtifact, currentThreadId],
  );
  // agentId → id of the user message whose turn created the plan, so the inline
  // PlanCard renders in flow beneath that turn.
  const [boardAnchors, setBoardAnchors] = useState<Map<string, string>>(new Map());
  // Anchors are per-thread (message ids); drop them when the thread changes.
  useEffect(() => {
    setBoardAnchors(new Map());
  }, [currentThreadId]);

  // Group plan boards by the message they render beneath. A board anchors to
  // the user message whose turn created it: prefer the board's persisted
  // created_at (the user message just before it), so the anchor survives reload
  // and thread promotion (which clears the live boardAnchors map). Fall back to
  // the live anchor, then to the most recent user message.
  const boardsByAnchor = useMemo(() => {
    const result = new Map<string, TaskList[]>();
    if (boards.size === 0) return result;
    const userMessages = messages.filter((m) => m.role === "user");
    const lastUserId = userMessages[userMessages.length - 1]?.id;

    const anchorByTime = (createdAt?: string): string | undefined => {
      if (!createdAt) return undefined;
      const created = new Date(createdAt).getTime();
      if (Number.isNaN(created)) return undefined;
      // Latest user message sent at or before the board's creation time.
      let anchor: string | undefined;
      for (const m of userMessages) {
        if (m.timestamp.getTime() <= created + 1000) anchor = m.id;
        else break;
      }
      return anchor;
    };

    for (const tl of boards.values()) {
      const anchor =
        anchorByTime(tl.created_at) ?? boardAnchors.get(tl.agent_id) ?? lastUserId;
      if (!anchor) continue;
      const arr = result.get(anchor) ?? [];
      arr.push(tl);
      result.set(anchor, arr);
    }
    return result;
  }, [boards, boardAnchors, messages]);

  type ManifestEntry = { tool_name: string; http_url: string; resource_uri: string };
  const [mcpManifest, setMcpManifest] = useState<ManifestEntry[]>([]);
  useEffect(() => {
    fetch("/chat/api/backend/mcp-apps/manifest")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: ManifestEntry[]) => setMcpManifest(data))
      .catch(() => { });
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
      clearBoards();
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
  }, [authLoading, clearBoards, isAuthenticated, pathname, router, routeState.threadId, setActivePanelId, setPanelItems, settingsPanelOpen, updateLastActiveThreadId]);

  const renderComposerAttachment = useCallback((file: AttachedFilePreview) => {
    const AttachmentIcon = getAttachmentIcon(file.previewKind);
    const previewSource = file.previewUrl || file.url || (currentThreadId
      ? `/api/backend/threads/${currentThreadId}/files/${file.id}/content`
      : "");
    const processingState = getAttachmentProcessingState(file);
    const showRing = processingState === "pending" || processingState === "error";

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

        {showRing && (
          <div className="absolute bottom-2 right-2 z-10 rounded-full bg-background/85 p-0.5">
            <CircularProgress
              progress={computeSimulatedProgress(file)}
              state={processingState === "error" ? "error" : "pending"}
              size={18}
            />
          </div>
        )}

        <div className="min-w-0 flex-1 pr-7">
          <div className="truncate text-sm font-semibold text-foreground">{file.name}</div>
          <div className="mt-1 text-[11px] text-(--muted)">
            {processingState === "error"
              ? <span className="text-red-400">{file.stagingError}</span>
              : processingState === "pending"
                ? "Processing…"
                : formatFileSize(file.size)}
          </div>
        </div>
      </div>
    );
  }, [currentThreadId, handleRemoveFile]);

  // Wrap hook handlers to also manage local page state
  const handleNewChat = useCallback(async () => {
    setScheduledPanelOpen(false);
    await _handleNewChat({ onCreated: () => { setMessages([]); setMobileSidebarOpen(false); } });
  }, [_handleNewChat]);

  const handleSelectThread = useCallback((threadId: string) => {
    setScheduledPanelOpen(false);
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

    // Navigating to a different thread: clear the stream guard so the next
    // loadMessages call will fetch from DB rather than skip.
    if (currentThreadId !== streamedThreadRef.current) {
      streamedThreadRef.current = null;
      // isNearBottomRef persists for the page's lifetime, not per-thread —
      // without resetting it, scrolling up to read something in one thread
      // left it `false`, which then silently suppressed auto-scroll-to-bottom
      // in the NEXT thread opened, landing on whatever the first few lines
      // happened to be instead of the latest messages.
      isNearBottomRef.current = true;
    }

    clearAttachedFiles();
    if (currentThreadId && canLoadData) {
      // loadMessages guards against overwriting optimistic / just-streamed messages.
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
    // Auto-scroll to bottom whenever messages change — but only if the user
    // was already near the bottom. A burst of tool-call/result events (e.g.
    // 8+ tools running) fires this effect repeatedly; without the "near
    // bottom" check and without clearing the previous timeout, each firing
    // re-scheduled another forced scrollTo that fought the user's manual
    // scroll, making it impossible to scroll down to read a tool_approval /
    // human_input card that appeared below a long-running tool list.
    if (autoScrollTimeoutRef.current) {
      clearTimeout(autoScrollTimeoutRef.current);
    }
    const el = containerRef.current;
    if (!el || !isNearBottomRef.current) return;
    // Use setTimeout to ensure DOM is updated
    autoScrollTimeoutRef.current = setTimeout(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }, 100);
    return () => {
      if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
    };
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
      setMessages((current) => {
        // Active stream owns the message state — don't touch it.
        if (wsRef.current) return current;
        // This thread just finished streaming; in-memory messages are more
        // current than the DB snapshot (persistence may not have caught up).
        if (streamedThreadRef.current === threadId) return current;
        return fetchedMessages;
      });
    } catch (error) {
      console.error("Failed to load messages:", error);
      setMessages((current) => (wsRef.current ? current : []));
    }

    // Restore a still-pending ask_human card. input.requested is only ever
    // otherwise pushed into `messages` as a live SSE event — a human_input
    // card is never part of the persisted message history — so without
    // this, a thread with a durably-suspended run (waiting on a human,
    // possibly across a backend restart) loads with no visible card at all
    // even though the conversation is genuinely still waiting on the user.
    try {
      const { pending } = await api.getHitlStatus(threadId);
      if (pending.length > 0 && !wsRef.current && streamedThreadRef.current !== threadId) {
        const request = pending[0];
        setMessages((current) => {
          if (current.some((m) => m.metadata?.requestId === request.request_id)) {
            return current;
          }
          return [
            ...current,
            {
              id: nanoid(),
              role: "human_input" as const,
              content: "",
              timestamp: new Date(),
              metadata: {
                requestId: request.request_id,
                question: request.question,
                context: request.context,
                options: request.options,
                allowFreeform: request.allow_freeform,
              },
            },
          ];
        });
        setHitlPending(true);
      }
    } catch (error) {
      // Non-fatal: worst case the card doesn't restore and the user's
      // answer POST 404s, same as before this fallback existed.
      console.error("Failed to load HITL status:", error);
    }
  }

  // HITL: respond to a tool approval or human input request.
  // The Next.js route at /api/chat/respond/[requestId] proxies to the backend.
  async function respondToHITL(
    requestId: string,
    data: Record<string, unknown>
  ) {
    // Go back to the active "running" state until the next card or the
    // final answer arrives.
    setHitlPending(false);

    // Fast path: doSendMessage's stream is still live and connected — its
    // event_log.tail() already spans the whole suspend/resume gap, so it
    // will see whatever the agent does next with no help needed here. Just
    // POST the answer; tearing down a healthy connection to reconnect via
    // GET /stream/{threadId} would be pure overhead (and its own source of
    // bugs) for a case that already works.
    if (wsRef.current) {
      api.respondToHitl(requestId, data).catch((err: unknown) => {
        console.error("HITL respond failed:", err);
      });
      return;
    }

    // No live stream — most commonly a page refresh: the card itself was
    // restored from GET /hitl/status/{threadId} (see loadMessages), but
    // nothing has been tailing the run since. Reconnect FIRST, then POST
    // the answer, so nothing the resume produces lands in the gap between
    // the two (mirrors doSendMessage's own submit ordering).
    if (!currentThreadId) return;
    const threadId = currentThreadId;

    let lastUserMsgId = "";
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        lastUserMsgId = messages[i].id;
        break;
      }
    }

    const msgState = {
      activeAssistantId: nanoid() as string,
      // Always fresh — whatever comes next must render below the card
      // that's already visible, never back-fill some earlier bubble.
      needsNewBubble: true,
      userMsgId: lastUserMsgId,
      isNewThread: false,
      pendingSources: [] as CitationSource[],
    };

    await runEventStream(threadId, msgState, async (signal) => {
      const res = await api.streamThread(threadId, signal);
      api.respondToHitl(requestId, data).catch((err: unknown) => {
        console.error("HITL respond failed:", err);
      });
      return res;
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
    isSubmittingRef.current = false;
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
    if (!text.trim() || isSubmittingRef.current) return;
    isSubmittingRef.current = true;

    // Queued send: any attachment still eagerly processing (staged_at not
    // set yet — see useFileAttachments.ts) blocks the actual request from
    // firing, not the click itself. The button shows a spinner (sendQueued)
    // while this resolves; it's not "loading" (that would flip the button
    // into a Stop-stream affordance for a stream that hasn't started).
    // Covers every doSendMessage call site (retry, suggested prompts, voice
    // transcript, the composer's own submit) uniformly, so nothing needs
    // its own separate wait-and-retry logic.
    const stillProcessing = attachedFiles.some((f) => {
      const state = getAttachmentProcessingState(f);
      return state === "pending" || state === "error";
    });
    if (stillProcessing) {
      setSendQueued(true);
      const result = await waitForAttachmentsReady();
      setSendQueued(false);
      if (!result.ok) {
        isSubmittingRef.current = false;
        setMessages((prev) => [
          ...prev,
          {
            id: nanoid(),
            role: "assistant" as const,
            content: `⚠️ ${result.error}`,
            timestamp: new Date(),
          },
        ]);
        return;
      }
    }

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
    // isNewThread lives here too (not a separate local) so the shared
    // runEventStream can read/mutate it identically for both this call and
    // the HITL-reconnect call in respondToHITL.
    const msgState = {
      activeAssistantId: nanoid() as string,
      needsNewBubble: false,
      userMsgId: nanoid() as string,
      isNewThread: false,
      pendingSources: [] as CitationSource[],
    };

    // Clear input and show user message AND assistant placeholder immediately!
    setInput("");
    clearAttachedFiles();
    setMessages((prev) => [
      ...prev,
      {
        id: msgState.userMsgId,
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
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        threadId = newThread.id;
        msgState.isNewThread = true;
        // DO NOT call selectThread or setThreads here! We want to keep the UI perfectly
        // stable without triggering route transitions or sidebar layout shifts during
        // stream start (a real Next.js navigation would remount the page and wipe the
        // in-flight, not-yet-persisted assistant message — see promoteThreadUrl's
        // comment). But the URL itself must be promoted NOW, not deferred until the
        // stream completes: the thread already durably exists server-side at this
        // point, and a run can now suspend indefinitely waiting on a human (ask_human)
        // with no "run.completed"/"turn.completed" event firing until answered — if the
        // user refreshes while suspended, a stale "/chat" URL with no thread id loses
        // the conversation (and its pending HITL card) entirely. promoteThreadUrl uses
        // history.replaceState, which (per its own comment) updates the URL bar with no
        // remount, so doing it immediately is exactly as safe as doing it later.
        promoteThreadUrl(threadId);
        msgState.isNewThread = false;
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
        isSubmittingRef.current = false;
        setLoading(false);
        return;
      }
    }

    // Update thread name on the first message
    if (messages.length === 0) {
      const name = currentInput.slice(0, 50) + (currentInput.length > 50 ? "..." : "");
      handleRenameThread(threadId, name);
    }

    await runEventStream(threadId, msgState, (signal) =>
      api.streamChat(
        {
          thread_id: threadId!,
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
        signal,
      )
    );
  }

  // Runs one SSE stream to completion, feeding every event into the shared
  // message/card/board state. Both the initial send (doSendMessage) and a
  // HITL-answer reconnect (respondToHITL, only when no live stream is
  // already open) call this — so a resumed run's output is handled by
  // IDENTICAL logic regardless of which path triggered it, instead of a
  // second, divergent implementation that could drift out of sync.
  async function runEventStream(
    threadId: string,
    msgState: {
      activeAssistantId: string;
      needsNewBubble: boolean;
      userMsgId: string;
      isNewThread: boolean;
      pendingSources: CitationSource[];
    },
    streamFactory: (signal: AbortSignal) => Promise<Response>,
  ) {
    activeStreamThreadIdRef.current = threadId;

    // Call before any handler that writes streaming content. If a tool step
    // just finished (needsNewBubble=true), inserts a fresh bubble at the
    // tail of the message list and updates activeAssistantId to point at it.
    // Seeds `sources` from whatever citations have accumulated so far this
    // turn (possibly several knowledge_search calls) — mirrors
    // history-fold.ts's ensureActive() so a page reload reconstructs the
    // same bubble/sources split the live stream produced.
    function ensureActiveBubble() {
      if (!msgState.needsNewBubble) return;
      const newId = nanoid();
      msgState.activeAssistantId = newId;
      msgState.needsNewBubble = false;
      setMessages((m) => [
        ...m,
        {
          id: newId,
          role: "assistant" as const,
          content: "",
          reasoning: "",
          timestamp: new Date(),
          isContinuation: true,
          sources: msgState.pendingSources.length ? [...msgState.pendingSources] : undefined,
        },
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
            const hasVisibleToolCalls = Boolean(message.toolCalls?.length);
            return hasContent || hasVisibleToolCalls;
          })
      );
    }

    // ── Start SSE stream from backend ────────────────────────────────────
    const abortController = new AbortController();
    wsRef.current = abortController;
    isSubmittingRef.current = true;
    setLoading(true);

    // processEvent handles every server-sent event type.
    const processEvent = (data: Record<string, unknown>) => {
      // Guard: if a newer stream has taken ownership (user sent a new message
      // while this one was still draining), discard all further events from
      // this stream so they don't corrupt the new stream's message state.
      if (wsRef.current !== abortController) return;

      // ── Keepalive / end marker ────────────────────────────────────────
      if (data.type === "ping" || data.type === "protocol.hello" || data.type === "done") return;

      // ── HITL: Tool Approval Request ─────────────────────────────────
      if (data.type === "approval.requested") {
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
              arguments: data.args,
              context: data.context,
              risk: data.risk,
              summary: data.summary,
            },
          },
        ]);
        setHitlPending(true);
        // Whatever text streams in once this resumes (possibly the agent's
        // very first output, if this was its first action) must land in a
        // NEW bubble appended after this card — not back-fill the initial
        // placeholder bubble created at submit time, which sits earlier in
        // the array and would otherwise render the final answer ABOVE this
        // card even though it happened chronologically after.
        msgState.needsNewBubble = true;
        return;
      }

      // ── HITL: Human Input Request ───────────────────────────────────
      if (data.type === "input.requested") {
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
        setHitlPending(true);
        // See approval.requested above: force the next text into a fresh
        // trailing bubble instead of the (possibly still-empty, and
        // array-early) original placeholder.
        msgState.needsNewBubble = true;
        return;
      }

      // ── Tool result ─────────────────────────────────────────────────
      if (data.type === "tool.result") {
        // Task management results update the boards map, not the chat
        if (data.tool_name === "manage_tasks") {
          const sc = data.structured_content as Record<string, unknown> | undefined;
          const tl = sc?.task_list as import("@/types").TaskList | undefined;
          if (tl) {
            upsertBoard(tl);
            // Anchor this board to the current turn's user message the first
            // time we see it, so the inline plan card renders beneath it.
            setBoardAnchors((prev) => {
              if (prev.has(tl.agent_id)) return prev;
              const next = new Map(prev);
              next.set(tl.agent_id, msgState.userMsgId);
              return next;
            });
          }
          return;
        }

        // Grounded citations from knowledge_search (or any future tool that
        // sets structured_content.citations) — accumulate across every call
        // this turn and apply to whichever bubble is currently active, same
        // carry-forward rule as history-fold.ts's replay path so a reload
        // reconstructs an identical result. Does NOT return early: the
        // ordinary result/isError attachment below still needs to run.
        const newCitations = parseCitations(data.structured_content);
        if (newCitations.length) {
          msgState.pendingSources = mergeSources(msgState.pendingSources, newCitations);
          const sources = msgState.pendingSources;
          setMessages((m) =>
            m.map((msg) =>
              msg.id === msgState.activeAssistantId ? { ...msg, sources } : msg
            )
          );
        }

        const resultText = (data.ok ? data.output : data.error) as string || "";
        const isError = !data.ok;

        // Match by call_id whenever the event carries one (it always should
        // — the backend logs call_id=effect_id for every tool.result). Only
        // fall back to matching by tool NAME when call_id is genuinely
        // absent, and even then only against a call that doesn't already
        // have a result — a bare name match isn't a unique key: a turn that
        // calls the same tool more than once (e.g. knowledge_search asked
        // several differently-worded questions) has multiple toolCalls
        // sharing one name, possibly split across multiple bubbles, and an
        // unconditional name-only OR broadcasts one result's attachments
        // into every bubble that happens to contain a same-named call —
        // observed as "N charts generated" duplicated identically across
        // several bubbles instead of appearing once in the right one.
        const matchesCall = (tc: import("@/types").ToolCall) =>
          data.call_id ? tc.id === data.call_id : tc.name === data.tool_name && !tc.result;

        // For MCP App tools with app_data, merge the data into
        // the tool_call arguments so the iframe receives it
        if (data.has_app && data.app_data) {
          const panelId = (data.call_id as string) || nanoid();
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
                if (!matchesCall(tc)) return tc;
                const existingArgs =
                  typeof tc.arguments === "string" ? JSON.parse(tc.arguments) : tc.arguments;
                return {
                  ...tc,
                  arguments: { ...existingArgs, ...(data.app_data as object) },
                  result: resultText || tc.result,
                };
              });
              return { ...msg, toolCalls: updatedCalls };
            })
          );
          return;
        }

        // Skip rendering if an MCP App UI is already showing this tool's output
        if (data.has_app && !isError) return;

        // Media the tool produced (e.g. code_interpreter charts) — tagged
        // origin:"tool" so MessageBubble renders them collapsed (exploratory
        // re-runs shouldn't flood the chat). The model surfaces the ones
        // worth showing full-size via `sandbox:` markdown refs instead.
        const toolAttachments = getMessageAttachments({
          attachments: data.attachments,
        })?.map((a) => ({ ...a, origin: "tool" as const }));

        // Attach result to whichever assistant message owns this tool call.
        // Search all assistant messages (not just the current one) so results
        // from earlier steps still land in the correct bubble. matchesCall
        // (defined above) is call_id-authoritative, so this can't leak one
        // call's result/attachments into an unrelated bubble that happens to
        // share the same tool name.
        setMessages((m) =>
          m.map((msg) => {
            if (msg.role !== "assistant" || !msg.toolCalls) return msg;
            const hasMatch = msg.toolCalls.some(matchesCall);
            if (!hasMatch) return msg;
            const updatedCalls = msg.toolCalls.map((tc) => {
              if (!matchesCall(tc)) return tc;
              return { ...tc, result: resultText, isError };
            });
            return {
              ...msg,
              toolCalls: updatedCalls,
              attachments: toolAttachments
                ? [...(msg.attachments ?? []), ...toolAttachments]
                : msg.attachments,
            };
          })
        );
        return;
      }

      // ── Interactive UI (MCP Apps) — the narrow waist ────────────────
      // ANY rich tool UI (kanban, chart, form, map, …) arrives as one event.
      // Open/update a sandboxed iframe for `uri`, keyed by the resource so
      // repeat events coalesce onto the same panel and stream fresh data in.
      if (data.type === "ui.resource") {
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
      if (data.type === "text.delta") {
        if (hitlPending) setHitlPending(false);
        ensureActiveBubble();
        const textContent = String(data.text || "");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, content: msg.content + textContent, isToolExecuting: false }
              : msg
          )
        );
        return;
      }

      if (data.type === "reasoning.delta") {
        ensureActiveBubble();
        const reasoningContent = String(data.text || "");
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, reasoning: (msg.reasoning || "") + reasoningContent }
              : msg
          )
        );
        return;
      }

      if (data.type === "turn.completed") {
        // Handle LLM-level errors (e.g. provider returned no completion)
        if (data.finish_reason === "error") {
          const errorDetail = typeof data.text === "string" ? data.text : "";
          const errorMsg = errorDetail || "The AI model failed to generate a response. Please try again.";
          finalizeAssistantMessages((message) => ({
            ...message,
            content: (message.content ? message.content + "\n\n" : "") + "⚠️ " + errorMsg,
          }));
          setLoading(false);
          return;
        }

        const finalContent = String(data.text || "");
        const toolCallList = (data.tool_calls as unknown[]) ?? [];
        const hasToolCalls = toolCallList.length > 0;

        const toolCalls: import("@/types").ToolCall[] = toolCallList
          .filter((tc) => {
            const n = (tc as { name: string }).name;
            return n !== "manage_tasks" && n !== "ask_human";
          })
          .map((tc) => {
            const t = tc as { id: string; name: string; args: unknown; _meta?: import("@/types").ToolCallMeta; risk?: "safe" | "sensitive" | "critical"; color?: "green" | "yellow" | "red" };
            return {
              id: t.id || nanoid(),
              name: t.name,
              arguments: (t.args ?? {}) as string | Record<string, unknown>,
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
                role: "assistant" as Message["role"],
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
          if (msgState.isNewThread && !currentThreadId) {
            promoteThreadUrl(threadId);
            msgState.isNewThread = false;
          }
        }
        return;
      }

      if (data.type === "tool.call") {
        if (data.tool_name === "manage_tasks" || data.tool_name === "ask_human") return;
        ensureActiveBubble();
        const toolCall = {
          id: (data.call_id as string) || nanoid(),
          name: (data.tool_name as string) || "tool",
          arguments: JSON.stringify(data.args || {}),
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
      if (data.type === "run.completed") {
        if (data.reason === "max_iterations") {
          finalizeAssistantMessages();
          setLoading(false);
          void loadThreads();
          if (msgState.isNewThread && !currentThreadId) {
            promoteThreadUrl(threadId);
            msgState.isNewThread = false;
          }
          const cardId = nanoid();
          setMessages((m) => [
            ...m,
            { id: cardId, role: "max_iterations" as const, content: "", timestamp: new Date() },
          ]);
        } else {
          // Clean completion: stop any still-spinning plan tasks immediately.
          settleBoards();
          // Safety net: if no turn.completed fired (e.g. tool-only runs), stop loading.
          finalizeAssistantMessages();
          setLoading(false);
          void loadThreads();
          if (msgState.isNewThread && !currentThreadId) {
            promoteThreadUrl(threadId);
            msgState.isNewThread = false;
          }
          // Auto-open the main file artifact (Claude-style), once per message.
          const runThreadId = threadId;
          setMessages((m) => {
            for (let i = m.length - 1; i >= 0; i--) {
              if (m[i].role !== "assistant") continue;
              const msg = m[i];
              if (!autoOpenedArtifactRef.current.has(msg.id)) {
                const ref = firstArtifactRef(msg.content || "");
                if (ref) {
                  autoOpenedArtifactRef.current.add(msg.id);
                  setTimeout(() => openArtifact(ref, undefined, runThreadId), 0);
                }
              }
              break;
            }
            return m;
          });
          // Reconcile already-open file panels: the agent may have rewritten a
          // file that's open in the editor. Bump the cache-bust so the viewer
          // remounts (AppPanel keys on `id:fileUrl`) and re-fetches — the
          // BetterOffice editor then reloads the new bytes/checksum.
          setPanelItems((items) =>
            items.map((it) =>
              it.kind === "file" && it.fileUrl?.includes(`thread_id=${runThreadId}`)
                ? { ...it, fileUrl: it.fileUrl.replace(/&v=\d+/, `&v=${Date.now()}`) }
                : it,
            ),
          );
        }
        return;
      }

      if (data.type === "run.failed") {
        const errorMsg = String(data.error || "The agent encountered an error.");
        finalizeAssistantMessages((message) => ({
          ...message,
          content: message.content + "\n\n⚠️ " + errorMsg,
        }));
        setLoading(false);
        if (msgState.isNewThread && !currentThreadId) {
          promoteThreadUrl(threadId);
          msgState.isNewThread = false;
        }
        return;
      }

      if (data.type === "agent.handoff") {
        ensureActiveBubble();
        const handoffCall = {
          id: nanoid(),
          name: `→ ${data.target_agent as string || "agent"}`,
          arguments: data.reason ? JSON.stringify({ reason: data.reason }) : "{}",
        };
        setMessages((m) =>
          m.map((msg) =>
            msg.id === msgState.activeAssistantId
              ? { ...msg, toolCalls: [...(msg.toolCalls || []), handoffCall], isToolExecuting: true }
              : msg
          )
        );
        return;
      }

      if (data.type === "run.cancelled") {
        finalizeAssistantMessages();
        setLoading(false);
        return;
      }

      if (data.type === "error") {
        const errorMsg = String(data.message || data.error || "Unknown error");
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
      const response = await streamFactory(abortController.signal);

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
          processEvent(parsed as Record<string, unknown>);
        }
      }
    } catch (err: unknown) {
      const name = (err as { name?: string }).name;
      if (name === "AbortError") {
        // intentional cancel — no UI update needed
      } else if (err instanceof ChatConflictError) {
        // 409: a stream is already running for this thread — silently drop
        // the duplicate request; the active stream will complete normally.
        setMessages((m) =>
          m.filter((msg) => msg.id !== msgState.activeAssistantId)
        );
      } else {
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
        isSubmittingRef.current = false;
        // Record which thread just finished streaming so loadMessages won't
        // overwrite the in-memory messages with a potentially stale DB snapshot.
        streamedThreadRef.current = threadId;
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
          <div className="substrate-fade-up w-14 h-14 mx-auto rounded-2xl flex items-center justify-center text-xl font-bold bg-foreground text-background">
            R
          </div>
          <div className="substrate-fade-up" style={{ '--stagger': 1 } as React.CSSProperties}>
            <h1 className="text-2xl font-semibold">Welcome</h1>
            <p className="text-sm mt-2 text-(--muted)">
              Sign in to start chatting with your AI assistant
            </p>
          </div>
          {authNotice && (
            <div className="substrate-fade-up rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200" style={{ '--stagger': 2 } as React.CSSProperties}>
              {authNotice}
            </div>
          )}
          <button
            onClick={loginWithGoogle}
            className="substrate-fade-up substrate-press flex items-center gap-3 mx-auto px-6 py-3 bg-white text-gray-800 rounded-2xl text-sm font-semibold hover:bg-gray-50 transition-colors cursor-pointer"
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
          <p className="substrate-fade-up text-xs text-(--muted)" style={{ '--stagger': 4 } as React.CSSProperties}>
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
          onOpenScheduled={handleOpenScheduled}
          isScheduledOpen={scheduledPanelOpen}
          scheduledCount={scheduledCount}
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
            {/* Collapsed desktop rail — keeps expand + new chat reachable
                without opening the sidebar (Claude-style). */}
            {!desktopSidebarOpen && (
              <div className="pointer-events-auto hidden flex-col gap-2 rounded-xl border border-(--border) bg-(--card)/95 p-1 shadow-sm backdrop-blur-sm lg:flex">
                <button
                  type="button"
                  onClick={() => setDesktopSidebarOpen(true)}
                  className="btn-icon flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-(--muted) transition-colors hover:bg-background hover:text-foreground"
                  aria-label="Open sidebar"
                  title="Open sidebar"
                >
                  <SidebarToggleIcon direction="open" className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={handleNewChat}
                  className="btn-icon flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-(--muted) transition-colors hover:bg-background hover:text-foreground"
                  aria-label="New chat"
                  title="New chat"
                >
                  <SquarePen className="h-4 w-4" />
                </button>
              </div>
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
          ) : scheduledPanelOpen ? (
            <ScheduledPanel onBack={handleCloseScheduled} />
          ) : (
            <>
              <div
                ref={containerRef}
                onScroll={(e) => {
                  const el = e.currentTarget;
                  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
                  // 150px slack so a small streaming-driven scrollHeight change
                  // doesn't flip this back to true while the user is reading.
                  isNearBottomRef.current = distanceFromBottom < 150;
                }}
                className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent"
              >
                {messages.length === 0 ? (
                  <div className="flex h-full items-center justify-center">
                    <div className="w-full max-w-2xl px-4 text-center sm:px-6">
                      <div className="space-y-5">
                        <SubstrateMark className="substrate-fade-up mx-auto h-10 w-10 text-foreground sm:h-12 sm:w-12" />
                        <div className="substrate-fade-up" style={{ '--stagger': 1 } as React.CSSProperties}>
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
                              className="substrate-pop-in substrate-press flex cursor-pointer items-center gap-3 rounded-2xl p-3 text-left text-sm text-(--muted) transition-colors hover:bg-(--card-hover) sm:p-3.5"
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
                                    className="substrate-press flex items-center gap-1.5 rounded-xl border border-(--border) bg-(--card) px-3 py-1.5 text-xs text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground cursor-pointer"
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
                                risk={m.metadata.risk as string | undefined}
                                summary={m.metadata.summary as string | undefined}
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
                                initialStatus={
                                  m.metadata.initialStatus as "answered" | "skipped" | undefined
                                }
                                initialAnswerLabel={
                                  m.metadata.initialAnswerLabel as string | undefined
                                }
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
                        const anchoredBoards = boardsByAnchor.get(m.id);
                        return (
                          <React.Fragment key={m.id}>
                            <MessageBubble
                              role={m.role}
                              content={m.content}
                              attachments={m.attachments}
                              reasoning={m.reasoning}
                              timestamp={m.timestamp}
                              toolCalls={m.toolCalls}
                              isToolExecuting={m.isToolExecuting}
                              isContinuation={m.isContinuation}
                              threadId={currentThreadId}
                              sources={m.sources}
                              onOpenArtifact={openArtifact}
                              onOpenSource={openSource}
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
                            {anchoredBoards && anchoredBoards.length > 0 && (
                              <div className="px-4 sm:px-6">
                                <div className="mx-auto max-w-(--chat-width)">
                                  <PlanCardStack
                                    boards={anchoredBoards}
                                    runActive={loading && !hitlPending}
                                    onChange={upsertBoard}
                                  />
                                </div>
                              </div>
                            )}
                          </React.Fragment>
                        );
                      }

                      return null;
                    })}

                    {loading && !hitlPending && !messages.some((m) => m.role === "assistant" && m.id === messages[messages.length - 1]?.id) && (
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
                      onPaste={(e) => {
                        const text = e.clipboardData.getData("text/plain");
                        if (text.length < PASTE_TO_DOCUMENT_THRESHOLD) return;
                        // Long paste becomes a document attachment instead
                        // of filling the composer — don't let the default
                        // paste insert the raw text too.
                        e.preventDefault();
                        void handleFilesPasted([buildPastedDocumentFile(text)]);
                      }}
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

                        {input.trim() || (loading && !hitlPending) ? (
                          loading && !hitlPending ? (
                            <button
                              type="button"
                              onClick={handleStop}
                              className="btn-icon substrate-press flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-foreground text-background transition-colors"
                              aria-label="Stop"
                            >
                              <StopCircle className="h-4 w-4" />
                            </button>
                          ) : (
                            <button
                              type="submit"
                              disabled={!input.trim() || sendQueued}
                              className={`btn-icon substrate-press flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-foreground text-background transition-all disabled:cursor-not-allowed ${sendQueued ? "disabled:opacity-60" : "disabled:opacity-10"}`}
                              aria-label={sendQueued ? "Waiting for attachments to finish processing" : "Send"}
                            >
                              {sendQueued ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <ArrowUp className="h-5 w-5" />
                              )}
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
            onReloadFile={(id) =>
              setPanelItems((items) =>
                items.map((it) =>
                  it.id === id && it.fileUrl
                    ? {
                      ...it,
                      fileUrl: it.fileUrl.includes("&v=")
                        ? it.fileUrl.replace(/&v=\d+/, `&v=${Date.now()}`)
                        : `${it.fileUrl}&v=${Date.now()}`,
                    }
                    : it,
                ),
              )
            }
          />
        )}

      </div>

      {/* Mobile Sidebar Drawer */}
      {mobileSidebarOpen && (
        <div className="substrate-fade-in fixed inset-0 z-40 lg:hidden">
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
            className="substrate-slide-in-left relative flex h-full flex-col"
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
              onOpenScheduled={handleOpenScheduled}
              isScheduledOpen={scheduledPanelOpen}
              scheduledCount={scheduledCount}
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
