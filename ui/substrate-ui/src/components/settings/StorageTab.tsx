"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowDownAZ,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ArrowUpZA,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Eye,
  FileCode,
  FileSpreadsheet,
  FileText,
  Folder,
  HardDrive,
  ImageIcon,
  LayoutGrid,
  List,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  User,
  Weight,
  X,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { buildObjectUrl, buildWorkspaceFileUrl } from "@/lib/api/_client";
import { formatFileSize, getFileExtension } from "@/lib/file-utils";
import { FileArtifactViewer } from "@/components/FileArtifactViewer";
import { FileTypeIcon, type FileTypeIconSize } from "@/components/FileTypeIcon";
import type { Thread, WorkspaceFile, WorkspaceUsage } from "@/types";

// ─── Constants & Types ───────────────────────────────────────────────────────

const UPLOADS_KEY = "__uploads__";

type ViewMode = "grid" | "list";
type GridSize = "sm" | "md" | "lg" | "xl";

// Explorer-style tile density for grid view — column width, the icon slot's
// height, an inline-image thumbnail's edge length, and which FileIconDisplay
// size renders the folded-corner icon at that scale.
const GRID_SIZE_PRESETS: Record<
  GridSize,
  { label: string; minmax: string; iconBox: string; thumb: string; iconSize: "sm" | "md" | "lg" | "xl" }
> = {
  sm: { label: "Small", minmax: "minmax(64px, 72px)", iconBox: "h-9", thumb: "h-7 w-7", iconSize: "sm" },
  md: { label: "Medium", minmax: "minmax(86px, 96px)", iconBox: "h-14", thumb: "h-12 w-12", iconSize: "lg" },
  lg: { label: "Large", minmax: "minmax(110px, 130px)", iconBox: "h-20", thumb: "h-[72px] w-[72px]", iconSize: "lg" },
  xl: { label: "Extra large", minmax: "minmax(150px, 180px)", iconBox: "h-28", thumb: "h-24 w-24", iconSize: "xl" },
};
type NavigationLocation =
  | { type: "drive" }
  | { type: "folder"; id: string; name: string }
  | { type: "recent" }
  | { type: "uploads" }
  | { type: "assistant" }
  | { type: "category"; category: "pdf" | "doc" | "sheet" | "image" | "code" };

type SortKey = "recent" | "oldest" | "largest" | "smallest" | "name-asc" | "name-desc";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "recent", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "largest", label: "Largest" },
  { key: "smallest", label: "Smallest" },
  { key: "name-asc", label: "Name A–Z" },
  { key: "name-desc", label: "Name Z–A" },
];

const SORT_ICONS: Record<SortKey, LucideIcon> = {
  recent: Clock,
  oldest: Clock,
  largest: Weight,
  smallest: Weight,
  "name-asc": ArrowDownAZ,
  "name-desc": ArrowUpZA,
};

interface SessionFolder {
  id: string;
  name: string;
  files: WorkspaceFile[];
  totalBytes: number;
  latestModified: number;
}

// ─── Utilities ───────────────────────────────────────────────────────────────

function formatDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatFullDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type FileCategory = "pdf" | "doc" | "sheet" | "image" | "code" | "media" | "other";

function getFileCategory(name: string): FileCategory {
  const ext = getFileExtension(name).toLowerCase();
  if (ext === "pdf") return "pdf";
  if (["md", "markdown", "txt", "doc", "docx", "rtf", "odt"].includes(ext)) return "doc";
  if (["csv", "tsv", "xlsx", "xls", "ods", "parquet"].includes(ext)) return "sheet";
  if (["png", "jpg", "jpeg", "webp", "svg", "gif", "bmp", "avif"].includes(ext)) return "image";
  if (["py", "js", "ts", "tsx", "jsx", "json", "yaml", "yml", "sh", "sql", "html", "css", "toml"].includes(ext))
    return "code";
  if (["mp3", "wav", "mp4", "mov", "webm", "ogg", "m4a"].includes(ext)) return "media";
  return "other";
}

/** Break long, space-less filenames (e.g. "FY24_Q1_Consolidated.pdf") at
 * underscores/hyphens/dots instead of letting the browser pick an
 * arbitrary mid-syllable point — real file managers' text layout engines
 * do the equivalent. `<wbr>` is a zero-width, invisible opportunity to
 * wrap; it does nothing unless the browser actually needs to break there. */
function renderBreakableName(name: string) {
  const parts = name.split(/(?<=[_\-.])/g);
  return parts.map((part, i) => (
    <span key={i}>
      {part}
      {i < parts.length - 1 && <wbr />}
    </span>
  ));
}

function sortFiles(files: WorkspaceFile[], sort: SortKey): WorkspaceFile[] {
  const copy = [...files];
  copy.sort((a, b) => {
    switch (sort) {
      case "recent": return b.modified_at - a.modified_at;
      case "oldest": return a.modified_at - b.modified_at;
      case "largest": return b.size_bytes - a.size_bytes;
      case "smallest": return a.size_bytes - b.size_bytes;
      case "name-asc": return a.name.localeCompare(b.name);
      case "name-desc": return b.name.localeCompare(a.name);
    }
  });
  return copy;
}

function sortFolders(folders: SessionFolder[], sort: SortKey): SessionFolder[] {
  const copy = [...folders];
  copy.sort((a, b) => {
    switch (sort) {
      case "recent": return b.latestModified - a.latestModified;
      case "oldest": return a.latestModified - b.latestModified;
      case "largest": return b.totalBytes - a.totalBytes;
      case "smallest": return a.totalBytes - b.totalBytes;
      case "name-asc": return a.name.localeCompare(b.name);
      case "name-desc": return b.name.localeCompare(a.name);
    }
  });
  return copy;
}

// ─── Subcomponents ───────────────────────────────────────────────────────────

function SidebarItem({
  icon: Icon,
  iconColor,
  glyphName,
  label,
  badge,
  active,
  onClick,
}: {
  icon: LucideIcon;
  iconColor?: string;
  /** File name whose extension picks a getFileGlyph icon+color — renders
   *  the same flat colored-square badge as a file's own tile (FileTypeIcon)
   *  instead of a plain line icon, so a "PDFs"/"Images"/etc. filter reads as
   *  a miniature file icon, not just a colored outline. */
  glyphName?: string;
  label: string;
  badge?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] transition-colors cursor-pointer ${
        active
          ? "bg-background font-semibold text-foreground shadow-xs"
          : "font-medium text-(--muted) hover:bg-background/50 hover:text-foreground"
      }`}
    >
      {glyphName ? (
        <FileTypeIcon name={glyphName} size="sm" className="shrink-0" />
      ) : (
        <Icon
          className={`h-[15px] w-[15px] shrink-0 ${
            active ? "text-(--accent)" : iconColor ?? "text-(--muted)"
          }`}
        />
      )}
      <span className="flex-1 truncate text-left">{label}</span>
      {badge !== undefined && (
        <span
          className={`inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums leading-none shrink-0 ${
            active ? "bg-(--badge-bg) text-foreground" : "bg-background/60 text-(--muted)"
          }`}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

function SourcePill({ owner }: { owner: WorkspaceFile["owner"] }) {
  const isUser = owner === "user";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-[2px] text-[10px] font-medium leading-none shrink-0 ${
        isUser
          ? "bg-sky-500/12 text-sky-400 ring-1 ring-sky-500/20"
          : "bg-purple-500/12 text-purple-400 ring-1 ring-purple-500/20"
      }`}
    >
      {isUser ? (
        <User className="h-[10px] w-[10px] shrink-0" />
      ) : (
        <Bot className="h-[10px] w-[10px] shrink-0" />
      )}
      {isUser ? "You" : "Assistant"}
    </span>
  );
}

const GRID_SIZE_ORDER: GridSize[] = ["sm", "md", "lg", "xl"];
const GRID_SIZE_SHORT_LABEL: Record<GridSize, string> = { sm: "S", md: "M", lg: "L", xl: "XL" };

function GridSizeControl({ size, onChange }: { size: GridSize; onChange: (s: GridSize) => void }) {
  return (
    <div className="flex h-8 items-center rounded-lg border border-(--border) bg-background/50 shrink-0" role="group" aria-label="Tile size">
      {GRID_SIZE_ORDER.map((key, i) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          title={GRID_SIZE_PRESETS[key].label}
          className={`flex h-8 min-w-8 items-center justify-center px-1.5 text-[10px] font-semibold transition cursor-pointer ${
            i === 0 ? "rounded-l-lg" : "border-l border-(--border)/60"
          } ${i === GRID_SIZE_ORDER.length - 1 ? "rounded-r-lg" : ""} ${
            size === key ? "bg-(--card) text-foreground" : "text-(--muted) hover:text-foreground"
          }`}
        >
          {GRID_SIZE_SHORT_LABEL[key]}
        </button>
      ))}
    </div>
  );
}

function SortDropdown({ sort, onChange }: { sort: SortKey; onChange: (s: SortKey) => void }) {
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
        onClick={() => setIsOpen((o) => !o)}
        className="flex h-8 items-center gap-1.5 rounded-lg border border-(--border) bg-background/60 px-2.5 text-xs font-medium text-foreground transition hover:bg-(--card) cursor-pointer"
        aria-label="Sort options"
      >
        <TriggerIcon className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
        <span className="hidden sm:inline">{selectedLabel}</span>
        <ChevronDown className="h-3 w-3 shrink-0 opacity-50" />
      </button>

      {isOpen && (
        <div
          className="absolute right-0 z-50 mt-1.5 w-40 overflow-hidden rounded-xl border border-(--border) p-1 shadow-xl"
          style={{ background: "var(--card)" }}
        >
          {SORT_OPTIONS.map((opt) => {
            const OptIcon = SORT_ICONS[opt.key];
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => { onChange(opt.key); setIsOpen(false); }}
                className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition cursor-pointer ${
                  sort === opt.key
                    ? "bg-foreground/8 font-semibold text-foreground"
                    : "text-(--muted) hover:bg-background hover:text-foreground"
                }`}
              >
                <OptIcon className="h-3.5 w-3.5 shrink-0 text-(--muted)" />
                <span className="flex-1">{opt.label}</span>
                {sort === opt.key && <Check className="h-3.5 w-3.5 shrink-0 text-foreground" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Shared menu body — used by both the per-card 3-dot dropdown and the
 * right-click context menu below, so the two triggers can't drift apart. */
function FileMenuContent({
  file,
  onInspect,
  onDelete,
  downloadUrl,
  onClose,
}: {
  file: WorkspaceFile;
  onInspect: () => void;
  onDelete: () => void;
  downloadUrl: string;
  onClose: () => void;
}) {
  return (
    <>
      <div className="px-3 py-2 border-b border-(--border)/40">
        <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-(--muted)/60 mb-1">Details</p>
        <p className="text-[11px] text-(--muted)">{formatFileSize(file.size_bytes)} · {formatDate(file.modified_at)}</p>
        <p className="text-[11px] text-(--muted) mt-0.5">
          {file.owner === "user" ? "Uploaded by you" : "Generated by assistant"}
        </p>
      </div>
      <div className="mt-1 space-y-0.5">
        <button
          type="button"
          onClick={() => { onInspect(); onClose(); }}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-(--muted) hover:bg-background hover:text-foreground transition cursor-pointer"
        >
          <Eye className="h-3.5 w-3.5 shrink-0" />
          Inspect
        </button>
        <a
          href={downloadUrl}
          download={file.name}
          onClick={onClose}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-(--muted) hover:bg-background hover:text-foreground transition cursor-pointer"
        >
          <Download className="h-3.5 w-3.5 shrink-0" />
          Download
        </a>
        <button
          type="button"
          onClick={() => { onDelete(); onClose(); }}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-rose-400/80 hover:bg-rose-500/10 hover:text-rose-400 transition cursor-pointer"
        >
          <Trash2 className="h-3.5 w-3.5 shrink-0" />
          Delete
        </button>
      </div>
    </>
  );
}

/** Right-click context menu — opens at the cursor. The only way to reach
 * per-file actions (Inspect/Download/Delete); there's no persistent 3-dot
 * button cluttering every card — same as a real desktop file manager,
 * where right-click is the primary discovery path. Portal-rendered at
 * document.body so it always floats above the panel regardless of any
 * ancestor's overflow/z-index. One instance, shared by the whole explorer
 * (mounted once in StorageTab, driven by a single `contextMenu` state)
 * rather than one per row/card — real file managers only ever show one
 * context menu at a time regardless of how many items exist. */
function FileContextMenu({
  file,
  x,
  y,
  onInspect,
  onDelete,
  downloadUrl,
  onClose,
}: {
  file: WorkspaceFile;
  x: number;
  y: number;
  onInspect: () => void;
  onDelete: () => void;
  downloadUrl: string;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) onClose();
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    window.addEventListener("scroll", onClose, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
      window.removeEventListener("scroll", onClose, true);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  // Clamp so the menu never opens off the right/bottom edge of the viewport.
  const menuWidth = 192;
  const menuHeight = 180;
  const left = Math.min(x, window.innerWidth - menuWidth - 8);
  const top = Math.min(y, window.innerHeight - menuHeight - 8);

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-50 w-48 overflow-hidden rounded-xl border border-(--border) p-1 shadow-xl"
      style={{ background: "var(--card)", top, left }}
      onClick={(e) => e.stopPropagation()}
    >
      <FileMenuContent
        file={file}
        onInspect={onInspect}
        onDelete={onDelete}
        downloadUrl={downloadUrl}
        onClose={onClose}
      />
    </div>,
    document.body
  );
}

/** The app-wide flat colored-square file icon (see FileTypeIcon.tsx) — one
 * shared component/mapping instead of this tab's own separate shape. */
function FileIconDisplay({ file, size = "md" }: { file: WorkspaceFile; size?: FileTypeIconSize }) {
  return <FileTypeIcon name={file.name} size={size} />;
}

// ─── Main Component ──────────────────────────────────────────────────────────

export function StorageTab() {
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [location, setLocation] = useState<NavigationLocation>({ type: "drive" });
  const [history, setHistory] = useState<NavigationLocation[]>([{ type: "drive" }]);
  const [historyIndex, setHistoryIndex] = useState(0);

  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [gridSize, setGridSize] = useState<GridSize>("md");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");

  const [inspectedFile, setInspectedFile] = useState<WorkspaceFile | null>(null);
  const [deletingFile, setDeletingFile] = useState<WorkspaceFile | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Click-to-select / double-click-to-open, like a real file manager —
  // single click was previously wired straight to openInspector, so there
  // was no way to just highlight a file without opening it.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; file: WorkspaceFile } | null>(null);
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const explorerRef = useRef<HTMLDivElement>(null);

  // ─── Data Loading ──────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const withTimeout = <T,>(p: Promise<T>, ms: number, fallback: T): Promise<T> =>
      Promise.race([p, new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms))]);

    try {
      const [usageResult, filesResult, threadsResult] = await Promise.allSettled([
        withTimeout(api.getWorkspaceUsage(), 4000, null),
        withTimeout(api.listWorkspaceFiles(), 6000, [] as WorkspaceFile[]),
        withTimeout(api.getThreads().catch(() => [] as Thread[]), 3000, [] as Thread[]),
      ]);
      if (usageResult.status === "fulfilled" && usageResult.value) setUsage(usageResult.value);
      if (filesResult.status === "fulfilled") {
        // .extracted.md sidecars (agent-substrate writes one next to every
        // staged PDF/doc, for code_interpreter's benefit — see
        // routes/files.py::_write_extracted_sidecar) are an implementation
        // detail, not a document the user uploaded or generated; listing
        // them here just doubles the visible file count for one logical
        // document.
        setFiles(filesResult.value.filter((f) => !f.name.endsWith(".extracted.md")));
      } else {
        setError("Could not load workspace files. Please click Refresh.");
      }
      if (threadsResult.status === "fulfilled") setThreads(threadsResult.value);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Storage isn't available for this deployment.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // ─── Navigation ────────────────────────────────────────────────────────────

  const navigateTo = useCallback((next: NavigationLocation) => {
    setSearch("");
    setHistory((prev) => [...prev.slice(0, historyIndex + 1), next]);
    setHistoryIndex((prev) => prev + 1);
    setLocation(next);
  }, [historyIndex]);

  const goBack = useCallback(() => {
    if (historyIndex > 0) {
      const i = historyIndex - 1;
      setHistoryIndex(i);
      setLocation(history[i]);
      setSearch("");
    }
  }, [historyIndex, history]);

  const goForward = useCallback(() => {
    if (historyIndex < history.length - 1) {
      const i = historyIndex + 1;
      setHistoryIndex(i);
      setLocation(history[i]);
      setSearch("");
    }
  }, [historyIndex, history]);

  const goUp = useCallback(() => {
    if (location.type !== "drive") navigateTo({ type: "drive" });
  }, [location, navigateTo]);

  // Selection resets on navigation — a selected file's index is only
  // meaningful within the folder it was selected in.
  useEffect(() => {
    setSelected(new Set());
    setLastSelectedIndex(null);
  }, [location]);

  // ─── Data ──────────────────────────────────────────────────────────────────

  const sessionFolders = useMemo<SessionFolder[]>(() => {
    const byKey = new Map<string, SessionFolder>();
    for (const file of files) {
      const id = file.session_id ?? UPLOADS_KEY;
      const name = file.session_id ? file.session_name ?? "Untitled conversation" : "Uploads";
      const folder = byKey.get(id) ?? { id, name, files: [], totalBytes: 0, latestModified: 0 };
      folder.files.push(file);
      folder.totalBytes += file.size_bytes;
      folder.latestModified = Math.max(folder.latestModified, file.modified_at);
      byKey.set(id, folder);
    }
    const threadOrder = new Map(threads.map((t, i) => [t.id, i]));
    const list = Array.from(byKey.values()).sort((a, b) => {
      if (a.id === UPLOADS_KEY) return 1;
      if (b.id === UPLOADS_KEY) return -1;
      const ai = threadOrder.get(a.id);
      const bi = threadOrder.get(b.id);
      if (ai !== undefined && bi !== undefined) return ai - bi;
      if (ai !== undefined) return -1;
      if (bi !== undefined) return 1;
      return b.latestModified - a.latestModified;
    });
    return sortFolders(list, sort);
  }, [files, threads, sort]);

  const currentViewData = useMemo(() => {
    const term = search.trim().toLowerCase();

    if (location.type === "drive") {
      const folders = term ? sessionFolders.filter((f) => f.name.toLowerCase().includes(term)) : sessionFolders;
      return { title: "Home Drive", folders, files: [] as WorkspaceFile[] };
    }
    if (location.type === "folder") {
      const folder = sessionFolders.find((f) => f.id === location.id);
      const all = folder ? folder.files : [];
      const filtered = term ? all.filter((f) => f.name.toLowerCase().includes(term)) : all;
      return { title: location.name, folders: [] as SessionFolder[], files: sortFiles(filtered, sort) };
    }
    if (location.type === "recent") {
      const recent = [...files].sort((a, b) => b.modified_at - a.modified_at);
      const filtered = term ? recent.filter((f) => f.name.toLowerCase().includes(term)) : recent;
      return { title: "Recent Files", folders: [] as SessionFolder[], files: sortFiles(filtered, sort) };
    }
    if (location.type === "uploads") {
      const userFiles = files.filter((f) => f.owner === "user");
      const filtered = term ? userFiles.filter((f) => f.name.toLowerCase().includes(term)) : userFiles;
      return { title: "User Uploads", folders: [] as SessionFolder[], files: sortFiles(filtered, sort) };
    }
    if (location.type === "assistant") {
      const agentFiles = files.filter((f) => f.owner === "agent");
      const filtered = term ? agentFiles.filter((f) => f.name.toLowerCase().includes(term)) : agentFiles;
      return { title: "Assistant Artifacts", folders: [] as SessionFolder[], files: sortFiles(filtered, sort) };
    }
    if (location.type === "category") {
      const cat = location.category;
      const catFiles = files.filter((f) => getFileCategory(f.name) === cat);
      const filtered = term ? catFiles.filter((f) => f.name.toLowerCase().includes(term)) : catFiles;
      const titles: Record<string, string> = {
        pdf: "PDF Documents", doc: "Text & Markdown", sheet: "Spreadsheets & CSV", image: "Images", code: "Code & Scripts",
      };
      return { title: titles[cat], folders: [] as SessionFolder[], files: sortFiles(filtered, sort) };
    }
    return { title: "Files", folders: [] as SessionFolder[], files: [] as WorkspaceFile[] };
  }, [location, sessionFolders, files, search, sort]);

  const getDownloadUrl = (file: WorkspaceFile) =>
    // buildWorkspaceFileUrl needs the path *relative to the conversation's
    // shared dir* (it re-resolves the full backend key from thread_id +
    // this), not just the basename — file.name alone 404s (or serves the
    // wrong file) for anything inside a subfolder. file.path is the full
    // backend object key; strip everything through .../workspace/shared/.
    file.session_id
      ? buildWorkspaceFileUrl(file.session_id, file.path.replace(/^.*\/workspace\/shared\//, ""))
      : buildObjectUrl(file.path);

  // Single click: select (Ctrl/Cmd toggles one, Shift range-selects from
  // the last click). Double click: open. Same model as Nautilus/Dolphin/
  // Explorer — a click no longer jumps straight into the inspector.
  const handleFileClick = useCallback((file: WorkspaceFile, index: number, e: React.MouseEvent) => {
    if (e.shiftKey && lastSelectedIndex !== null) {
      const [start, end] = [Math.min(lastSelectedIndex, index), Math.max(lastSelectedIndex, index)];
      const range = currentViewData.files.slice(start, end + 1).map((f) => f.path);
      setSelected(new Set(range));
    } else if (e.metaKey || e.ctrlKey) {
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(file.path)) next.delete(file.path);
        else next.add(file.path);
        return next;
      });
      setLastSelectedIndex(index);
    } else {
      setSelected(new Set([file.path]));
      setLastSelectedIndex(index);
    }
  }, [lastSelectedIndex, currentViewData.files]);

  const handleFileContextMenu = useCallback((file: WorkspaceFile, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Right-clicking an unselected file selects just that one first — same
    // as every desktop file manager (right-click never acts on a stale
    // selection from a different file).
    if (!selected.has(file.path)) setSelected(new Set([file.path]));
    setContextMenu({ x: e.clientX, y: e.clientY, file });
  }, [selected]);

  const selectedFiles = useMemo(
    () => currentViewData.files.filter((f) => selected.has(f.path)),
    [currentViewData.files, selected],
  );

  // A refresh (or another tab deleting a file) can drop paths out of
  // `files` while `selected` still holds them — without this, the count
  // shown (`selected.size`) drifts from reality (e.g. "1 of 0 selected").
  // Prune to whatever still actually exists whenever the file list changes.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(currentViewData.files.map((f) => f.path));
      const next = new Set([...prev].filter((p) => valid.has(p)));
      return next.size === prev.size ? prev : next;
    });
  }, [currentViewData.files]);

  const executeBulkDelete = async () => {
    setIsBulkDeleting(true);
    try {
      const toDelete = selectedFiles;
      // allSettled, not all: one locked/already-gone file must not stop the
      // rest from deleting, and must not leave successfully-deleted files
      // still showing in the UI as if the whole batch failed.
      const results = await Promise.allSettled(
        toDelete.map((f) => api.deleteWorkspaceFile(f.path)),
      );
      const succeeded = toDelete.filter((_f, i) => results[i]?.status === "fulfilled");
      const failed = toDelete.filter((_f, i) => results[i]?.status === "rejected");

      const deletedPaths = new Set(succeeded.map((f) => f.path));
      setFiles((prev) => prev.filter((f) => !deletedPaths.has(f.path)));
      const freedBytes = succeeded.reduce((a, f) => a + f.size_bytes, 0);
      setUsage((prev) => (prev ? { ...prev, used_bytes: Math.max(0, prev.used_bytes - freedBytes) } : prev));
      // Keep only the files that failed selected, so the user can see
      // exactly what's left and retry just those.
      setSelected(new Set(failed.map((f) => f.path)));
      setBulkDeleteConfirm(false);

      if (failed.length > 0) {
        setError(
          succeeded.length > 0
            ? `Deleted ${succeeded.length} of ${toDelete.length} files. ${failed.length} failed: ${failed.map((f) => f.name).join(", ")}.`
            : `Failed to delete ${failed.length === 1 ? failed[0].name : `${failed.length} files`}.`,
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete files.");
    } finally {
      setIsBulkDeleting(false);
    }
  };

  // Keyboard shortcuts — Escape clears selection (or closes an open
  // modal/menu first), Delete removes the selection, Ctrl/Cmd+A selects
  // everything in the current view. Disabled while a modal/menu already
  // owns keyboard focus, or while typing in the search box.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
      if (inspectedFile || deletingFile || bulkDeleteConfirm || contextMenu) return;

      if (e.key === "Escape") {
        setSelected(new Set());
      } else if ((e.key === "Delete" || e.key === "Backspace") && selected.size > 0) {
        e.preventDefault();
        if (selected.size === 1) confirmDelete(selectedFiles[0]);
        else setBulkDeleteConfirm(true);
      } else if ((e.metaKey || e.ctrlKey) && e.key === "a") {
        e.preventDefault();
        setSelected(new Set(currentViewData.files.map((f) => f.path)));
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [inspectedFile, deletingFile, bulkDeleteConfirm, contextMenu, selected, selectedFiles, currentViewData.files]);

  const openInspector = (file: WorkspaceFile) => {
    setInspectedFile(file);
  };

  const confirmDelete = (file: WorkspaceFile) => setDeletingFile(file);

  const executeDelete = async () => {
    if (!deletingFile) return;
    setIsDeleting(true);
    try {
      await api.deleteWorkspaceFile(deletingFile.path);
      setFiles((prev) => prev.filter((f) => f.path !== deletingFile.path));
      setUsage((prev) =>
        prev ? { ...prev, used_bytes: Math.max(0, prev.used_bytes - deletingFile.size_bytes) } : prev,
      );
      if (inspectedFile?.path === deletingFile.path) setInspectedFile(null);
      setDeletingFile(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete file.");
    } finally {
      setIsDeleting(false);
    }
  };

  const pct = usage && usage.quota_bytes > 0 ? Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100) : 0;
  const barColor = pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-400" : "bg-emerald-500";

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-foreground">Storage Drive</h2>
          <p className="mt-0.5 text-sm text-(--muted)">Browse, inspect, and organize files across your conversations.</p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-8 items-center gap-2 rounded-lg border border-(--border) bg-(--card) px-3 text-xs font-medium text-(--muted) transition hover:text-foreground disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw className={`h-3.5 w-3.5 shrink-0 ${loading ? "animate-spin text-(--accent)" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/8 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Main Explorer Window */}
      <div
        className="grid min-h-[600px] overflow-hidden rounded-2xl border border-(--border) lg:grid-cols-[220px_1fr]"
        style={{ background: "var(--card)" }}
      >
        {/* Sidebar */}
        <aside className="flex flex-col border-b border-(--border) lg:border-b-0 lg:border-r">
          <div className="flex-1 space-y-5 p-3">
            <div className="space-y-0.5">
              <p className="mb-1.5 px-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-(--muted)/60">Places</p>
              <SidebarItem icon={HardDrive} label="Home Drive" badge={sessionFolders.length} active={location.type === "drive"} onClick={() => navigateTo({ type: "drive" })} />
              <SidebarItem icon={Clock} label="Recent" badge={files.length > 0 ? files.length : undefined} active={location.type === "recent"} onClick={() => navigateTo({ type: "recent" })} />
              <SidebarItem icon={Upload} label="User Uploads" badge={files.filter((f) => f.owner === "user").length || undefined} active={location.type === "uploads"} onClick={() => navigateTo({ type: "uploads" })} />
              <SidebarItem icon={Bot} label="Assistant Outputs" badge={files.filter((f) => f.owner === "agent").length || undefined} active={location.type === "assistant"} onClick={() => navigateTo({ type: "assistant" })} />
            </div>
            <div className="space-y-0.5">
              <p className="mb-1.5 px-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-(--muted)/60">File Types</p>
              {/* Same flat colored-square badge a file's own tile uses
                  (FileTypeIcon → getFileGlyph, file-utils.ts) — a representative
                  extension per category — not a plain line icon, so this list
                  reads as miniature file icons matching the explorer grid and
                  chat bubbles exactly, not just matching their colors. */}
              <SidebarItem icon={FileText}        glyphName="x.pdf"  label="PDFs"          active={location.type === "category" && location.category === "pdf"}   onClick={() => navigateTo({ type: "category", category: "pdf" })} />
              <SidebarItem icon={FileText}        glyphName="x.docx" label="Documents"     active={location.type === "category" && location.category === "doc"}   onClick={() => navigateTo({ type: "category", category: "doc" })} />
              <SidebarItem icon={FileSpreadsheet} glyphName="x.xlsx" label="Data & Sheets" active={location.type === "category" && location.category === "sheet"} onClick={() => navigateTo({ type: "category", category: "sheet" })} />
              <SidebarItem icon={ImageIcon}       glyphName="x.png"  label="Images"        active={location.type === "category" && location.category === "image"} onClick={() => navigateTo({ type: "category", category: "image" })} />
              <SidebarItem icon={FileCode}        glyphName="x.js"   label="Code & Scripts" active={location.type === "category" && location.category === "code"}  onClick={() => navigateTo({ type: "category", category: "code" })} />
            </div>
          </div>

          {usage && (
            <div className="border-t border-(--border) p-3">
              <div className="rounded-xl bg-background/40 p-3">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="flex items-center gap-1.5 font-medium text-foreground">
                    <HardDrive className="h-3 w-3 shrink-0 text-(--muted)" />
                    Storage
                  </span>
                  <span className="font-semibold tabular-nums text-foreground">{pct.toFixed(0)}%</span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-(--border)/60">
                  <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
                </div>
                <p className="mt-1.5 text-[10px] text-(--muted)">{formatFileSize(usage.used_bytes)} of {formatFileSize(usage.quota_bytes)}</p>
              </div>
            </div>
          )}
        </aside>

        {/* Main Pane */}
        <div className="flex min-h-0 flex-col">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2.5 border-b border-(--border) px-4 py-2.5">
            {/* Nav */}
            <div className="flex h-8 items-center rounded-lg border border-(--border) bg-background/50 shrink-0">
              <button type="button" onClick={goBack} disabled={historyIndex <= 0} className="flex h-8 w-8 items-center justify-center rounded-l-lg text-(--muted) transition hover:bg-(--card) hover:text-foreground disabled:opacity-30 disabled:pointer-events-none cursor-pointer" title="Back">
                <ArrowLeft className="h-3.5 w-3.5 shrink-0" />
              </button>
              <button type="button" onClick={goForward} disabled={historyIndex >= history.length - 1} className="flex h-8 w-8 items-center justify-center border-l border-(--border)/60 text-(--muted) transition hover:bg-(--card) hover:text-foreground disabled:opacity-30 disabled:pointer-events-none cursor-pointer" title="Forward">
                <ArrowRight className="h-3.5 w-3.5 shrink-0" />
              </button>
              {location.type !== "drive" && (
                <button type="button" onClick={goUp} className="flex h-8 w-8 items-center justify-center rounded-r-lg border-l border-(--border)/60 text-(--muted) transition hover:bg-(--card) hover:text-foreground cursor-pointer" title="Up">
                  <ArrowUp className="h-3.5 w-3.5 shrink-0" />
                </button>
              )}
            </div>

            {/* Breadcrumb */}
            <div className="flex h-8 items-center rounded-lg border border-(--border) bg-background/50 px-3 text-xs shrink-0 min-w-0">
              <button type="button" onClick={() => navigateTo({ type: "drive" })} className={`flex items-center gap-1.5 shrink-0 transition cursor-pointer ${location.type === "drive" ? "font-semibold text-foreground" : "text-(--muted) hover:text-foreground"}`}>
                <HardDrive className="h-3.5 w-3.5 shrink-0" />
                <span>Home</span>
              </button>
              {location.type === "folder" && (
                <>
                  <ChevronRight className="mx-2 h-3 w-3 shrink-0 text-(--muted)/40" />
                  <span className="truncate max-w-[200px] font-semibold text-foreground">{location.name}</span>
                </>
              )}
              {location.type !== "drive" && location.type !== "folder" && (
                <>
                  <ChevronRight className="mx-2 h-3 w-3 shrink-0 text-(--muted)/40" />
                  <span className="truncate max-w-[200px] font-semibold text-foreground">{currentViewData.title}</span>
                </>
              )}
            </div>

            <div className="flex-1" />

            {/* Search */}
            <div className="relative h-8 w-40 sm:w-48 shrink-0">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-(--muted)" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search files…"
                className="h-8 w-full rounded-lg border border-(--border) bg-background/50 pl-8 pr-7 text-xs text-foreground placeholder:text-(--muted) focus:border-(--accent) focus:outline-none focus:ring-1 focus:ring-(--accent) transition"
              />
              {search && (
                <button type="button" onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-(--muted) hover:text-foreground cursor-pointer">
                  <X className="h-3 w-3 shrink-0" />
                </button>
              )}
            </div>

            {/* View toggle */}
            <div className="flex h-8 items-center rounded-lg border border-(--border) bg-background/50 shrink-0">
              <button type="button" onClick={() => setViewMode("grid")} className={`flex h-8 w-8 items-center justify-center rounded-l-lg transition cursor-pointer ${viewMode === "grid" ? "bg-(--card) text-foreground" : "text-(--muted) hover:text-foreground"}`} title="Grid View">
                <LayoutGrid className="h-3.5 w-3.5 shrink-0" />
              </button>
              <button type="button" onClick={() => setViewMode("list")} className={`flex h-8 w-8 items-center justify-center rounded-r-lg border-l border-(--border)/60 transition cursor-pointer ${viewMode === "list" ? "bg-(--card) text-foreground" : "text-(--muted) hover:text-foreground"}`} title="List View">
                <List className="h-3.5 w-3.5 shrink-0" />
              </button>
            </div>

            {viewMode === "grid" && <GridSizeControl size={gridSize} onChange={setGridSize} />}

            <SortDropdown sort={sort} onChange={setSort} />
          </div>

          {/* Canvas */}
          <div
            ref={explorerRef}
            className="flex-1 overflow-y-auto p-5"
            onClick={(e) => { if (e.target === e.currentTarget) setSelected(new Set()); }}
          >
            {loading ? (
              <div className="flex h-72 flex-col items-center justify-center gap-2.5">
                <Loader2 className="h-7 w-7 animate-spin text-(--accent) shrink-0" />
                <p className="text-xs text-(--muted)">Loading storage drive…</p>
              </div>
            ) : currentViewData.folders.length === 0 && currentViewData.files.length === 0 ? (
              <div className="flex h-72 flex-col items-center justify-center gap-3">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-background border border-(--border) shrink-0">
                  <Folder className="h-7 w-7 text-(--muted) opacity-40 shrink-0" />
                </div>
                <div className="text-center max-w-xs">
                  <p className="text-sm font-medium text-foreground">{search ? `No matches for "${search}"` : "This folder is empty"}</p>
                  <p className="mt-1 text-xs text-(--muted)">Files uploaded or generated will appear here.</p>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Folders */}
                {currentViewData.folders.length > 0 && (
                  <section>
                    <p className="mb-3 text-[11px] font-bold uppercase tracking-[0.08em] text-(--muted)">
                      Folders ({currentViewData.folders.length})
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {currentViewData.folders.map((folder) => (
                        <button
                          key={folder.id}
                          type="button"
                          onClick={() => navigateTo({ type: "folder", id: folder.id, name: folder.name })}
                          className="group flex items-center gap-3 rounded-xl border border-(--border) bg-background/40 p-3 text-left transition hover:border-amber-500/30 hover:bg-background hover:shadow-sm cursor-pointer"
                        >
                          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/10 shrink-0">
                            <Folder className="h-5 w-5 shrink-0 fill-amber-500/20 text-amber-500" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-[13px] font-semibold text-foreground group-hover:text-amber-400 transition-colors">{folder.name}</p>
                            <p className="mt-0.5 text-[11px] text-(--muted)">{folder.files.length} {folder.files.length === 1 ? "file" : "files"} · {formatFileSize(folder.totalBytes)}</p>
                          </div>
                          <ChevronRight className="h-4 w-4 shrink-0 text-(--muted)/30 group-hover:text-foreground transition" />
                        </button>
                      ))}
                    </div>
                  </section>
                )}

                {/* Files */}
                {currentViewData.files.length > 0 && (
                  <section>
                    <div className="mb-3 flex items-center justify-between">
                      <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-(--muted)">Files ({currentViewData.files.length})</p>
                      <p className="text-[11px] text-(--muted)">{formatFileSize(currentViewData.files.reduce((a, f) => a + f.size_bytes, 0))}</p>
                    </div>

                    {viewMode === "grid" ? (
                      /* Grid — Nautilus/GNOME Files style: icon + name only.
                       * Click selects (highlight only); double-click opens
                       * the inspector; right-click (or the 3-dot menu)
                       * opens actions — matches every desktop file manager,
                       * instead of a single click jumping straight in. */
                      <div
                        className="grid gap-0.5"
                        style={{ gridTemplateColumns: `repeat(auto-fill, ${GRID_SIZE_PRESETS[gridSize].minmax})` }}
                      >
                        {currentViewData.files.map((file, index) => {
                          const isSelected = selected.has(file.path);
                          const preset = GRID_SIZE_PRESETS[gridSize];
                          return (
                            <div
                              key={file.path}
                              onClick={(e) => handleFileClick(file, index, e)}
                              onDoubleClick={() => void openInspector(file)}
                              onContextMenu={(e) => handleFileContextMenu(file, e)}
                              className={`group relative flex flex-col items-center rounded-lg px-2 py-2.5 transition cursor-pointer ${
                                isSelected
                                  ? "bg-(--accent)/12 ring-1 ring-(--accent)/40"
                                  : "hover:bg-background"
                              }`}
                            >
                              {/* Owner icon top-left, always visible */}
                              <div className="absolute left-1.5 top-1.5">
                                {file.owner === "user" ? (
                                  <User className="h-2.5 w-2.5 text-sky-400/70" />
                                ) : (
                                  <Bot className="h-2.5 w-2.5 text-purple-400/70" />
                                )}
                              </div>

                              {/* File icon */}
                              <div className={`flex items-center justify-center ${preset.iconBox}`}>
                                {getFileCategory(file.name) === "image" ? (
                                  /* eslint-disable-next-line @next/next/no-img-element */
                                  <img src={getDownloadUrl(file)} alt={file.name} className={`rounded-md object-cover ${preset.thumb}`} loading="lazy" />
                                ) : (
                                  <FileIconDisplay file={file} size={preset.iconSize} />
                                )}
                              </div>

                              {/* Name + size */}
                              <p
                                className={`mt-1 w-full text-center text-[10.5px] font-medium leading-tight line-clamp-2 break-words transition-colors ${
                                  isSelected ? "text-(--accent)" : "text-foreground group-hover:text-(--accent)"
                                }`}
                                title={file.name}
                              >
                                {renderBreakableName(file.name)}
                              </p>
                              <p className="text-[9px] text-(--muted)/70 tabular-nums">
                                {formatFileSize(file.size_bytes)}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      /* List view */
                      <div className="overflow-hidden rounded-xl border border-(--border)">
                        <table className="w-full text-left text-xs">
                          <thead className="border-b border-(--border) bg-background/50">
                            <tr className="text-(--muted)">
                              <th className="px-3 py-2.5 font-semibold">Name</th>
                              <th className="px-3 py-2.5 font-semibold">Source</th>
                              <th className="px-3 py-2.5 font-semibold">Size</th>
                              <th className="px-3 py-2.5 font-semibold">Modified</th>
                              <th className="px-3 py-2.5 text-right font-semibold">Actions</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-(--border)/40">
                            {currentViewData.files.map((file, index) => {
                              const isSelected = selected.has(file.path);
                              return (
                                <tr
                                  key={file.path}
                                  onClick={(e) => handleFileClick(file, index, e)}
                                  onDoubleClick={() => void openInspector(file)}
                                  onContextMenu={(e) => handleFileContextMenu(file, e)}
                                  className={`cursor-pointer transition ${
                                    isSelected ? "bg-(--accent)/12" : "hover:bg-(--card)/40"
                                  }`}
                                >
                                  <td className="px-3 py-2.5">
                                    <div className="flex items-center gap-2.5">
                                      <FileIconDisplay file={file} size="sm" />
                                      <span className={`truncate font-medium max-w-xs sm:max-w-md ${isSelected ? "text-(--accent)" : "text-foreground"}`}>{file.name}</span>
                                    </div>
                                  </td>
                                  <td className="px-3 py-2.5"><SourcePill owner={file.owner} /></td>
                                  <td className="px-3 py-2.5 text-(--muted) tabular-nums">{formatFileSize(file.size_bytes)}</td>
                                  <td className="px-3 py-2.5 text-(--muted)">{formatDate(file.modified_at)}</td>
                                  <td className="px-3 py-2.5">
                                    <div className="flex items-center justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
                                      <button type="button" onClick={() => void openInspector(file)} className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) hover:bg-background hover:text-foreground cursor-pointer" title="Inspect">
                                        <Eye className="h-3.5 w-3.5 shrink-0" />
                                      </button>
                                      <a href={getDownloadUrl(file)} download={file.name} className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) hover:bg-background hover:text-foreground cursor-pointer" title="Download">
                                        <Download className="h-3.5 w-3.5 shrink-0" />
                                      </a>
                                      <button type="button" onClick={() => confirmDelete(file)} className="flex h-7 w-7 items-center justify-center rounded-md text-(--muted) hover:bg-rose-500/10 hover:text-rose-400 cursor-pointer" title="Delete">
                                        <Trash2 className="h-3.5 w-3.5 shrink-0" />
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>
                )}
              </div>
            )}
          </div>

          {/* Status bar — Nautilus/Dolphin style: item count normally,
              selection count + size + a quick delete action once
              something's selected. */}
          <div className="flex h-8 shrink-0 items-center justify-between border-t border-(--border) px-4 text-[11px] text-(--muted)">
            {selected.size > 0 ? (
              <>
                <span className="font-medium text-foreground">
                  {selected.size} of {currentViewData.files.length} selected · {formatFileSize(selectedFiles.reduce((a, f) => a + f.size_bytes, 0))}
                </span>
                <div className="flex items-center gap-3">
                  <button type="button" onClick={() => setSelected(new Set())} className="hover:text-foreground transition cursor-pointer">
                    Clear
                  </button>
                  <button
                    type="button"
                    onClick={() => (selected.size === 1 ? confirmDelete(selectedFiles[0]) : setBulkDeleteConfirm(true))}
                    className="flex items-center gap-1 text-rose-400/80 hover:text-rose-400 transition cursor-pointer"
                  >
                    <Trash2 className="h-3 w-3 shrink-0" />
                    Delete
                  </button>
                </div>
              </>
            ) : (
              <span>
                {currentViewData.folders.length > 0 && `${currentViewData.folders.length} ${currentViewData.folders.length === 1 ? "folder" : "folders"}`}
                {currentViewData.folders.length > 0 && currentViewData.files.length > 0 && " · "}
                {currentViewData.files.length > 0 && `${currentViewData.files.length} ${currentViewData.files.length === 1 ? "file" : "files"}`}
                {currentViewData.folders.length === 0 && currentViewData.files.length === 0 && "Empty"}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Right-click context menu */}
      {contextMenu && (
        <FileContextMenu
          file={contextMenu.file}
          x={contextMenu.x}
          y={contextMenu.y}
          onInspect={() => void openInspector(contextMenu.file)}
          onDelete={() => confirmDelete(contextMenu.file)}
          downloadUrl={getDownloadUrl(contextMenu.file)}
          onClose={() => setContextMenu(null)}
        />
      )}

      {/* Inspector Modal */}
      {inspectedFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-(--border) shadow-2xl" style={{ background: "var(--card)" }}>
            <div className="flex items-center justify-between border-b border-(--border) px-5 py-3.5">
              <div className="flex items-center gap-3 min-w-0">
                <FileIconDisplay file={inspectedFile} size="lg" />
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-foreground">{inspectedFile.name}</h3>
                  <div className="flex items-center gap-2 text-[11px] text-(--muted)">
                    <span>{formatFileSize(inspectedFile.size_bytes)}</span>
                    <span>•</span>
                    <span>{formatFullDate(inspectedFile.modified_at)}</span>
                    <span>•</span>
                    <SourcePill owner={inspectedFile.owner} />
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <a href={getDownloadUrl(inspectedFile)} download={inspectedFile.name} aria-label="Download" title="Download" className="flex h-8 w-8 items-center justify-center rounded-lg text-(--muted) hover:bg-background hover:text-foreground transition cursor-pointer">
                  <Download className="h-4 w-4 shrink-0" />
                </a>
                <button type="button" onClick={() => setInspectedFile(null)} className="flex h-8 w-8 items-center justify-center rounded-lg text-(--muted) hover:bg-background hover:text-foreground transition cursor-pointer">
                  <X className="h-4 w-4 shrink-0" />
                </button>
              </div>
            </div>

            {/* Real preview, not a "binary file, download it" dead end —
                reuses the same viewer the chat/code-interpreter artifact
                panel already uses: native PDF/image/HTML, Monaco for
                code/text, and the BetterOffice WASM editor (read-only
                here) for docx/xlsx/pptx. Always editMode={false}: this is
                a browse-and-inspect surface, editing happens from the
                actual conversation. */}
            <div className="mx-5 mb-5 mt-5 min-h-0 flex-1 overflow-hidden rounded-xl border border-(--border) bg-background/40">
              <FileArtifactViewer
                fileUrl={getDownloadUrl(inspectedFile)}
                fileName={inspectedFile.name}
                editMode={false}
              />
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deletingFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-(--border) p-5 shadow-2xl" style={{ background: "var(--card)" }}>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-500/10 text-rose-400 shrink-0">
                <Trash2 className="h-5 w-5 shrink-0" />
              </div>
              <h3 className="text-sm font-semibold text-foreground">Delete File?</h3>
            </div>
            <p className="mt-3 text-sm text-(--muted) leading-relaxed">
              Are you sure you want to permanently delete{" "}
              <strong className="text-foreground">{deletingFile.name}</strong>? This cannot be undone.
            </p>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" disabled={isDeleting} onClick={() => setDeletingFile(null)} className="h-8 rounded-lg border border-(--border) bg-background px-4 text-xs font-medium text-foreground transition hover:bg-(--card) disabled:opacity-50 cursor-pointer">
                Cancel
              </button>
              <button type="button" disabled={isDeleting} onClick={() => void executeDelete()} className="flex h-8 items-center gap-1.5 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50 cursor-pointer">
                {isDeleting && <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Delete Confirmation Modal */}
      {bulkDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-(--border) p-5 shadow-2xl" style={{ background: "var(--card)" }}>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-500/10 text-rose-400 shrink-0">
                <Trash2 className="h-5 w-5 shrink-0" />
              </div>
              <h3 className="text-sm font-semibold text-foreground">Delete {selectedFiles.length} Files?</h3>
            </div>
            <p className="mt-3 text-sm text-(--muted) leading-relaxed">
              Are you sure you want to permanently delete these {selectedFiles.length} files
              ({formatFileSize(selectedFiles.reduce((a, f) => a + f.size_bytes, 0))})? This cannot be undone.
            </p>
            <div className="mt-3 max-h-32 overflow-y-auto rounded-lg bg-background/60 p-2">
              {selectedFiles.map((f) => (
                <p key={f.path} className="truncate px-1 py-0.5 text-xs text-(--muted)">{f.name}</p>
              ))}
            </div>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" disabled={isBulkDeleting} onClick={() => setBulkDeleteConfirm(false)} className="h-8 rounded-lg border border-(--border) bg-background px-4 text-xs font-medium text-foreground transition hover:bg-(--card) disabled:opacity-50 cursor-pointer">
                Cancel
              </button>
              <button type="button" disabled={isBulkDeleting} onClick={() => void executeBulkDelete()} className="flex h-8 items-center gap-1.5 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50 cursor-pointer">
                {isBulkDeleting && <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />}
                Delete {selectedFiles.length}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

