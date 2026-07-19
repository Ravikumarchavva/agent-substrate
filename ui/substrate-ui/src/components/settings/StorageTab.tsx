"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownAZ,
  ArrowUpZA,
  Bot,
  Check,
  ChevronDown,
  Clock,
  FolderOpen,
  HardDrive,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  User,
  Weight,
  X,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatFileSize, getAttachmentIcon, getAttachmentKind } from "@/lib/file-utils";
import type { Thread, WorkspaceFile, WorkspaceUsage } from "@/types";

const UPLOADS_KEY = "__uploads__";

type SortKey = "recent" | "oldest" | "largest" | "smallest" | "name-asc" | "name-desc";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "recent", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "largest", label: "Largest" },
  { key: "smallest", label: "Smallest" },
  { key: "name-asc", label: "Name A–Z" },
  { key: "name-desc", label: "Name Z–A" },
];

interface SessionGroup {
  key: string;
  label: string;
  items: WorkspaceFile[];
  totalBytes: number;
  latest: number;
}

function formatDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// Same date buckets and order the chat sidebar uses, so Storage reads like the
// list of conversations the user already knows.
const DATE_BUCKETS = ["Today", "Yesterday", "Last 7 days", "Last 30 days", "Older"] as const;
type DateBucket = (typeof DATE_BUCKETS)[number];

function dateBucket(epochSeconds: number): DateBucket {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const ts = epochSeconds * 1000;
  if (ts >= today) return "Today";
  if (ts >= today - day) return "Yesterday";
  if (ts >= today - 7 * day) return "Last 7 days";
  if (ts >= today - 30 * day) return "Last 30 days";
  return "Older";
}

function sortFiles(files: WorkspaceFile[], sort: SortKey): WorkspaceFile[] {
  const copy = [...files];
  copy.sort((a, b) => {
    switch (sort) {
      case "recent":
        return b.modified_at - a.modified_at;
      case "oldest":
        return a.modified_at - b.modified_at;
      case "largest":
        return b.size_bytes - a.size_bytes;
      case "smallest":
        return a.size_bytes - b.size_bytes;
      case "name-asc":
        return a.name.localeCompare(b.name);
      case "name-desc":
        return b.name.localeCompare(a.name);
    }
  });
  return copy;
}

function sortSessions(sessions: SessionGroup[], sort: SortKey): SessionGroup[] {
  const copy = [...sessions];
  copy.sort((a, b) => {
    switch (sort) {
      case "recent":
        return b.latest - a.latest;
      case "oldest":
        return a.latest - b.latest;
      case "largest":
        return b.totalBytes - a.totalBytes;
      case "smallest":
        return a.totalBytes - b.totalBytes;
      case "name-asc":
        return a.label.localeCompare(b.label);
      case "name-desc":
        return b.label.localeCompare(a.label);
    }
  });
  return copy;
}

export function StorageTab() {
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingPath, setDeletingPath] = useState<string | null>(null);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");
  const [sessionSearch, setSessionSearch] = useState("");
  const [sessionSort, setSessionSort] = useState<SortKey>("recent");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [usageResult, filesResult, threadsResult] = await Promise.all([
        api.getWorkspaceUsage(),
        api.listWorkspaceFiles(),
        api.getThreads().catch(() => [] as Thread[]),
      ]);
      setUsage(usageResult);
      setFiles(filesResult);
      setThreads(threadsResult);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Storage isn't available for this deployment.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Group files by session, then order those groups to mirror the sidebar:
  // threads by their position in /threads (recency), with an "Uploads" bucket
  // for files that don't belong to any conversation.
  const sessions = useMemo<SessionGroup[]>(() => {
    const byKey = new Map<string, SessionGroup>();
    for (const file of files) {
      const key = file.session_id ?? UPLOADS_KEY;
      const label = file.session_id
        ? file.session_name ?? "Untitled conversation"
        : "Uploads";
      const group =
        byKey.get(key) ?? { key, label, items: [], totalBytes: 0, latest: 0 };
      group.items.push(file);
      group.totalBytes += file.size_bytes;
      group.latest = Math.max(group.latest, file.modified_at);
      byKey.set(key, group);
    }

    const order = new Map(threads.map((t, i) => [t.id, i]));
    return Array.from(byKey.values()).sort((a, b) => {
      if (a.key === UPLOADS_KEY) return 1;
      if (b.key === UPLOADS_KEY) return -1;
      const ai = order.get(a.key);
      const bi = order.get(b.key);
      if (ai !== undefined && bi !== undefined) return ai - bi;
      if (ai !== undefined) return -1;
      if (bi !== undefined) return 1;
      return b.latest - a.latest;
    });
  }, [files, threads]);

  // Keep a valid selection as data loads / files are deleted.
  useEffect(() => {
    if (sessions.length === 0) {
      setSelectedKey(null);
    } else if (!sessions.some((s) => s.key === selectedKey)) {
      setSelectedKey(sessions[0].key);
    }
  }, [sessions, selectedKey]);

  const selected = sessions.find((s) => s.key === selectedKey) ?? null;

  const visibleFiles = useMemo(() => {
    if (!selected) return [];
    const term = search.trim().toLowerCase();
    const filtered = term
      ? selected.items.filter((f) => f.name.toLowerCase().includes(term))
      : selected.items;
    return sortFiles(filtered, sort);
  }, [selected, search, sort]);

  const handleDelete = async (file: WorkspaceFile) => {
    if (!confirm(`Delete "${file.name}" permanently? This cannot be undone.`)) return;
    setDeletingPath(file.path);
    try {
      await api.deleteWorkspaceFile(file.path);
      setFiles((prev) => prev.filter((f) => f.path !== file.path));
      setUsage((prev) =>
        prev ? { ...prev, used_bytes: Math.max(0, prev.used_bytes - file.size_bytes) } : prev,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete file.");
    } finally {
      setDeletingPath(null);
    }
  };

  const pct = usage && usage.quota_bytes > 0
    ? Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100)
    : 0;
  const barColour = pct >= 100 ? "bg-red-500" : pct >= 75 ? "bg-amber-400" : "bg-emerald-500";

  // Filter the session list by name, then lay it out. On the default "Newest"
  // sort we keep the sidebar's recency order and date buckets; any other sort
  // flattens into a single sorted list (date buckets don't apply to size/name).
  const sessionSections = useMemo<{ label: string | null; items: SessionGroup[] }[]>(() => {
    const term = sessionSearch.trim().toLowerCase();
    const filtered = term
      ? sessions.filter((s) => s.label.toLowerCase().includes(term))
      : sessions;

    if (sessionSort === "recent") {
      const buckets = new Map<DateBucket, SessionGroup[]>();
      for (const session of filtered) {
        const bucket = session.key === UPLOADS_KEY ? "Older" : dateBucket(session.latest);
        const list = buckets.get(bucket) ?? [];
        list.push(session);
        buckets.set(bucket, list);
      }
      return DATE_BUCKETS.map((b) => ({ label: b as string, items: buckets.get(b) ?? [] })).filter(
        (section) => section.items.length > 0,
      );
    }

    return [{ label: null, items: sortSessions(filtered, sessionSort) }];
  }, [sessions, sessionSearch, sessionSort]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">Storage</h2>
          <p className="max-w-3xl text-sm leading-6 text-(--muted)">
            Files you&apos;ve uploaded and files the assistant created while working
            (e.g. via the code interpreter), organised by conversation.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex cursor-pointer items-center gap-2 self-start rounded-xl bg-(--card) px-4 py-2 text-sm text-(--muted) transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          style={{ boxShadow: "var(--shadow-sm)" }}
          aria-label="Refresh storage data"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <p className="rounded-[18px] bg-(--badge-bg) px-4 py-3 text-sm text-(--muted)">
          {error}
        </p>
      )}

      {usage && (
        <div className="rounded-[24px] p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-(--muted)">
              <HardDrive className="h-3.5 w-3.5" />
              Storage used
            </span>
            <span className="text-xs text-(--muted)">
              {formatFileSize(usage.used_bytes)} of {formatFileSize(usage.quota_bytes)}
            </span>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-(--border)">
            <div
              className={`h-full rounded-full transition-all duration-500 ${barColour}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
        </div>
      ) : sessions.length === 0 ? (
        <p className="py-8 text-center text-sm text-(--muted)">
          No files yet. Uploads and assistant-created files will show up here.
        </p>
      ) : (
        <div className="grid h-[calc(100vh-19rem)] min-h-[26rem] gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          {/* Sessions — ordered like the chat sidebar */}
          <div
            className="flex min-h-0 flex-col rounded-[24px] p-3"
            style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
          >
            <div className="flex items-center gap-2 px-1 pb-3">
              <SearchInput
                value={sessionSearch}
                onChange={setSessionSearch}
                placeholder="Search conversations"
              />
              <SortControl sort={sessionSort} onChange={setSessionSort} />
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-0.5">
              {sessionSections.length === 0 ? (
                <p className="py-8 text-center text-sm text-(--muted)">
                  No conversations match “{sessionSearch}”.
                </p>
              ) : (
                sessionSections.map((section, i) => (
                  <div key={section.label ?? `flat-${i}`} className="space-y-1">
                    {section.label && (
                      <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-(--muted)">
                        {section.label}
                      </p>
                    )}
                    {section.items.map((session) => {
                      const active = session.key === selectedKey;
                      return (
                        <button
                          key={session.key}
                          type="button"
                          onClick={() => {
                            setSelectedKey(session.key);
                            setSearch("");
                          }}
                          className={`flex w-full items-center gap-3 rounded-[18px] px-3 py-2.5 text-left transition-colors ${
                            active ? "bg-background" : "hover:bg-background"
                          }`}
                          style={active ? { boxShadow: "var(--shadow-sm)" } : undefined}
                        >
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-(--badge-bg) text-(--muted)">
                            <FolderOpen className="h-4 w-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">{session.label}</p>
                            <p className="truncate text-xs text-(--muted)">
                              {session.items.length} {session.items.length === 1 ? "file" : "files"} ·{" "}
                              {formatFileSize(session.totalBytes)}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Files for the selected session */}
          <div
            className="flex min-h-0 flex-col rounded-[24px] p-4"
            style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
          >
            {selected && (
              <>
                <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h3 className="truncate text-sm font-semibold text-foreground" title={selected.label}>
                    {selected.label}
                  </h3>
                  <div className="flex items-center gap-2">
                    <SearchInput
                      value={search}
                      onChange={setSearch}
                      placeholder="Search files"
                      className="sm:w-48"
                    />
                    <SortControl sort={sort} onChange={setSort} />
                  </div>
                </div>

                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-0.5">
                  {visibleFiles.length === 0 ? (
                    <p className="py-8 text-center text-sm text-(--muted)">
                      {search ? `No files match “${search}”.` : "No files in this conversation."}
                    </p>
                  ) : (
                    visibleFiles.map((file) => {
                      const Icon = getAttachmentIcon(getAttachmentKind(undefined, file.name));
                      return (
                        <div
                          key={file.path}
                          className="flex items-center gap-3 rounded-[18px] bg-background px-3 py-2.5"
                        >
                          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-(--badge-bg) text-(--muted)">
                            <Icon className="h-4 w-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <p className="truncate text-sm font-semibold text-foreground">{file.name}</p>
                              <OwnerBadge owner={file.owner} />
                            </div>
                            <p className="truncate text-xs text-(--muted)">
                              {formatFileSize(file.size_bytes)} · {formatDate(file.modified_at)}
                            </p>
                          </div>
                          <button
                            onClick={() => void handleDelete(file)}
                            disabled={deletingPath === file.path}
                            className="shrink-0 cursor-pointer rounded-lg p-2 text-(--muted) transition-colors hover:bg-rose-500/10 hover:text-rose-400 disabled:opacity-40"
                            aria-label={`Delete ${file.name}`}
                          >
                            {deletingPath === file.path ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Trash2 className="h-4 w-4" />
                            )}
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function OwnerBadge({ owner }: { owner: WorkspaceFile["owner"] }) {
  const isUser = owner === "user";
  const Icon = isUser ? User : Bot;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
        isUser
          ? "bg-sky-500/12 text-sky-500"
          : "bg-violet-500/12 text-violet-500"
      }`}
    >
      <Icon className="h-2.5 w-2.5" />
      {isUser ? "You" : "Assistant"}
    </span>
  );
}

function SearchInput({
  value,
  onChange,
  placeholder,
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  className?: string;
}) {
  return (
    <div
      className={`flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-(--border) bg-background px-3 py-2 text-sm transition focus-within:ring-2 focus-within:ring-(--accent) ${className}`}
    >
      <Search className="h-4 w-4 shrink-0 text-(--muted)" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full min-w-0 bg-transparent text-sm text-foreground outline-none placeholder:text-(--muted)"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          className="cursor-pointer rounded-lg p-0.5 text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground"
          aria-label="Clear search"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

const SORT_ICONS: Record<SortKey, LucideIcon> = {
  recent: Clock,
  oldest: Clock,
  largest: Weight,
  smallest: Weight,
  "name-asc": ArrowDownAZ,
  "name-desc": ArrowUpZA,
};

// Custom dropdown mirroring the model-selection selects (ModelsTab): a bordered
// trigger + a floating `substrate-scale-in` panel with a check on the active row.
function SortControl({ sort, onChange }: { sort: SortKey; onChange: (s: SortKey) => void }) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setIsOpen(false);
    }
    if (isOpen) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const TriggerIcon = SORT_ICONS[sort];
  const selectedLabel = SORT_OPTIONS.find((o) => o.key === sort)?.label ?? "Sort";

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className="flex cursor-pointer items-center gap-2 rounded-xl border border-(--border) bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
        aria-label="Sort"
      >
        <TriggerIcon className="h-4 w-4 shrink-0 text-(--muted)" />
        <span className="hidden truncate sm:inline">{selectedLabel}</span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>

      {isOpen && (
        <div
          className="substrate-scale-in absolute right-0 z-50 mt-2 w-44 overflow-hidden rounded-xl border border-(--border) p-1 shadow-xl"
          style={{ background: "var(--card)", transformOrigin: "top right" }}
        >
          <div className="flex flex-col gap-0.5">
            {SORT_OPTIONS.map((opt) => {
              const OptIcon = SORT_ICONS[opt.key];
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => {
                    onChange(opt.key);
                    setIsOpen(false);
                  }}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    sort === opt.key
                      ? "bg-foreground/10 font-medium text-foreground"
                      : "text-foreground hover:bg-(--card-hover)"
                  }`}
                >
                  <OptIcon className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
                  <span className="flex-1 truncate">{opt.label}</span>
                  {sort === opt.key && <Check className="h-4 w-4 shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
