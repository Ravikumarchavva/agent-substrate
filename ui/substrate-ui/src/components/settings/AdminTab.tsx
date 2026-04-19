"use client";

import React from "react";
import { ChevronRight, Loader2, RefreshCw, Trash2 } from "lucide-react";
import type { AdminStats, AdminStep, AdminThread, AdminUser } from "@/types";

interface AdminTabProps {
  adminStats: AdminStats | null;
  adminUsers: AdminUser[];
  adminThreads: AdminThread[];
  adminLoading: boolean;
  adminLoadNotice: string | null;
  adminUsersError: string | null;
  adminTab: "users" | "threads";
  setAdminTab: (tab: "users" | "threads") => void;
  expandedThreadId: string | null;
  threadSteps: Record<string, AdminStep[]>;
  deletingThreadId: string | null;
  handleExpandThread: (threadId: string) => void;
  handleDeleteThread: (threadId: string, event: React.MouseEvent) => void;
  loadAdminData: () => void;
}

const tabOptions = ["users", "threads"] as const;

function StepTypeBadge({ type }: { type: string }) {
  const color =
    type === "user_message"
      ? "text-blue-400"
      : type === "assistant_message"
        ? "text-green-400"
        : type === "tool_result"
          ? "text-yellow-400"
          : "text-(--muted)";
  return <span className={`font-semibold ${color}`}>[{type}]</span>;
}

export function AdminTab({
  adminStats,
  adminUsers,
  adminThreads,
  adminLoading,
  adminLoadNotice,
  adminUsersError,
  adminTab,
  setAdminTab,
  expandedThreadId,
  threadSteps,
  deletingThreadId,
  handleExpandThread,
  handleDeleteThread,
  loadAdminData,
}: AdminTabProps) {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">Admin</h2>
        <p className="max-w-3xl text-sm leading-6 text-(--muted)">
          Conversation and user operations in a cleaner, denser review surface.
        </p>
      </div>

      {adminLoadNotice && (
        <p className="rounded-[18px] bg-(--badge-bg) px-4 py-3 text-sm text-(--muted)">
          {adminLoadNotice}
        </p>
      )}

      {/* Stats cards */}
      {adminStats && (
        <div className="grid gap-4 sm:grid-cols-2">
          {[
            { label: "Threads", value: adminStats.total_threads },
            { label: "Steps", value: adminStats.total_steps },
          ].map((stat) => (
            <div key={stat.label} className="rounded-[24px] p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
              <p className="text-xs font-medium uppercase tracking-wider text-(--muted)">
                {stat.label}
              </p>
              <p className="mt-3 text-4xl font-semibold text-foreground">{stat.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Tab bar + refresh */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-2 rounded-2xl bg-(--panel-muted) p-1">
          {tabOptions.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setAdminTab(tab)}
              className={`cursor-pointer rounded-xl px-4 py-2 text-sm font-medium capitalize transition-colors ${
                adminTab === tab
                  ? "bg-foreground text-background"
                  : "border border-transparent text-(--muted) hover:text-foreground"
              }`}
              style={adminTab === tab ? { boxShadow: "var(--shadow-sm)" } : undefined}
            >
              {tab} ({tab === "users" ? adminUsers.length : adminThreads.length})
            </button>
          ))}
        </div>

        <button
          onClick={loadAdminData}
          disabled={adminLoading}
          className="inline-flex cursor-pointer items-center gap-2 self-start rounded-xl bg-(--card) px-4 py-2 text-sm text-(--muted) transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 sm:self-auto"
          style={{ boxShadow: "var(--shadow-sm)" }}
          aria-label="Refresh admin data"
        >
          <RefreshCw className={`h-4 w-4 ${adminLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Content */}
      {adminLoading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
        </div>
      ) : adminTab === "users" ? (
        <div className="space-y-2">
          {adminUsersError && (
            <p className="rounded-xl border border-(--border) bg-(--card) px-4 py-3 text-sm text-(--muted)">
              {adminUsersError}
            </p>
          )}

          {adminUsers.length === 0 ? (
            <p className="py-8 text-center text-sm text-(--muted)">
              {adminUsersError ? "User accounts could not be loaded." : "No registered users yet"}
            </p>
          ) : (
            adminUsers.map((user) => (
              <div key={user.id} className="flex items-center gap-3 rounded-[22px] px-4 py-4" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
                {user.avatarUrl ? (
                  <img
                    src={user.avatarUrl}
                    className="h-9 w-9 shrink-0 rounded-full object-cover"
                    alt=""
                  />
                ) : (
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-(--accent) text-sm font-bold text-white">
                    {(user.email || "U")[0].toUpperCase()}
                  </div>
                )}

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">{user.name ?? user.email}</p>
                  <p className="truncate text-xs text-(--muted)">{user.email}</p>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  {user.isAdmin && (
                    <span className="rounded-full bg-(--badge-bg) px-2.5 py-1 text-xs font-medium text-(--badge-fg)">
                      Admin
                    </span>
                  )}
                  <span className="rounded-full bg-(--badge-bg) px-2.5 py-1 text-xs text-(--muted)">
                    {new Date(user.createdAt).toLocaleDateString()}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {adminThreads.length === 0 ? (
            <p className="py-8 text-center text-sm text-(--muted)">No threads found</p>
          ) : (
            adminThreads.map((thread) => (
              <div key={thread.id} className="overflow-hidden rounded-[24px]" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
                <div className="flex items-center gap-2 px-4 py-3">
                  <button
                    onClick={() => handleExpandThread(thread.id)}
                    className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
                  >
                    <ChevronRight
                      className={`h-4 w-4 shrink-0 text-(--muted) transition-transform duration-150 ${
                        expandedThreadId === thread.id ? "rotate-90" : ""
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{thread.name}</p>
                      <p className="text-xs text-(--muted)">
                        {thread.user_identifier ?? "Anonymous"} · {thread.step_count} steps ·{" "}
                        {thread.updated_at
                          ? new Date(thread.updated_at).toLocaleDateString()
                          : "—"}
                      </p>
                    </div>
                  </button>

                  <button
                    onClick={(e) => handleDeleteThread(thread.id, e)}
                    disabled={deletingThreadId === thread.id}
                    className="shrink-0 cursor-pointer rounded-lg p-2 text-(--muted) transition-colors hover:bg-rose-500/10 hover:text-rose-400 disabled:opacity-40"
                    aria-label="Delete thread"
                  >
                    {deletingThreadId === thread.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </button>
                </div>

                {expandedThreadId === thread.id && (
                  <div className="max-h-80 overflow-y-auto border-t border-(--border) bg-background px-4 py-3">
                    {!threadSteps[thread.id] ? (
                      <div className="flex items-center gap-2 py-3 text-sm text-(--muted)">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading steps…
                      </div>
                    ) : threadSteps[thread.id].length === 0 ? (
                      <p className="py-3 text-center text-sm text-(--muted)">No steps</p>
                    ) : (
                      <div className="space-y-2">
                        {threadSteps[thread.id].map((step) => (
                          <div key={step.id} className="rounded-2xl border border-(--border) bg-(--card) px-3 py-3 text-sm">
                            <StepTypeBadge type={step.type} />{" "}
                            <span className="wrap-break-word text-foreground">
                              {(step.input ?? step.output ?? "—").slice(0, 300)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
