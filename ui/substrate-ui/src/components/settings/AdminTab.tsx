"use client";

import React, { useState } from "react";
import { ChevronRight, Loader2, Pencil, RefreshCw, Trash2 } from "lucide-react";
import type {
  AdminStats,
  AdminStep,
  AdminStorageSession,
  AdminStorageUser,
  AdminThread,
  AdminUser,
} from "@/types";

interface AdminTabProps {
  adminStats: AdminStats | null;
  adminUsers: AdminUser[];
  adminThreads: AdminThread[];
  adminLoading: boolean;
  adminLoadNotice: string | null;
  adminUsersError: string | null;
  adminTab: "users" | "threads" | "storage";
  setAdminTab: (tab: "users" | "threads" | "storage") => void;
  expandedThreadId: string | null;
  threadSteps: Record<string, AdminStep[]>;
  deletingThreadId: string | null;
  handleExpandThread: (threadId: string) => void;
  handleDeleteThread: (threadId: string, event: React.MouseEvent) => void;
  loadAdminData: () => void;
  // Storage tab — lazy-loaded on first switch, not part of loadAdminData's
  // Promise.allSettled batch (see SettingsPanel.tsx).
  adminStorageUsers: AdminStorageUser[];
  adminStorageLoading: boolean;
  adminStorageSessions: Record<string, AdminStorageSession[]>;
  expandedStorageUserId: string | null;
  handleExpandStorageUser: (userId: string) => void;
  savingQuotaUserId: string | null;
  handleSaveQuota: (userId: string, quotaBytes: number | null) => void;
}

const tabOptions = ["users", "threads", "storage"] as const;

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function QuotaEditor({
  userId,
  quotaBytes,
  saving,
  onSave,
}: {
  userId: string;
  quotaBytes: number;
  saving: boolean;
  onSave: (userId: string, quotaBytes: number | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(() => (quotaBytes / (1024 * 1024)).toString());

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          setValue((quotaBytes / (1024 * 1024)).toString());
          setEditing(true);
        }}
        className="inline-flex cursor-pointer items-center gap-1 text-(--muted) transition-colors hover:text-foreground"
        aria-label="Edit quota"
      >
        <Pencil className="h-3 w-3" />
      </button>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <input
        type="number"
        min={0}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-20 rounded-lg border border-(--border) bg-background px-2 py-1 text-xs"
        autoFocus
      />
      <span className="text-xs text-(--muted)">MB</span>
      <button
        type="button"
        disabled={saving}
        onClick={() => {
          const mb = Number(value);
          onSave(userId, Number.isFinite(mb) && mb >= 0 ? mb * 1024 * 1024 : null);
          setEditing(false);
        }}
        className="cursor-pointer rounded-lg bg-foreground px-2 py-1 text-xs font-medium text-background disabled:opacity-50"
      >
        {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
      </button>
      <button
        type="button"
        onClick={() => setEditing(false)}
        className="cursor-pointer text-xs text-(--muted) hover:text-foreground"
      >
        Cancel
      </button>
    </span>
  );
}

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
  adminStorageUsers,
  adminStorageLoading,
  adminStorageSessions,
  expandedStorageUserId,
  handleExpandStorageUser,
  savingQuotaUserId,
  handleSaveQuota,
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
              {tab} (
              {tab === "users"
                ? adminUsers.length
                : tab === "threads"
                  ? adminThreads.length
                  : adminStorageUsers.length}
              )
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
      {(adminTab === "storage" ? adminStorageLoading : adminLoading) ? (
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
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-(--accent) text-sm font-bold text-(--accent-foreground)">
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
      ) : adminTab === "threads" ? (
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
                      <p className="truncate text-sm font-semibold">
                        {thread.name}
                        {thread.deleted_at && (
                          <span className="ml-2 rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-400">
                            Deleted
                          </span>
                        )}
                      </p>
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
      ) : (
        <div className="space-y-2">
          {adminStorageUsers.length === 0 ? (
            <p className="py-8 text-center text-sm text-(--muted)">No user workspaces yet</p>
          ) : (
            adminStorageUsers.map((user) => {
              const pct = user.quota_bytes > 0 ? user.used_bytes / user.quota_bytes : 0;
              return (
                <div
                  key={user.user_id}
                  className="overflow-hidden rounded-[24px]"
                  style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
                >
                  <div className="flex items-center gap-3 px-4 py-4">
                    <button
                      onClick={() => handleExpandStorageUser(user.user_id)}
                      className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
                    >
                      <ChevronRight
                        className={`h-4 w-4 shrink-0 text-(--muted) transition-transform duration-150 ${
                          expandedStorageUserId === user.user_id ? "rotate-90" : ""
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">{user.user_id}</p>
                        <p className="text-xs text-(--muted)">
                          {user.session_count} session{user.session_count === 1 ? "" : "s"}
                        </p>
                      </div>
                    </button>

                    <div className="w-40 shrink-0">
                      <div className="h-1.5 overflow-hidden rounded-full bg-(--panel-muted)">
                        <div
                          className={`h-full rounded-full ${pct > 0.9 ? "bg-rose-400" : "bg-foreground"}`}
                          style={{ width: `${Math.min(100, pct * 100)}%` }}
                        />
                      </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-2 text-xs text-(--muted)">
                      <span>
                        {formatBytes(user.used_bytes)} / {formatBytes(user.quota_bytes)}
                      </span>
                      <QuotaEditor
                        userId={user.user_id}
                        quotaBytes={user.quota_bytes}
                        saving={savingQuotaUserId === user.user_id}
                        onSave={handleSaveQuota}
                      />
                    </div>
                  </div>

                  {expandedStorageUserId === user.user_id && (
                    <div className="max-h-80 overflow-y-auto border-t border-(--border) bg-background px-4 py-3">
                      {!adminStorageSessions[user.user_id] ? (
                        <div className="flex items-center gap-2 py-3 text-sm text-(--muted)">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Loading sessions…
                        </div>
                      ) : adminStorageSessions[user.user_id].length === 0 ? (
                        <p className="py-3 text-center text-sm text-(--muted)">No sessions</p>
                      ) : (
                        <div className="space-y-2">
                          {adminStorageSessions[user.user_id].map((session) => (
                            <div
                              key={session.session_id}
                              className="flex items-center justify-between rounded-2xl border border-(--border) bg-(--card) px-3 py-3 text-sm"
                            >
                              <span className="truncate text-foreground">{session.session_id}</span>
                              <span className="shrink-0 text-xs text-(--muted)">
                                {formatBytes(session.size_bytes)} · {session.file_count} file
                                {session.file_count === 1 ? "" : "s"}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
