import { Thread, BackendMessage, Message } from "@/types";

// API service layer for backend communication
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = {
  // Thread management
  async getThreads(): Promise<Thread[]> {
    const res = await fetch(`${API_BASE}/threads`);
    if (!res.ok) throw new Error("Failed to fetch threads");
    return res.json();
  },

  async createThread(name?: string): Promise<Thread> {
    const res = await fetch(`${API_BASE}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name || "New Chat" }),
    });
    if (!res.ok) throw new Error("Failed to create thread");
    return res.json();
  },

  async deleteThread(threadId: string): Promise<void> {
    const res = await fetch(`${API_BASE}/threads/${threadId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Failed to delete thread");
  },

  async updateThread(threadId: string, name: string): Promise<Thread> {
    const res = await fetch(`${API_BASE}/threads/${threadId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error("Failed to update thread");
    return res.json();
  },

  // Message management
  async getMessages(threadId: string): Promise<Message[]> {
    const res = await fetch(`${API_BASE}/threads/${threadId}/messages`);
    if (!res.ok) throw new Error("Failed to fetch messages");
    const backendMessages: BackendMessage[] = await res.json();
    
    // Convert backend message format to frontend format
    return backendMessages.map((msg) => ({
      id: msg.id,
      role: msg.type === "user_message" ? "user" : "assistant",
      content: msg.type === "user_message" 
        ? (msg.input || "") 
        : (Array.isArray(msg.output) ? msg.output.join("") : (msg.output || "")),
      timestamp: new Date(msg.created_at),
    })) as Message[];
  },

  // Chat streaming (handled separately in chat route)
};
