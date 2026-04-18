import {
  ImageIcon,
  FileText,
  Music2,
  Film,
  FileIcon,
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
