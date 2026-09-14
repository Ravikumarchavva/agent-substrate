import {
  ImageIcon,
  FileText,
  FileSpreadsheet,
  Music2,
  Film,
  FileIcon,
  Presentation,
  FileCode,
  FileJson,
  FileArchive,
  type LucideIcon,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Attachment classification
// ---------------------------------------------------------------------------

export type AttachmentKind = "image" | "pdf" | "audio" | "video" | "document";

const IMAGE_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff", "avif",
]);
const PDF_EXTENSIONS = new Set(["pdf"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "m4a", "ogg", "aac", "flac"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "avi", "mkv", "webm"]);

export function getFileExtension(name: string): string {
  const parts = name.split(".");
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : "";
}

export function getAttachmentKind(
  mime: string | undefined,
  name: string,
): AttachmentKind {
  const normalizedMime = mime?.toLowerCase() ?? "";
  const extension = getFileExtension(name);

  if (normalizedMime.startsWith("image/") || IMAGE_EXTENSIONS.has(extension)) return "image";
  if (normalizedMime === "application/pdf" || PDF_EXTENSIONS.has(extension)) return "pdf";
  if (normalizedMime.startsWith("audio/") || AUDIO_EXTENSIONS.has(extension)) return "audio";
  if (normalizedMime.startsWith("video/") || VIDEO_EXTENSIONS.has(extension)) return "video";
  return "document";
}

export function getAttachmentIcon(kind: AttachmentKind): LucideIcon {
  switch (kind) {
    case "image":  return ImageIcon;
    case "pdf":    return FileText;
    case "audio":  return Music2;
    case "video":  return Film;
    default:       return FileIcon;
  }
}

export interface DocumentBadge {
  Icon: LucideIcon;
  label: string;
  badgeClass: string;
}

const CODE_EXTENSIONS = new Set([
  "js", "jsx", "ts", "tsx", "py", "java", "c", "cpp", "cs", "go", "rs",
  "rb", "php", "sh", "sql", "yaml", "yml", "xml",
]);
const ARCHIVE_EXTENSIONS = new Set(["zip", "tar", "gz", "rar", "7z"]);

// Per-extension icon/color for a document-kind attachment — matches
// office-suite convention (PowerPoint orange, Excel green, Word blue, PDF
// red) so the file type reads at a glance, the same way Claude's own
// artifact cards do. Originally lived only on the assistant-generated
// `sandbox:` file card (MessageBubble.tsx) and only covered office types; a
// user-uploaded file got the same generic FileIcon every other kind did
// (getAttachmentIcon below) regardless of type — PDF included, despite PDF
// having its own AttachmentKind. Shared by both card types and by every
// non-image/audio/video kind, not just "document".
export function getDocumentBadge(name: string): DocumentBadge {
  const extension = getFileExtension(name);
  if (extension === "pptx" || extension === "ppt") {
    return { Icon: Presentation, label: "Presentation", badgeClass: "bg-orange-500/15 text-orange-500" };
  }
  if (extension === "xlsx" || extension === "xls" || extension === "csv") {
    return { Icon: FileSpreadsheet, label: "Spreadsheet", badgeClass: "bg-emerald-500/15 text-emerald-500" };
  }
  if (extension === "docx" || extension === "doc") {
    return { Icon: FileText, label: "Document", badgeClass: "bg-blue-500/15 text-blue-500" };
  }
  if (extension === "pdf") {
    return { Icon: FileText, label: "PDF", badgeClass: "bg-red-500/15 text-red-500" };
  }
  if (extension === "md" || extension === "markdown") {
    return { Icon: FileText, label: "Document", badgeClass: "bg-teal-500/15 text-teal-500" };
  }
  if (extension === "html" || extension === "htm") {
    return { Icon: FileCode, label: "Web page", badgeClass: "bg-amber-500/15 text-amber-500" };
  }
  if (extension === "json") {
    return { Icon: FileJson, label: "Data", badgeClass: "bg-violet-500/15 text-violet-500" };
  }
  if (CODE_EXTENSIONS.has(extension)) {
    return { Icon: FileCode, label: "Code", badgeClass: "bg-indigo-500/15 text-indigo-500" };
  }
  if (ARCHIVE_EXTENSIONS.has(extension)) {
    return { Icon: FileArchive, label: "Archive", badgeClass: "bg-stone-500/15 text-stone-500" };
  }
  if (extension === "txt") {
    return { Icon: FileText, label: "Text", badgeClass: "bg-slate-500/15 text-slate-500" };
  }
  return { Icon: FileIcon, label: "File", badgeClass: "bg-(--muted)/15 text-(--muted)" };
}

export interface FileGlyph {
  Icon: LucideIcon;
  label: string;
  /** Solid Tailwind bg color (not the soft /15 tint getDocumentBadge uses)
   *  — the flat colored-square glyph FileTypeIcon renders everywhere. */
  bg: string;
}

// One color/icon per file type, shared by every icon in the app (see
// FileTypeIcon.tsx) — a superset of getDocumentBadge's mapping (same colors,
// so nothing looks disconnected from the existing badges) plus image/audio/
// video, which getDocumentBadge never had to cover.
export function getFileGlyph(name: string, mime?: string): FileGlyph {
  const kind = getAttachmentKind(mime, name);
  if (kind === "image") return { Icon: ImageIcon, label: "Image", bg: "bg-sky-500" };
  if (kind === "audio") return { Icon: Music2, label: "Audio", bg: "bg-pink-500" };
  if (kind === "video") return { Icon: Film, label: "Video", bg: "bg-purple-500" };

  const extension = getFileExtension(name);
  if (extension === "pptx" || extension === "ppt") {
    return { Icon: Presentation, label: "Presentation", bg: "bg-orange-500" };
  }
  if (extension === "xlsx" || extension === "xls" || extension === "csv") {
    return { Icon: FileSpreadsheet, label: "Spreadsheet", bg: "bg-emerald-500" };
  }
  if (extension === "docx" || extension === "doc") {
    return { Icon: FileText, label: "Document", bg: "bg-blue-500" };
  }
  if (extension === "pdf") {
    return { Icon: FileText, label: "PDF", bg: "bg-red-500" };
  }
  if (extension === "md" || extension === "markdown") {
    return { Icon: FileText, label: "Document", bg: "bg-teal-500" };
  }
  if (extension === "html" || extension === "htm") {
    return { Icon: FileCode, label: "Web page", bg: "bg-amber-500" };
  }
  if (extension === "json") {
    return { Icon: FileJson, label: "Data", bg: "bg-violet-500" };
  }
  if (CODE_EXTENSIONS.has(extension)) {
    return { Icon: FileCode, label: "Code", bg: "bg-indigo-500" };
  }
  if (ARCHIVE_EXTENSIONS.has(extension)) {
    return { Icon: FileArchive, label: "Archive", bg: "bg-stone-500" };
  }
  if (extension === "txt") {
    return { Icon: FileText, label: "Text", bg: "bg-slate-500" };
  }
  return { Icon: FileIcon, label: "File", bg: "bg-neutral-500" };
}

export function formatFileSize(size: number | undefined): string {
  if (!size) return "";

  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  const digits = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}
