"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { nanoid } from "nanoid";
import { MessageBubble } from "@/components/MessageBubble";
import { ToolApprovalCard } from "@/components/ToolApprovalCard";
import { HumanInputCard } from "@/components/HumanInputCard";
import { AppPanel, AppPanelItem } from "@/components/AppPanel";
import { KanbanPanel } from "@/components/KanbanPanel";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { Thread, Message, Task, TaskList, TaskStatus } from "@/types";
import { api } from "@/lib/api";
import { Send, Plus, Mic, Music2, Music, Clock, BarChart2, type LucideIcon } from "lucide-react";

export default function ChatPage() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // ── App Panel State ──────────────────────────────────────
  const [panelItems, setPanelItems] = useState<AppPanelItem[]>([]);
  const [activePanelId, setActivePanelId] = useState<string | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  // ── Task Board State ─────────────────────────────────────
  const [taskList, setTaskList] = useState<TaskList | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Load threads on mount
  useEffect(() => {
    loadThreads();
  }, []);

  // Load messages when thread changes
  useEffect(() => {
    if (currentThreadId) {
      loadMessages(currentThreadId);
    }
  }, [currentThreadId]);

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
      setMessages(fetchedMessages);
    } catch (error) {
      console.error("Failed to load messages:", error);
      setMessages([]);
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

  // HITL: respond to a tool approval or human input request
  async function respondToHITL(
    requestId: string,
    data: Record<string, unknown>
  ) {
    try {
      await fetch(`/api/chat/respond/${requestId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (err) {
      console.error("Failed to send HITL response:", err);
    }
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

  async function doSendMessage(text: string) {
    if (!text.trim() || loading) return;

    // If no thread exists, create one first
    let threadId = currentThreadId;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        setThreads([newThread]);
        setCurrentThreadId(newThread.id);
        threadId = newThread.id;
      } catch (error) {
        console.error("Failed to create thread:", error);
        return;
      }
    }

    const userMessage: Message = {
      id: nanoid(),
      role: "user",
      content: text,
      timestamp: new Date(),
    };

    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    const currentInput = text;
    setInput("");
    setLoading(true);

    // Update thread name if it's the first message
    if (messages.length === 0) {
      const name = currentInput.slice(0, 50) + (currentInput.length > 50 ? "..." : "");
      handleRenameThread(threadId, name);
    }

    // Create an assistant placeholder message immediately
    const assistantId = nanoid();
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      reasoning: "",
      timestamp: new Date(),
    };
    setMessages((m) => [...m, assistantMessage]);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          thread_id: threadId,
          messages: [{ role: "user", content: currentInput }]
        }),
      });

      if (!response.ok) {
        throw new Error("Request failed");
      }

      if (!response.body) {
        throw new Error("No stream");
      }

      // Read SSE stream manually
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by double newlines
        const parts = buffer.split(/\n\n/);
        // Keep the last (possibly partial) part in the buffer
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.trim()) continue;

          // Parse SSE format: "data: {...}"
          const lines = part.split(/\n/);
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const jsonStr = line.slice(6); // Remove "data: " prefix
              
              // Skip [DONE] message
              if (jsonStr === "[DONE]") continue;
              
              try {
                const data = JSON.parse(jsonStr);

                // ── HITL: Tool Approval Request ──
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
                  continue;
                }

                // ── HITL: Human Input Request ──
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
                  continue;
                }

                // ── Tool result ──
                if (data.type === "tool_result") {
                  // For MCP App tools with app_data, merge the data into
                  // the tool_call arguments so the iframe receives it
                  if (data.has_app && data.app_data) {
                    // Auto-open in side panel
                    const panelId = data.tool_call_id || nanoid();
                    const httpUrl = data.http_url || `/ui/${data.tool_name}`;
                    openInPanel({
                      id: panelId,
                      httpUrl,
                      toolName: data.tool_name,
                      toolArguments: data.app_data,
                      timestamp: Date.now(),
                    });

                    setMessages((m) =>
                      m.map((msg) => {
                        if (msg.role !== "assistant" || !msg.toolCalls) return msg;
                        const updatedCalls = msg.toolCalls.map((tc) => {
                          // Match by tool_call_id or tool name
                          const matchById = data.tool_call_id && tc.id === data.tool_call_id;
                          const matchByName = tc.name === data.tool_name;
                          if (!matchById && !matchByName) return tc;
                          const existingArgs =
                            typeof tc.arguments === "string"
                              ? JSON.parse(tc.arguments)
                              : tc.arguments;
                          return {
                            ...tc,
                            arguments: { ...existingArgs, ...data.app_data },
                            result: data.content || tc.result,
                          };
                        });
                        return { ...msg, toolCalls: updatedCalls };
                      })
                    );
                    continue;
                  }
                  // Skip rendering if an MCP App UI is already showing this tool's output
                  // (only skip if app_data was received; otherwise show text fallback)
                  if (data.has_app && !data.is_error) continue;

                  // Attach result back to the matching tool call in the assistant message
                  setMessages((m) =>
                    m.map((msg) => {
                      if (msg.id !== assistantId || !msg.toolCalls) return msg;
                      const updatedCalls = msg.toolCalls.map((tc) => {
                        const matchById = data.tool_call_id && tc.id === data.tool_call_id;
                        const matchByName = tc.name === data.tool_name;
                        if (!matchById && !matchByName) return tc;
                        return {
                          ...tc,
                          result: data.content || "",
                          isError: !!data.is_error,
                        };
                      });
                      return { ...msg, toolCalls: updatedCalls };
                    })
                  );
                  continue;
                }

                // ── Task Board Events ──
                if (data.type === "task_list_created") {
                  setTaskList(data.task_list as TaskList);
                  continue;
                }
                if (data.type === "task_updated") {
                  const updatedTask = data.task as Task;
                  setTaskList((prev) => {
                    if (!prev || prev.id !== data.task_list_id) return prev;
                    return {
                      ...prev,
                      tasks: prev.tasks.map((t) =>
                        t.id === updatedTask.id ? { ...t, ...updatedTask } : t
                      ),
                    };
                  });
                  continue;
                }
                if (data.type === "task_added") {
                  const newTask = data.task as Task;
                  setTaskList((prev) => {
                    if (!prev || prev.id !== data.task_list_id) return prev;
                    // Avoid duplicates
                    if (prev.tasks.find((t) => t.id === newTask.id)) return prev;
                    return { ...prev, tasks: [...prev.tasks, newTask] };
                  });
                  continue;
                }
                if (data.type === "task_deleted") {
                  setTaskList((prev) => {
                    if (!prev || prev.id !== data.task_list_id) return prev;
                    return {
                      ...prev,
                      tasks: prev.tasks.filter((t) => t.id !== data.task_id),
                    };
                  });
                  continue;
                }

                if (data.type === 'text_delta') {
                  // Append incremental text to the assistant message
                  const textContent = String(data.content || "");
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { ...msg, content: msg.content + textContent, isToolExecuting: false }
                        : msg
                    )
                  );
                } else if (data.type === 'reasoning_delta') {
                  // Append thinking/reasoning process
                  const reasoningContent = String(data.content || "");
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { ...msg, reasoning: (msg.reasoning || "") + reasoningContent }
                        : msg
                    )
                  );
                } else if (data.type === 'completion') {
                  // Completion event — may be intermediate (with tool_calls)
                  // or final (text response, no tool_calls).
                  const finalContent = Array.isArray(data.content) 
                    ? data.content.join("") 
                    : String(data.content || "");
                  const hasToolCalls = !!data.has_tool_calls;
                  
                  // Parse tool calls if present
                  const toolCalls = data.tool_calls?.map((tc: any) => ({
                    id: tc.id,
                    name: tc.name,
                    arguments: tc.arguments,
                    result: "Completed",
                    _meta: tc._meta || undefined,
                  })) || [];
                  
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { 
                            ...msg, 
                            // Only replace content if there IS content (avoid clearing on tool-call-only completions)
                            content: finalContent || msg.content, 
                            role: data.role,
                            toolCalls: toolCalls.length > 0 ? toolCalls : msg.toolCalls,
                            // Keep executing flag while tools are running
                            isToolExecuting: hasToolCalls
                          }
                        : msg
                    )
                  );

                  // Only stop loading on the final completion (no tool calls)
                  if (!hasToolCalls) {
                    setLoading(false);
                    loadThreads();
                  }
                } else if (data.type === 'tool_call') {
                  // Tool is being called - show it in UI
                  const toolCall = {
                    id: data.tool_call_id || nanoid(),
                    name: data.tool_name || "tool",
                    arguments: JSON.stringify(data.arguments || {}),
                  };
                  
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { 
                            ...msg, 
                            toolCalls: [...(msg.toolCalls || []), toolCall],
                            isToolExecuting: true
                          }
                        : msg
                    )
                  );
                } else if (data.type === 'error') {
                  const errorMsg = String(data.error || "Unknown error");
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { ...msg, content: msg.content + "\n\n⚠️ " + errorMsg, isToolExecuting: false }
                        : msg
                    )
                  );
                  setLoading(false);
                }
              } catch (err) {
                console.error("Failed to parse SSE message:", err);
              }
            }
          }
        }
      }

      setLoading(false);
    } catch (error) {
      console.error("Stream error:", error);
      setMessages((m) =>
        m.map((msg) =>
          msg.id === assistantId
            ? { ...msg, content: msg.content + "\n\n⚠️ Connection error." }
            : msg
        )
      );
      setLoading(false);
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

  return (
    <div className="flex h-screen bg-background text-foreground" suppressHydrationWarning>
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
                    { icon: Music, text: "Play some jazz music" },
                    { icon: Clock, text: "What's the current time?" },
                    { icon: BarChart2, text: "Show a data visualisation" },
                  ] as { icon: LucideIcon, text: string }[]).map(({ icon: Icon, text }, idx) => (
                    <button
                      key={idx}
                      onClick={() => doSendMessage(text)}
                      className="flex items-center gap-2.5 p-3 rounded-xl text-left text-sm transition-colors hover:bg-(--card-hover)"
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
                    <div key={m.id} className="py-1">
                      <ToolApprovalCard
                        requestId={m.metadata.requestId as string}
                        toolName={m.metadata.toolName as string}
                        arguments={m.metadata.arguments as Record<string, unknown>}
                        context={m.metadata.context as string | undefined}
                        onRespond={respondToHITL}
                      />
                    </div>
                  );
                }

                if (m.role === "human_input" && m.metadata) {
                  return (
                    <div key={m.id} className="py-1">
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
              {loading && (
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

        {/* Kanban Task Board — above input */}
        <KanbanPanel
          taskList={taskList}
          onTaskStatusChange={handleTaskStatusChange}
          onTaskDelete={handleTaskDelete}
          onTaskAdd={handleTaskAdd}
          onDismiss={() => setTaskList(null)}
        />

        {/* Input Area */}
        <div className="bg-background pb-4 pt-2">
          <div className="max-w-3xl mx-auto px-4">
            <form
              onSubmit={sendMessage}
              className="flex items-end gap-2 rounded-3xl border border-(--border) bg-(--input-bg) px-3 py-2 shadow-sm"
            >
              {/* Left: attach/plus */}
              <button
                type="button"
                className="shrink-0 mb-0.5 p-1.5 rounded-full hover:bg-(--card-hover) transition-colors self-end"
                style={{ color: "var(--muted)" }}
                aria-label="Attach"
              >
                <Plus className="w-4 h-4" />
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

              {/* Right: mic + send */}
              <div className="flex items-center gap-1 shrink-0 self-end mb-0.5">
                {!input.trim() && (
                  <button
                    type="button"
                    className="p-1.5 rounded-full hover:bg-(--card-hover) transition-colors"
                    style={{ color: "var(--muted)" }}
                    aria-label="Voice input"
                  >
                    <Mic className="w-4 h-4" />
                  </button>
                )}
                <button
                  type="submit"
                  disabled={loading || !input.trim()}
                  className="p-1.5 rounded-full transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  style={{
                    background: input.trim() ? "var(--accent)" : "var(--card)",
                    color: input.trim() ? "#fff" : "var(--muted)",
                  }}
                  aria-label="Send"
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </form>
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