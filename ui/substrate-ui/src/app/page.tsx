"use client";

import { useState, useRef, useEffect } from "react";
import { nanoid } from "nanoid";
import { MessageBubble } from "@/components/MessageBubble";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { Thread, Message } from "@/types";
import { api } from "@/lib/api";
import { Send } from "lucide-react";

export default function ChatPage() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

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

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;

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
      content: input,
      timestamp: new Date(),
    };

    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    const currentInput = input;
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
                  // Final message received - update with complete content
                  const finalContent = Array.isArray(data.content) 
                    ? data.content.join("") 
                    : String(data.content || "");
                  
                  // Parse tool calls if present
                  const toolCalls = data.tool_calls?.map((tc: any) => ({
                    id: tc.id,
                    name: tc.name,
                    arguments: tc.arguments,
                    result: "Completed" // You can enhance this with actual results
                  })) || [];
                  
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId
                        ? { 
                            ...msg, 
                            content: finalContent, 
                            role: data.role,
                            toolCalls: toolCalls.length > 0 ? toolCalls : msg.toolCalls,
                            isToolExecuting: false
                          }
                        : msg
                    )
                  );
                  setLoading(false);
                  // Reload threads to update message count
                  loadThreads();
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

  const currentThread = threads.find((t) => t.id === currentThreadId);

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      {/* Sidebar */}
      <div
        className={`${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } fixed lg:static inset-y-0 left-0 z-20 transition-transform duration-300 ease-in-out lg:translate-x-0`}
      >
        <Sidebar
          threads={threads}
          currentThreadId={currentThreadId}
          onNewChat={handleNewChat}
          onSelectThread={handleSelectThread}
          onDeleteThread={handleDeleteThread}
          onRenameThread={handleRenameThread}
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        <Header
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          threadName={currentThread?.name}
        />

        {/* Messages Area */}
        <div
          ref={containerRef}
          className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent"
        >
          {messages.length === 0 ? (
            <div className="h-full flex items-center justify-center">
              <div className="text-center space-y-4 max-w-md px-4">
                <div className="w-16 h-16 mx-auto rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-2xl font-bold">
                  AI
                </div>
                <h2 className="text-2xl font-semibold">How can I help you today?</h2>
                <p className="text-zinc-400">
                  Ask me anything, and I'll do my best to assist you with thoughtful, detailed responses.
                </p>
              </div>
            </div>
          ) : (
            <div>
              {messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  reasoning={m.reasoning}
                  timestamp={m.timestamp}
                  toolCalls={m.toolCalls}
                  isToolExecuting={m.isToolExecuting}
                />
              ))}
              {loading && (
                <div className="px-4 py-6">
                  <div className="max-w-3xl mx-auto flex gap-4">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-sm font-semibold">
                      AI
                    </div>
                    <div className="flex-1 pt-2">
                      <div className="flex gap-1">
                        <div className="w-2 h-2 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                        <div className="w-2 h-2 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                        <div className="w-2 h-2 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input Area */}
        <div className="border-t border-zinc-800 bg-zinc-950">
          <div className="max-w-3xl mx-auto px-4 py-4">
            <form onSubmit={sendMessage} className="relative">
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
                className="w-full resize-none rounded-2xl bg-zinc-900 border border-zinc-800 px-4 py-3 pr-12 text-sm outline-none focus:border-zinc-700 focus:ring-1 focus:ring-zinc-700 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent"
                placeholder="Message AI assistant..."
                disabled={loading}
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="absolute right-2 bottom-2 p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                aria-label="Send message"
              >
                <Send className="w-4 h-4" />
              </button>
            </form>
            <p className="text-xs text-center text-zinc-500 mt-2">
              AI can make mistakes. Consider checking important information.
            </p>
          </div>
        </div>
      </div>

      {/* Overlay for mobile sidebar */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-10 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
    </div>
  );
}