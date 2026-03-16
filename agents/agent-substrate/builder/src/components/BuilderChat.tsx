/* ── BuilderChat — test runner with sessions + embedded MCP apps ─────── */

"use client";

import { nanoid } from "nanoid";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PipelineAppPanel, type AppItem } from "@/components/PipelineAppPanel";
import { api } from "@/lib/api";
import { usePipelineStore, type ChatMessage } from "@/store/pipeline-store";
import styles from "./BuilderChat.module.css";

async function consumeSSE(
  response: Response,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  signal: AbortSignal
) {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;

        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr || jsonStr === "[DONE]") continue;

        try {
          const parsed = JSON.parse(jsonStr) as Record<string, unknown>;
          onEvent((parsed.type as string) ?? "unknown", parsed);
        } catch {
          /* ignore malformed sse chunk */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

const ROLE_STYLE: Record<ChatMessage["role"], { bg: string; color: string; row: string; bubble: string }> = {
  user: {
    bg: "linear-gradient(135deg, #6366f1 0%, #818cf8 100%)",
    color: "#fff",
    row: styles.rowRight,
    bubble: styles.bubbleStandard,
  },
  assistant: {
    bg: "var(--bg-elevated)",
    color: "var(--text)",
    row: styles.rowLeft,
    bubble: styles.bubbleStandard,
  },
  system: {
    bg: "transparent",
    color: "var(--text-dim)",
    row: styles.rowLeft,
    bubble: styles.bubbleSystem,
  },
  tool: {
    bg: "transparent",
    color: "var(--text-dim)",
    row: styles.rowLeft,
    bubble: styles.bubbleSystem,
  },
};

const EMPTY_MESSAGES: ChatMessage[] = [];
const EMPTY_SESSIONS: ReturnType<typeof usePipelineStore.getState>["chatSessions"][string] = [];

function formatRelativeTime(value: number): string {
  const diff = Date.now() - value;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function Bubble({ message }: { message: ChatMessage }) {
  const style = ROLE_STYLE[message.role] ?? ROLE_STYLE.system;
  return (
    <div className={`${styles.row} ${style.row}`}>
      <div className={`${styles.bubble} ${style.bubble}`} style={{ background: style.bg, color: style.color }}>
        {message.content}
      </div>
    </div>
  );
}

interface BuilderChatProps {
  open: boolean;
  onClose: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  appItems: AppItem[];
  activeAppId: string | null;
  onSetActiveApp: (id: string) => void;
  onCloseApp: (id: string) => void;
  onOpenApp: (item: AppItem) => void;
  isAppPanelCollapsed: boolean;
  onToggleAppPanel: () => void;
}

export function BuilderChat({
  open,
  onClose,
  isFullscreen,
  onToggleFullscreen,
  appItems,
  activeAppId,
  onSetActiveApp,
  onCloseApp,
  onOpenApp,
  isAppPanelCollapsed,
  onToggleAppPanel,
}: BuilderChatProps) {
  const pipelineId = usePipelineStore((s) => s.pipelineId);
  const isRunning = usePipelineStore((s) => s.isRunning);
  const setIsRunning = usePipelineStore((s) => s.setIsRunning);
  const sessions = usePipelineStore((s) => (s.pipelineId ? s.chatSessions[s.pipelineId] ?? EMPTY_SESSIONS : EMPTY_SESSIONS));
  const activeSessionId = usePipelineStore((s) => (s.pipelineId ? s.activeChatSessionIds[s.pipelineId] ?? null : null));
  const createChatSession = usePipelineStore((s) => s.createChatSession);
  const setActiveChatSession = usePipelineStore((s) => s.setActiveChatSession);
  const addChatMessage = usePipelineStore((s) => s.addChatMessage);
  const clearChat = usePipelineStore((s) => s.clearChat);

  const messages = usePipelineStore((s) => {
    const pid = s.pipelineId;
    if (!pid) return EMPTY_MESSAGES;
    const sessionId = s.activeChatSessionIds[pid];
    if (!sessionId) return EMPTY_MESSAGES;
    return s.allChatMessages[pid]?.[sessionId] ?? EMPTY_MESSAGES;
  });

  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open && pipelineId && sessions.length === 0) {
      createChatSession();
    }
  }, [open, pipelineId, sessions.length, createChatSession]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, activeSessionId]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [input]);

  const currentSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? null,
    [sessions, activeSessionId]
  );

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !pipelineId || isRunning) return;

    const runSessionId = activeSessionId ?? createChatSession();

    setInput("");
    addChatMessage({ role: "user", content: text }, runSessionId);
    setIsRunning(true);

    const abortController = new AbortController();
    abortRef.current = abortController;

    try {
      const response = await api.runPipeline(pipelineId, text, runSessionId);

      if (!response.ok) {
        addChatMessage({ role: "system", content: `HTTP ${response.status} — ${response.statusText}` }, runSessionId);
        return;
      }

      let assistantText = "";

      await consumeSSE(
        response,
        (type, data) => {
          switch (type) {
            case "text_delta": {
              assistantText += (data.content as string) ?? "";
              const store = usePipelineStore.getState();
              const pid = store.pipelineId ?? "";
              const sid = runSessionId;
              if (!pid || !sid) break;

              const sessionMessages = store.allChatMessages[pid]?.[sid] ?? [];
              const lastMessage = sessionMessages[sessionMessages.length - 1];

              if (lastMessage?.role === "assistant") {
                lastMessage.content = assistantText;
                usePipelineStore.setState({
                  allChatMessages: {
                    ...store.allChatMessages,
                    [pid]: {
                      ...(store.allChatMessages[pid] ?? {}),
                      [sid]: [...sessionMessages],
                    },
                  },
                });
              } else {
                store.addChatMessage({ role: "assistant", content: assistantText }, runSessionId);
              }
              break;
            }

            case "tool_call":
              addChatMessage({
                role: "tool",
                content: `🔧 ${data.tool_name as string}(${JSON.stringify(data.input ?? data.arguments ?? {}).slice(0, 100)})`,
              }, runSessionId);
              break;

            case "tool_result": {
              if (data.has_app && data.app_data) {
                const appId = (data.tool_call_id as string) || nanoid();
                const httpUrl =
                  (data.http_url as string) ||
                  `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"}/ui/${data.tool_name as string}`;
                onOpenApp({
                  id: appId,
                  httpUrl,
                  toolName: data.tool_name as string,
                  toolArguments: data.app_data as Record<string, unknown>,
                  timestamp: Date.now(),
                });
              } else {
                addChatMessage({
                  role: "tool",
                  content: `✅ ${data.tool_name as string} → ${String(data.result ?? data.content ?? "").slice(0, 160)}`,
                }, runSessionId);
              }
              break;
            }

            case "tool_approval_request":
              addChatMessage({ role: "system", content: `⚠ Approval needed: ${data.tool_name as string}` }, runSessionId);
              break;

            case "human_input_request":
              addChatMessage({ role: "system", content: `💬 Agent asks: ${(data.question as string) ?? (data.prompt as string) ?? "Input required"}` }, runSessionId);
              break;

            case "router_decision":
              addChatMessage({ role: "system", content: `[Branch: ${(data.parsed ?? data.raw_text) as string}]` }, runSessionId);
              break;

            case "error":
              addChatMessage({ role: "system", content: `❌ ${(data.message as string) ?? "Unknown error"}` }, runSessionId);
              break;

            default:
              break;
          }
        },
        abortController.signal
      );
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        addChatMessage({ role: "system", content: `Error: ${(error as Error).message}` }, runSessionId);
      }
    } finally {
      setIsRunning(false);
      abortRef.current = null;
    }
  }, [
    input,
    pipelineId,
    isRunning,
    activeSessionId,
    createChatSession,
    addChatMessage,
    setIsRunning,
    onOpenApp,
  ]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setIsRunning(false);
  }, [setIsRunning]);

  const handleCreateSession = useCallback(() => {
    createChatSession();
    setInput("");
  }, [createChatSession]);

  if (!open) return null;

  return (
    <section className={`${styles.panel} ${appItems.length > 0 ? styles.panelExpanded : ""} ${isFullscreen ? styles.panelFullscreen : ""}`} aria-label="Test chat panel">
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={`${styles.statusDot} ${isRunning ? styles.statusDotRunning : ""}`} />
          <div>
            <div className={styles.title}>Test chat</div>
            <div className={styles.subtitle}>
              {currentSession ? `${currentSession.title} • ${formatRelativeTime(currentSession.updatedAt)}` : "Create a session to start testing"}
            </div>
          </div>
        </div>

        <div className={styles.headerMeta}>
          {pipelineId ? <span className={styles.badge}>{sessions.length} session{sessions.length === 1 ? "" : "s"}</span> : null}
          {appItems.length > 0 ? <span className={styles.badge}>{appItems.length} app{appItems.length === 1 ? "" : "s"} open</span> : null}
          <div className={styles.headerActions}>
            <button onClick={onToggleFullscreen} className={styles.ghostButton}>
              {isFullscreen ? "Exit fullscreen" : "Fullscreen"}
            </button>
            <button onClick={clearChat} className={styles.ghostButton}>Clear</button>
            <button onClick={onClose} className={styles.iconButton} aria-label="Close test chat">✕</button>
          </div>
        </div>
      </div>

      <div className={`${styles.body} ${appItems.length > 0 ? styles.bodyWithApps : ""}`}>
        <div className={styles.chatColumn}>
          <div className={styles.sessionsBar}>
            <div className={styles.sessionsScroller}>
              {sessions.map((session) => (
                <button
                  key={session.id}
                  onClick={() => setActiveChatSession(session.id)}
                  className={`${styles.sessionChip} ${session.id === activeSessionId ? styles.sessionChipActive : ""}`}
                >
                  <span className={styles.sessionLabel}>{session.title}</span>
                  <span className={styles.sessionTime}>{formatRelativeTime(session.updatedAt)}</span>
                </button>
              ))}
            </div>
            <button onClick={handleCreateSession} className={styles.newSessionButton}>+ New session</button>
          </div>

          <div ref={scrollRef} className={styles.messages}>
            {messages.length === 0 ? (
              <div className={styles.emptyState}>
                <div className={styles.emptyIcon}>✦</div>
                <div className={styles.emptyTitle}>Test the current workflow</div>
                <p className={styles.emptyText}>
                  {pipelineId
                    ? "Use multiple sessions to test different paths, inspect router decisions, and open MCP apps beside the conversation."
                    : "Save the workflow first to create sessions and run the pipeline."}
                </p>
              </div>
            ) : (
              messages.map((message, index) => <Bubble key={`${message.role}-${index}`} message={message} />)
            )}
          </div>

          <div className={styles.inputBar}>
            <div className={styles.inputWrap}>
              <div className={styles.inputHint}>
                {pipelineId ? `Session ID: ${(activeSessionId ?? "not-ready").slice(0, 18)}${activeSessionId ? "…" : ""}` : "Save the workflow to enable sessions"}
              </div>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder={pipelineId ? "Message the workflow…" : "Save the workflow to enable testing"}
                disabled={!pipelineId || isRunning}
                className={styles.input}
                rows={1}
              />
            </div>

            {isRunning ? (
              <button onClick={stop} className={styles.dangerButton}>■ Stop run</button>
            ) : (
              <button onClick={() => void send()} disabled={!input.trim() || !pipelineId} className={styles.sendButton}>
                Send
              </button>
            )}
          </div>
        </div>

        {appItems.length > 0 && (
          <div className={styles.appColumn}>
            <PipelineAppPanel
              items={appItems}
              activeItemId={activeAppId}
              onSetActive={onSetActiveApp}
              onClose={onCloseApp}
              isCollapsed={isAppPanelCollapsed}
              onToggleCollapse={onToggleAppPanel}
              variant="embedded"
            />
          </div>
        )}
      </div>
    </section>
  );
}
