"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  X,
  Settings,
  User,
  Puzzle,
  ShieldCheck,
  ChevronRight,
  Loader2,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

// ── Types ──────────────────────────────────────────────────────────────────

export type SettingsTab = "general" | "profile" | "apps" | "admin";

interface AdminThread {
  id: string;
  name: string;
  user_identifier: string | null;
  created_at: string | null;
  updated_at: string | null;
  step_count: number;
}

interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  isAdmin: boolean;
  createdAt: string;
}

interface AdminStats {
  total_threads: number;
  total_steps: number;
}

interface AdminStep {
  id: string;
  type: string;
  name: string;
  input: string | null;
  output: string | null;
  is_error: boolean | null;
  created_at: string | null;
}

interface SettingsPanelProps {
  isOpen: boolean;
  initialTab?: SettingsTab;
  onClose: () => void;
}

// ── Component ──────────────────────────────────────────────────────────────

export function SettingsPanel({
  isOpen,
  initialTab = "general",
  onClose,
}: SettingsPanelProps) {
  const {
    user,
    isAuthenticated,
    isAdmin,
    googleAuth,
    spotifyAuth,
    loginWithGoogle,
    loginWithSpotify,
  } = useAuth();

  // ── General tab state ──
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const [customInstructions, setCustomInstructions] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // ── Admin tab state ──
  const [adminThreads, setAdminThreads] = useState<AdminThread[]>([]);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminStats, setAdminStats] = useState<AdminStats | null>(null);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminTab, setAdminTab] = useState<"users" | "threads">("users");
  const [expandedThreadId, setExpandedThreadId] = useState<string | null>(null);
  const [threadSteps, setThreadSteps] = useState<Record<string, AdminStep[]>>({});
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);

  // Load custom instructions from localStorage on mount
  useEffect(() => {
    setCustomInstructions(
      localStorage.getItem("system_instructions_override") ?? ""
    );
  }, []);

  // Sync tab when initialTab changes (opened via AccountMenu shortcut)
  useEffect(() => {
    if (isOpen) setActiveTab(initialTab);
  }, [isOpen, initialTab]);

  // Load admin data when switching to admin tab
  useEffect(() => {
    if (activeTab === "admin" && isAdmin && !adminLoading && adminThreads.length === 0) {
      loadAdminData();
    }
  }, [activeTab, isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadAdminData = useCallback(async () => {
    setAdminLoading(true);
    try {
      const [threadsRes, usersRes, statsRes] = await Promise.all([
        fetch("/api/admin/threads"),
        fetch("/api/admin/users"),
        fetch("/api/admin/stats"),
      ]);
      if (threadsRes.ok) setAdminThreads(await threadsRes.json());
      if (usersRes.ok) setAdminUsers(await usersRes.json());
      if (statsRes.ok) setAdminStats(await statsRes.json());
    } catch (err) {
      console.error("Failed to load admin data:", err);
    } finally {
      setAdminLoading(false);
    }
  }, []);

  const handleSaveInstructions = async () => {
    setSaveError(null);
    setSaveSuccess(false);

    if (!customInstructions.trim()) {
      localStorage.removeItem("system_instructions_override");
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
      return;
    }

    setIsSaving(true);
    try {
      const res = await fetch("/api/settings/check-instructions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instructions: customInstructions }),
      });
      const data = await res.json();

      if (!data.allowed) {
        setSaveError(data.reason ?? "Prompt not saved: content policy violation.");
        return;
      }

      localStorage.setItem("system_instructions_override", customInstructions.trim());
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    } catch {
      setSaveError("Failed to validate instructions. Please try again.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleExpandThread = async (threadId: string) => {
    if (expandedThreadId === threadId) {
      setExpandedThreadId(null);
      return;
    }
    setExpandedThreadId(threadId);
    if (!threadSteps[threadId]) {
      try {
        const res = await fetch(`/api/admin/threads/${threadId}/steps`);
        if (res.ok) {
          const steps: AdminStep[] = await res.json();
          setThreadSteps((prev) => ({ ...prev, [threadId]: steps }));
        }
      } catch (err) {
        console.error("Failed to load thread steps:", err);
      }
    }
  };

  const handleDeleteThread = async (threadId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this thread permanently? This cannot be undone.")) return;
    setDeletingThreadId(threadId);
    try {
      const res = await fetch(`/api/admin/threads/${threadId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setAdminThreads((prev) => prev.filter((t) => t.id !== threadId));
        if (expandedThreadId === threadId) setExpandedThreadId(null);
        if (adminStats) {
          setAdminStats((prev) =>
            prev ? { ...prev, total_threads: prev.total_threads - 1 } : prev
          );
        }
      }
    } catch (err) {
      console.error("Failed to delete thread:", err);
    } finally {
      setDeletingThreadId(null);
    }
  };

  if (!isOpen) return null;

  const tabs: { id: SettingsTab; label: string; icon: React.ElementType }[] = [
    { id: "general", label: "General", icon: Settings },
    { id: "profile", label: "Profile", icon: User },
    { id: "apps", label: "Apps", icon: Puzzle },
    ...(isAdmin
      ? [{ id: "admin" as SettingsTab, label: "Admin", icon: ShieldCheck }]
      : []),
  ];

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        className="relative flex w-full max-w-xl bg-background border-l border-(--border) flex-col h-full shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-(--border) shrink-0">
          <h2 className="text-base font-semibold">Settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-(--card-hover) rounded-lg transition-colors cursor-pointer"
            aria-label="Close settings"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar tabs */}
          <nav className="w-40 shrink-0 border-r border-(--border) py-2 overflow-y-auto">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`w-full flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors text-left cursor-pointer ${
                  activeTab === id
                    ? "bg-(--accent)/10 text-(--accent) font-medium"
                    : "hover:bg-(--card-hover) text-foreground"
                }`}
                aria-current={activeTab === id ? "page" : undefined}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </button>
            ))}
          </nav>

          {/* Content area */}
          <div className="flex-1 overflow-y-auto p-5">

            {/* ── General ──────────────────────────────────────────── */}
            {activeTab === "general" && (
              <div className="space-y-6">
                <div>
                  <h3 className="font-semibold mb-1">Custom Instructions</h3>
                  <p className="text-xs text-(--muted) mb-3 leading-relaxed">
                    Appended to the AI&apos;s base system prompt. Use this to personalise
                    tone, domain context, or response style without overriding core
                    behaviour.
                  </p>
                  <textarea
                    value={customInstructions}
                    onChange={(e) => {
                      setCustomInstructions(e.target.value);
                      setSaveError(null);
                    }}
                    rows={10}
                    className="w-full bg-(--input-bg) border border-(--border) rounded-xl p-3 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-(--accent) leading-relaxed"
                    placeholder={
                      "Examples:\n• Always respond in British English\n• I'm a senior software engineer — assume technical knowledge\n• Keep answers concise and cite sources when possible"
                    }
                  />

                  {saveError && (
                    <div className="mt-2 p-3 bg-red-950/40 border border-red-700 rounded-lg text-xs text-red-300">
                      ⛔ {saveError}
                    </div>
                  )}
                  {saveSuccess && (
                    <div className="mt-2 p-3 bg-green-950/40 border border-green-700 rounded-lg text-xs text-green-300">
                      ✓ Instructions saved successfully
                    </div>
                  )}

                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={handleSaveInstructions}
                      disabled={isSaving}
                      className="flex items-center gap-2 px-4 py-1.5 bg-(--accent) text-white rounded-lg text-sm disabled:opacity-50 hover:opacity-90 transition-opacity cursor-pointer"
                    >
                      {isSaving && <Loader2 className="w-3 h-3 animate-spin" />}
                      {isSaving ? "Checking…" : "Save Instructions"}
                    </button>
                    {customInstructions && (
                      <button
                        onClick={() => {
                          setCustomInstructions("");
                          localStorage.removeItem("system_instructions_override");
                          setSaveError(null);
                        }}
                        className="px-4 py-1.5 border border-(--border) hover:bg-(--card-hover) rounded-lg text-sm transition-colors cursor-pointer"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Profile ──────────────────────────────────────────── */}
            {activeTab === "profile" && (
              <div className="space-y-5">
                {isAuthenticated && user ? (
                  <>
                    <div className="flex items-start gap-4 p-4 bg-(--card) rounded-xl border border-(--border)">
                      {user.picture ? (
                        <img
                          src={user.picture}
                          alt={user.name ?? "User"}
                          className="w-14 h-14 rounded-full shrink-0 ring-2 ring-(--accent)/30"
                        />
                      ) : (
                        <div
                          className="w-14 h-14 rounded-full shrink-0 flex items-center justify-center text-xl font-bold text-white"
                          style={{ background: "var(--accent)" }}
                        >
                          {(user.name ?? user.email ?? "U")[0].toUpperCase()}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="font-semibold truncate">
                          {user.name ?? "User"}
                        </div>
                        <div className="text-sm text-(--muted) truncate mt-0.5">
                          {user.email ?? ""}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 mt-2">
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-(--accent)/10 text-(--accent)">
                            {user.provider === "google" ? "Google Account" : "Spotify"}
                          </span>
                          {isAdmin && (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-purple-900/40 text-purple-300">
                              ⚙ Admin
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    <button
                      onClick={() => setActiveTab("apps")}
                      className="w-full flex items-center justify-between px-4 py-3 bg-(--card) border border-(--border) rounded-xl text-sm hover:bg-(--card-hover) transition-colors cursor-pointer"
                    >
                      <span>Manage connected apps</span>
                      <ChevronRight className="w-4 h-4 text-(--muted)" />
                    </button>
                  </>
                ) : (
                  <div className="text-center py-10 space-y-4">
                    <div
                      className="w-16 h-16 mx-auto rounded-full flex items-center justify-center"
                      style={{ background: "var(--card)" }}
                    >
                      <User className="w-8 h-8 text-(--muted)" />
                    </div>
                    <div>
                      <p className="font-medium">Not signed in</p>
                      <p className="text-sm text-(--muted) mt-1">
                        Sign in to sync your preferences and unlock all features
                      </p>
                    </div>
                    <button
                      onClick={loginWithGoogle}
                      className="flex items-center gap-2.5 mx-auto px-5 py-2.5 bg-white text-gray-800 rounded-xl text-sm font-medium hover:bg-gray-100 transition-colors shadow-md cursor-pointer"
                    >
                      <GoogleIcon />
                      Continue with Google
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* ── Apps ─────────────────────────────────────────────── */}
            {activeTab === "apps" && (
              <div className="space-y-3">
                <p className="text-xs text-(--muted) mb-4 leading-relaxed">
                  Connect services to enable powerful agent integrations.
                </p>

                {/* Google */}
                <div className="flex items-center justify-between p-3.5 bg-(--card) rounded-xl border border-(--border)">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-white flex items-center justify-center shrink-0">
                      <GoogleIcon />
                    </div>
                    <div>
                      <div className="text-sm font-medium">Google</div>
                      <div className="text-xs text-(--muted)">
                        {googleAuth ? user?.email ?? "Connected" : "Not connected"}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        googleAuth ? "bg-green-400" : "bg-(--muted)"
                      }`}
                    />
                    {googleAuth ? (
                      <button
                        onClick={async () => {
                          await fetch("/api/auth/google/logout", {
                            method: "POST",
                            credentials: "include",
                          });
                          window.location.reload();
                        }}
                        className="text-xs text-red-400 hover:text-red-300 px-2.5 py-1 border border-red-700/30 rounded-lg transition-colors cursor-pointer"
                      >
                        Disconnect
                      </button>
                    ) : (
                      <button
                        onClick={loginWithGoogle}
                        className="text-xs px-2.5 py-1 bg-(--accent) text-white rounded-lg transition-colors hover:opacity-90 cursor-pointer"
                      >
                        Connect
                      </button>
                    )}
                  </div>
                </div>

                {/* Spotify */}
                <div className="flex items-center justify-between p-3.5 bg-(--card) rounded-xl border border-(--border)">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-[#1DB954] flex items-center justify-center shrink-0">
                      <SpotifyIcon />
                    </div>
                    <div>
                      <div className="text-sm font-medium">Spotify</div>
                      <div className="text-xs text-(--muted)">
                        {spotifyAuth ? "Connected" : "Not connected"}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        spotifyAuth ? "bg-green-400" : "bg-(--muted)"
                      }`}
                    />
                    {spotifyAuth ? (
                      <button
                        onClick={async () => {
                          await fetch("/api/spotify/logout", {
                            method: "POST",
                            credentials: "include",
                          });
                          window.location.reload();
                        }}
                        className="text-xs text-red-400 hover:text-red-300 px-2.5 py-1 border border-red-700/30 rounded-lg transition-colors cursor-pointer"
                      >
                        Disconnect
                      </button>
                    ) : (
                      <button
                        onClick={loginWithSpotify}
                        className="text-xs px-2.5 py-1 bg-[#1DB954] text-white rounded-lg transition-colors hover:opacity-90 cursor-pointer"
                      >
                        Connect
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Admin ─────────────────────────────────────────────── */}
            {activeTab === "admin" && isAdmin && (
              <div className="space-y-4">
                {/* Stats */}
                {adminStats && (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="p-3 bg-(--card) rounded-xl border border-(--border) text-center">
                      <div className="text-2xl font-bold text-(--accent)">
                        {adminStats.total_threads}
                      </div>
                      <div className="text-xs text-(--muted) mt-0.5">Threads</div>
                    </div>
                    <div className="p-3 bg-(--card) rounded-xl border border-(--border) text-center">
                      <div className="text-2xl font-bold text-(--accent)">
                        {adminStats.total_steps}
                      </div>
                      <div className="text-xs text-(--muted) mt-0.5">Steps</div>
                    </div>
                  </div>
                )}

                {/* Sub-tab bar */}
                <div className="flex items-center gap-1 border-b border-(--border)">
                  {(["users", "threads"] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => setAdminTab(t)}
                      className={`px-3 py-2 text-sm capitalize border-b-2 transition-colors -mb-px cursor-pointer ${
                        adminTab === t
                          ? "border-(--accent) text-(--accent)"
                          : "border-transparent text-(--muted) hover:text-foreground"
                      }`}
                    >
                      {t} {t === "users" ? `(${adminUsers.length})` : `(${adminThreads.length})`}
                    </button>
                  ))}
                  <button
                    onClick={loadAdminData}
                    disabled={adminLoading}
                    className="ml-auto flex items-center gap-1 text-xs text-(--muted) hover:text-foreground px-2 py-1.5 rounded transition-colors disabled:opacity-50 cursor-pointer"
                    aria-label="Refresh admin data"
                  >
                    <RefreshCw className={`w-3 h-3 ${adminLoading ? "animate-spin" : ""}`} />
                    Refresh
                  </button>
                </div>

                {adminLoading ? (
                  <div className="flex items-center justify-center py-10">
                    <Loader2 className="w-5 h-5 animate-spin text-(--muted)" />
                  </div>
                ) : (
                  <>
                    {/* Users */}
                    {adminTab === "users" && (
                      <div className="space-y-2">
                        {adminUsers.length === 0 ? (
                          <p className="text-xs text-(--muted) text-center py-6">
                            No registered users yet
                          </p>
                        ) : (
                          adminUsers.map((u) => (
                            <div
                              key={u.id}
                              className="flex items-center gap-3 p-3 bg-(--card) rounded-xl border border-(--border)"
                            >
                              {u.avatarUrl ? (
                                <img
                                  src={u.avatarUrl}
                                  className="w-8 h-8 rounded-full shrink-0"
                                  alt=""
                                />
                              ) : (
                                <div
                                  className="w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-sm font-bold text-white"
                                  style={{ background: "var(--accent)" }}
                                >
                                  {(u.email || "U")[0].toUpperCase()}
                                </div>
                              )}
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium truncate">
                                  {u.name ?? u.email}
                                </div>
                                <div className="text-xs text-(--muted) truncate">
                                  {u.email}
                                </div>
                              </div>
                              {u.isAdmin && (
                                <span className="text-xs px-1.5 py-0.5 bg-purple-900/40 text-purple-300 rounded shrink-0">
                                  Admin
                                </span>
                              )}
                              <div className="text-xs text-(--muted) shrink-0">
                                {new Date(u.createdAt).toLocaleDateString()}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    )}

                    {/* Threads */}
                    {adminTab === "threads" && (
                      <div className="space-y-1.5">
                        {adminThreads.length === 0 ? (
                          <p className="text-xs text-(--muted) text-center py-6">
                            No threads found
                          </p>
                        ) : (
                          adminThreads.map((t) => (
                            <div
                              key={t.id}
                              className="border border-(--border) rounded-xl overflow-hidden"
                            >
                              {/* Thread row */}
                              <div className="flex items-center bg-(--card) hover:bg-(--card-hover) transition-colors">
                                <button
                                  onClick={() => handleExpandThread(t.id)}
                                  className="flex flex-1 items-center gap-2.5 p-3 text-left min-w-0 cursor-pointer"
                                >
                                  <ChevronRight
                                    className={`w-3.5 h-3.5 shrink-0 text-(--muted) transition-transform duration-150 ${
                                      expandedThreadId === t.id ? "rotate-90" : ""
                                    }`}
                                  />
                                  <div className="flex-1 min-w-0">
                                    <div className="text-sm font-medium truncate">
                                      {t.name}
                                    </div>
                                    <div className="text-xs text-(--muted) mt-0.5">
                                      {t.user_identifier ?? "Anonymous"} ·{" "}
                                      {t.step_count} steps ·{" "}
                                      {t.updated_at
                                        ? new Date(t.updated_at).toLocaleDateString()
                                        : "—"}
                                    </div>
                                  </div>
                                </button>

                                {/* Delete button */}
                                <button
                                  onClick={(e) => handleDeleteThread(t.id, e)}
                                  disabled={deletingThreadId === t.id}
                                  className="shrink-0 p-2 mr-1 text-(--muted) hover:text-red-400 transition-colors disabled:opacity-40 cursor-pointer"
                                  aria-label="Delete thread"
                                >
                                  {deletingThreadId === t.id ? (
                                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  ) : (
                                    <Trash2 className="w-3.5 h-3.5" />
                                  )}
                                </button>
                              </div>

                              {/* Expanded steps */}
                              {expandedThreadId === t.id && (
                                <div className="border-t border-(--border) bg-background p-3 space-y-1.5 max-h-72 overflow-y-auto">
                                  {threadSteps[t.id] ? (
                                    threadSteps[t.id].length === 0 ? (
                                      <p className="text-xs text-(--muted) text-center py-2">
                                        No steps
                                      </p>
                                    ) : (
                                      threadSteps[t.id].map((step) => (
                                        <div
                                          key={step.id}
                                          className="text-xs p-2.5 bg-(--card) rounded-lg border border-(--border)"
                                        >
                                          <span
                                            className={`font-semibold ${
                                              step.type === "user_message"
                                                ? "text-blue-400"
                                                : step.type === "assistant_message"
                                                ? "text-green-400"
                                                : step.type === "tool_result"
                                                ? "text-yellow-400"
                                                : "text-(--muted)"
                                            }`}
                                          >
                                            [{step.type}]
                                          </span>{" "}
                                          <span className="text-foreground break-words">
                                            {(step.input ?? step.output ?? "—").slice(0, 300)}
                                          </span>
                                        </div>
                                      ))
                                    )
                                  ) : (
                                    <div className="flex items-center gap-2 text-xs text-(--muted) py-2">
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                      Loading steps…
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Icon helpers ─────────────────────────────────────────────────────────────

function GoogleIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}

function SpotifyIcon() {
  return (
    <svg
      className="w-5 h-5 text-white"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
    </svg>
  );
}
